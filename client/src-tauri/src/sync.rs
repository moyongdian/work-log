//! 云端同步模块
//!
//! 流程：连通性检测 → 推送本地 change_log → 拉取云端增量 → 本地合并 → 清理已同步变更。
//! 任一步失败都不会破坏本地数据：变更记录保留，下次同步自动重试。

use crate::config::Config;
use crate::db;
use crate::models::*;
use crate::AppState;
use rusqlite::{params, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tauri::State;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChangeItem {
    pub data_type: String,
    pub id: String,
    pub operate: String,
    pub update_time: i64,
    pub payload: Option<serde_json::Value>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PushRequest {
    changes: Vec<ChangeItem>,
    force: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteConflict {
    pub data_type: String,
    pub id: String,
    pub remote_update_time: i64,
    pub remote: serde_json::Value,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PushResponse {
    #[serde(default)]
    pub applied: i64,
    #[serde(default)]
    pub conflicts: Vec<RemoteConflict>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PullResponse {
    #[serde(default)]
    pub server_time: i64,
    #[serde(default)]
    pub trees: Vec<Tree>,
    #[serde(default)]
    pub logs: Vec<WorkLog>,
}

fn err_str<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(err_str)
}

fn url(base: &str, path: &str) -> String {
    format!("{}{}", base.trim_end_matches('/'), path)
}

/* ============================== 收集本地变更 ============================== */

fn collect_changes(conn: &rusqlite::Connection) -> Result<Vec<ChangeItem>, String> {
    let mut stmt = conn
        .prepare("SELECT data_type, data_id, operate FROM change_log ORDER BY operate_time ASC")
        .map_err(err_str)?;
    let rows: Vec<(String, String, String)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .map_err(err_str)?
        .filter_map(Result::ok)
        .collect();
    drop(stmt);

    let mut items = Vec::with_capacity(rows.len());
    for (data_type, id, operate) in rows {
        let payload = if data_type == "tree" {
            let t: Option<Tree> = conn
                .query_row("SELECT * FROM tree WHERE id = ?1", params![id], |r| Tree::from_row(r))
                .optional()
                .map_err(err_str)?;
            match t {
                Some(v) => serde_json::to_value(&v).ok(),
                None => None,
            }
        } else {
            let l: Option<WorkLog> = conn
                .query_row("SELECT * FROM work_log WHERE id = ?1", params![id], |r| {
                    WorkLog::from_row(r)
                })
                .optional()
                .map_err(err_str)?;
            match l {
                Some(v) => serde_json::to_value(&v).ok(),
                None => None,
            }
        };

        let update_time = payload
            .as_ref()
            .and_then(|p| p.get("updateTime"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        if payload.is_none() {
            continue; // 数据已不存在，跳过该变更
        }
        items.push(ChangeItem {
            data_type,
            id,
            operate,
            update_time,
            payload,
        });
    }
    Ok(items)
}

fn local_label(conn: &rusqlite::Connection, data_type: &str, id: &str) -> (Option<String>, Option<String>, i64) {
    if data_type == "tree" {
        if let Ok(t) = conn.query_row("SELECT * FROM tree WHERE id = ?1", params![id], |r| Tree::from_row(r)) {
            return (Some(t.name), None, t.update_time);
        }
    } else if let Ok(l) = conn.query_row("SELECT * FROM work_log WHERE id = ?1", params![id], |r| {
        WorkLog::from_row(r)
    }) {
        return (None, Some(l.title), l.update_time);
    }
    (None, None, 0)
}

/* ============================== 同步主流程 ============================== */

#[tauri::command]
pub async fn sync_now(state: State<'_, AppState>) -> Result<SyncReport, String> {
    let cfg: Config = state.cfg.lock().map_err(err_str)?.clone();
    if cfg.server_url.trim().is_empty() {
        return Err("未配置云服务器地址，请先在设置中填写".into());
    }

    let http = client()?;
    let token = cfg.token.clone();

    // 1) 连通性检测（避免网络误判导致半途失败）
    let ping = http
        .get(url(&cfg.server_url, "/api/ping"))
        .header("X-Api-Token", &token)
        .timeout(Duration::from_secs(6))
        .send()
        .await
        .map_err(|e| format!("无法连接云服务器：{}", e))?;
    if ping.status() == reqwest::StatusCode::UNAUTHORIZED {
        return Err("Token 校验失败，请检查 API Token".into());
    }
    if !ping.status().is_success() {
        return Err(format!("云服务器异常：HTTP {}", ping.status()));
    }

    // 2) 收集待推送变更
    let changes = {
        let conn = state.db.lock().map_err(err_str)?;
        collect_changes(&conn)?
    };

    let mut pushed = 0i64;
    let mut conflicts: Vec<ConflictItem> = Vec::new();

    // 3) 推送
    if !changes.is_empty() {
        let resp = http
            .post(url(&cfg.server_url, "/api/sync/push"))
            .header("X-Api-Token", &token)
            .json(&PushRequest {
                changes: changes.clone(),
                force: false,
            })
            .send()
            .await
            .map_err(|e| format!("推送失败：{}", e))?;

        if resp.status() == reqwest::StatusCode::UNAUTHORIZED {
            return Err("Token 校验失败，请检查 API Token".into());
        }
        if !resp.status().is_success() {
            return Err(format!("推送失败：HTTP {}", resp.status()));
        }
        let pr: PushResponse = resp.json().await.map_err(|e| format!("推送响应解析失败：{}", e))?;
        pushed = pr.applied;

        // 冲突项：本地保留待用户决策，不清理 change_log
        let conflict_keys: Vec<(String, String)> = pr
            .conflicts
            .iter()
            .map(|c| (c.data_type.clone(), c.id.clone()))
            .collect();

        {
            let conn = state.db.lock().map_err(err_str)?;
            for ch in &changes {
                if conflict_keys
                    .iter()
                    .any(|(dt, id)| dt == &ch.data_type && id == &ch.id)
                {
                    continue;
                }
                db::mark_synced(&conn, &ch.data_type, &ch.id).map_err(err_str)?;
            }
            for c in &pr.conflicts {
                let (local_name, local_title, local_time) = local_label(&conn, &c.data_type, &c.id);
                let remote_name = c
                    .remote
                    .get("name")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let remote_title = c
                    .remote
                    .get("title")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                conflicts.push(ConflictItem {
                    data_type: c.data_type.clone(),
                    id: c.id.clone(),
                    local_update_time: local_time,
                    remote_update_time: c.remote_update_time,
                    local_name,
                    remote_name,
                    local_title,
                    remote_title,
                    remote: c.remote.clone(),
                });
            }
        }
    }

    // 4) 拉取云端增量
    let since = cfg.last_sync_at;
    let pulled = match http
        .get(url(&cfg.server_url, "/api/sync/pull"))
        .header("X-Api-Token", &token)
        .query(&[("since", since.to_string())])
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => {
            let pr: PullResponse = resp.json().await.map_err(|e| format!("拉取响应解析失败：{}", e))?;
            let applied = {
                let conn = state.db.lock().map_err(err_str)?;
                crate::commands::apply_remote(&conn, &pr.trees, &pr.logs, pr.server_time, false)?
            };
            // 记录同步时间点，用于下次增量拉取
            {
                let mut cfg_guard = state.cfg.lock().map_err(err_str)?;
                cfg_guard.last_sync_at = pr.server_time;
                let _ = cfg_guard.save(&state.paths.config_file);
            }
            applied
        }
        Ok(resp) => return Err(format!("拉取失败：HTTP {}", resp.status())),
        // 推送已成功，拉取失败时不影响已同步数据
        Err(e) => return Err(format!("拉取失败（已推送 {} 条）：{}", pushed, e)),
    };

    // 5) 暂存冲突，等待用户选择
    {
        let mut guard = state.conflicts.lock().map_err(err_str)?;
        *guard = conflicts.clone();
    }

    let message = if conflicts.is_empty() {
        format!("上传 {} 条，拉取 {} 条", pushed, pulled)
    } else {
        format!("上传 {} 条，拉取 {} 条，{} 条冲突待处理", pushed, pulled, conflicts.len())
    };

    Ok(SyncReport {
        pushed,
        pulled,
        failed: 0,
        conflicts,
        message,
    })
}

/* ============================== 冲突处理 ============================== */

#[tauri::command]
pub async fn resolve_conflicts(
    state: State<'_, AppState>,
    choices: Vec<ConflictChoice>,
) -> Result<SyncReport, String> {
    let cfg: Config = state.cfg.lock().map_err(err_str)?.clone();
    if cfg.server_url.trim().is_empty() {
        return Err("未配置云服务器地址".into());
    }
    let pending = state.conflicts.lock().map_err(err_str)?.clone();
    if pending.is_empty() {
        return Ok(SyncReport::empty("没有待处理的冲突"));
    }

    let http = client()?;
    let mut keep_local_items: Vec<ChangeItem> = Vec::new();
    let mut pull_trees: Vec<Tree> = Vec::new();
    let mut pull_logs: Vec<WorkLog> = Vec::new();

    for c in &pending {
        let keep_local = choices
            .iter()
            .find(|ch| ch.data_type == c.data_type && ch.id == c.id)
            .map(|ch| ch.keep_local)
            .unwrap_or(true);

        if keep_local {
            // 保留本地：用本地数据强制覆盖云端
            let item = {
                let conn = state.db.lock().map_err(err_str)?;
                if c.data_type == "tree" {
                    conn.query_row("SELECT * FROM tree WHERE id = ?1", params![c.id], |r| Tree::from_row(r))
                        .optional()
                        .map_err(err_str)?
                        .and_then(|t| serde_json::to_value(&t).ok())
                        .map(|v| ChangeItem {
                            data_type: "tree".into(),
                            id: c.id.clone(),
                            operate: "update".into(),
                            update_time: c.local_update_time,
                            payload: Some(v),
                        })
                } else {
                    conn.query_row("SELECT * FROM work_log WHERE id = ?1", params![c.id], |r| {
                        WorkLog::from_row(r)
                    })
                    .optional()
                    .map_err(err_str)?
                    .and_then(|l| serde_json::to_value(&l).ok())
                    .map(|v| ChangeItem {
                        data_type: "log".into(),
                        id: c.id.clone(),
                        operate: "update".into(),
                        update_time: c.local_update_time,
                        payload: Some(v),
                    })
                }
            };
            if let Some(i) = item {
                keep_local_items.push(i);
            }
        } else {
            // 采用云端：把远端数据写回本地
            if c.data_type == "tree" {
                if let Ok(t) = serde_json::from_value::<Tree>(c.remote.clone()) {
                    pull_trees.push(t);
                }
            } else if let Ok(l) = serde_json::from_value::<WorkLog>(c.remote.clone()) {
                pull_logs.push(l);
            }
        }
    }

    let mut pushed = 0i64;
    if !keep_local_items.is_empty() {
        let resp = http
            .post(url(&cfg.server_url, "/api/sync/push"))
            .header("X-Api-Token", &cfg.token)
            .json(&PushRequest {
                changes: keep_local_items.clone(),
                force: true,
            })
            .send()
            .await
            .map_err(|e| format!("覆盖云端失败：{}", e))?;
        if !resp.status().is_success() {
            return Err(format!("覆盖云端失败：HTTP {}", resp.status()));
        }
        let pr: PushResponse = resp.json().await.map_err(err_str)?;
        pushed = pr.applied;
        let conn = state.db.lock().map_err(err_str)?;
        for item in &keep_local_items {
            db::mark_synced(&conn, &item.data_type, &item.id).map_err(err_str)?;
        }
    }

    let mut pulled = 0i64;
    if !pull_trees.is_empty() || !pull_logs.is_empty() {
        let conn = state.db.lock().map_err(err_str)?;
        pulled = crate::commands::apply_remote(&conn, &pull_trees, &pull_logs, 0, true)?;
    }

    state.conflicts.lock().map_err(err_str)?.clear();

    Ok(SyncReport {
        pushed,
        pulled,
        failed: 0,
        conflicts: Vec::new(),
        message: format!("冲突已处理：覆盖云端 {} 条，采用云端 {} 条", pushed, pulled),
    })
}
