//! Tauri 命令：目录分支、日志、配置、网络状态、备份
//!
//! 所有写操作都是「先写本地库、再记录变更」，全程不依赖网络。

use crate::config::Config;
use crate::db;
use crate::models::*;
use crate::AppState;
use rusqlite::{params, Connection, OptionalExtension};
use tauri::State;

const ARCHIVE_NAME: &str = "未分类（归档）";

fn conn_err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

/* ============================== 目录分支 ============================== */

#[tauri::command]
pub fn list_trees(state: State<'_, AppState>) -> Result<Vec<Tree>, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    let mut stmt = conn
        .prepare("SELECT * FROM tree WHERE is_deleted = 0 ORDER BY sort ASC, create_time ASC")
        .map_err(conn_err)?;
    let rows = stmt
        .query_map([], |r| Tree::from_row(r))
        .map_err(conn_err)?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

#[tauri::command]
pub fn create_tree(state: State<'_, AppState>, parent_id: Option<String>, name: String) -> Result<Tree, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("分支名称不能为空".into());
    }
    let conn = state.db.lock().map_err(conn_err)?;

    // 父节点必须存在（顶级分支 parent_id 为 null）
    if let Some(pid) = &parent_id {
        let exists: Option<String> = conn
            .query_row(
                "SELECT id FROM tree WHERE id = ?1 AND is_deleted = 0",
                params![pid],
                |r| r.get(0),
            )
            .optional()
            .map_err(conn_err)?;
        if exists.is_none() {
            return Err("父分支不存在".into());
        }
    }

    let now = now_ms();
    let id = uuid::Uuid::new_v4().to_string();
    let sort: i64 = conn
        .query_row(
            "SELECT COALESCE(MAX(sort), 0) + 10 FROM tree
             WHERE is_deleted = 0 AND ((parent_id IS NULL AND ?1 IS NULL) OR parent_id = ?1)",
            params![parent_id],
            |r| r.get(0),
        )
        .map_err(conn_err)?;

    conn.execute(
        "INSERT INTO tree (id, parent_id, name, sort, create_time, update_time, is_deleted, sync_status)
         VALUES (?1, ?2, ?3, ?4, ?5, ?5, 0, 'unsynced')",
        params![id, parent_id, name, sort, now],
    )
    .map_err(conn_err)?;
    db::record_change(&conn, "tree", &id, "add").map_err(conn_err)?;

    Ok(Tree {
        id,
        parent_id,
        name,
        sort,
        create_time: now,
        update_time: now,
        is_deleted: 0,
        sync_status: "unsynced".into(),
    })
}

#[tauri::command]
pub fn rename_tree(state: State<'_, AppState>, id: String, name: String) -> Result<Tree, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("分支名称不能为空".into());
    }
    let conn = state.db.lock().map_err(conn_err)?;
    let now = now_ms();
    let n = conn
        .execute(
            "UPDATE tree SET name = ?1, update_time = ?2, sync_status = 'unsynced'
             WHERE id = ?3 AND is_deleted = 0",
            params![name, now, id],
        )
        .map_err(conn_err)?;
    if n == 0 {
        return Err("分支不存在".into());
    }
    db::record_change(&conn, "tree", &id, "update").map_err(conn_err)?;
    conn.query_row("SELECT * FROM tree WHERE id = ?1", params![id], |r| Tree::from_row(r))
        .map_err(conn_err)
}

/// 收集某分支及其所有子孙分支的 ID
fn collect_subtree(tx: &rusqlite::Transaction<'_>, root: &str) -> rusqlite::Result<Vec<String>> {
    let mut ids = vec![root.to_string()];
    let mut i = 0;
    while i < ids.len() {
        let cur = ids[i].clone();
        let mut stmt = tx.prepare("SELECT id FROM tree WHERE parent_id = ?1 AND is_deleted = 0")?;
        let children: Vec<String> = stmt
            .query_map(params![cur], |r| r.get::<_, String>(0))?
            .filter_map(Result::ok)
            .collect();
        drop(stmt);
        for c in children {
            if !ids.contains(&c) {
                ids.push(c);
            }
        }
        i += 1;
    }
    Ok(ids)
}

/// 确保存在「未分类（归档）」分支，用于承接被删除分支下未删除的日志
fn ensure_archive_tree(tx: &rusqlite::Transaction<'_>, now: i64) -> rusqlite::Result<String> {
    let found: Option<String> = tx
        .query_row(
            "SELECT id FROM tree WHERE name = ?1 AND parent_id IS NULL AND is_deleted = 0",
            params![ARCHIVE_NAME],
            |r| r.get(0),
        )
        .optional()?;
    if let Some(id) = found {
        return Ok(id);
    }
    let id = uuid::Uuid::new_v4().to_string();
    let sort: i64 = tx.query_row(
        "SELECT COALESCE(MAX(sort), 0) + 10 FROM tree WHERE parent_id IS NULL AND is_deleted = 0",
        [],
        |r| r.get(0),
    )?;
    tx.execute(
        "INSERT INTO tree (id, parent_id, name, sort, create_time, update_time, is_deleted, sync_status)
         VALUES (?1, NULL, ?2, ?3, ?4, ?4, 0, 'unsynced')",
        params![id, ARCHIVE_NAME, sort, now],
    )?;
    db::record_change(tx, "tree", &id, "add")?;
    Ok(id)
}

#[tauri::command]
pub fn delete_tree(state: State<'_, AppState>, id: String, delete_logs: bool) -> Result<(), String> {
    let mut conn = state.db.lock().map_err(conn_err)?;
    let tx = conn.transaction().map_err(conn_err)?;
    let now = now_ms();

    let parent_id: Option<String> = tx
        .query_row("SELECT parent_id FROM tree WHERE id = ?1", params![id], |r| r.get(0))
        .optional()
        .map_err(conn_err)?
        .ok_or_else(|| "分支不存在".to_string())?;

    let ids = collect_subtree(&tx, &id).map_err(conn_err)?;

    // 目标归档分支（仅在「不删除日志」时需要）
    let dest = if delete_logs {
        None
    } else {
        Some(match &parent_id {
            Some(p) => p.clone(),
            None => ensure_archive_tree(&tx, now).map_err(conn_err)?,
        })
    };

    for tid in &ids {
        let mut stmt = tx
            .prepare("SELECT id FROM work_log WHERE tree_id = ?1 AND is_deleted = 0")
            .map_err(conn_err)?;
        let log_ids: Vec<String> = stmt
            .query_map(params![tid], |r| r.get::<_, String>(0))
            .map_err(conn_err)?
            .filter_map(Result::ok)
            .collect();
        drop(stmt);

        for lid in log_ids {
            match &dest {
                // 保留日志：移动到上一级分支
                Some(d) => {
                    tx.execute(
                        "UPDATE work_log SET tree_id = ?1, update_time = ?2, sync_status = 'unsynced' WHERE id = ?3",
                        params![d, now, lid],
                    )
                    .map_err(conn_err)?;
                    db::record_change(&tx, "log", &lid, "update").map_err(conn_err)?;
                }
                // 一并删除日志
                None => {
                    tx.execute(
                        "UPDATE work_log SET is_deleted = 1, update_time = ?1, sync_status = 'unsynced' WHERE id = ?2",
                        params![now, lid],
                    )
                    .map_err(conn_err)?;
                    db::record_change(&tx, "log", &lid, "delete").map_err(conn_err)?;
                }
            }
        }
    }

    for tid in &ids {
        tx.execute(
            "UPDATE tree SET is_deleted = 1, update_time = ?1, sync_status = 'unsynced' WHERE id = ?2",
            params![now, tid],
        )
        .map_err(conn_err)?;
        db::record_change(&tx, "tree", tid, "delete").map_err(conn_err)?;
    }

    tx.commit().map_err(conn_err)?;
    Ok(())
}

#[tauri::command]
pub fn reorder_trees(state: State<'_, AppState>, updates: Vec<TreeSort>) -> Result<(), String> {
    let mut conn = state.db.lock().map_err(conn_err)?;
    let tx = conn.transaction().map_err(conn_err)?;
    let now = now_ms();
    for u in &updates {
        tx.execute(
            "UPDATE tree SET parent_id = ?1, sort = ?2, update_time = ?3, sync_status = 'unsynced' WHERE id = ?4",
            params![u.parent_id, u.sort, now, u.id],
        )
        .map_err(conn_err)?;
        db::record_change(&tx, "tree", &u.id, "update").map_err(conn_err)?;
    }
    tx.commit().map_err(conn_err)?;
    Ok(())
}

/* ============================== 日志 ============================== */

#[tauri::command]
pub fn list_logs(state: State<'_, AppState>, tree_id: String) -> Result<Vec<LogSummary>, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    let mut stmt = conn
        .prepare(
            "SELECT id, tree_id, title, substr(content, 1, 240) AS preview,
                    create_time, update_time, sync_status
             FROM work_log
             WHERE tree_id = ?1 AND is_deleted = 0
             ORDER BY update_time DESC",
        )
        .map_err(conn_err)?;
    let rows = stmt
        .query_map(params![tree_id], |r| {
            Ok(LogSummary {
                id: r.get(0)?,
                tree_id: r.get(1)?,
                title: r.get(2)?,
                preview: r.get(3)?,
                create_time: r.get(4)?,
                update_time: r.get(5)?,
                sync_status: r.get(6)?,
            })
        })
        .map_err(conn_err)?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

#[tauri::command]
pub fn get_log(state: State<'_, AppState>, id: String) -> Result<WorkLog, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    conn.query_row("SELECT * FROM work_log WHERE id = ?1", params![id], |r| {
        WorkLog::from_row(r)
    })
    .map_err(conn_err)
}

#[tauri::command]
pub fn create_log(
    state: State<'_, AppState>,
    tree_id: String,
    title: String,
    content: String,
) -> Result<WorkLog, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    let now = now_ms();
    let id = uuid::Uuid::new_v4().to_string();
    let title = if title.trim().is_empty() {
        "未命名日志".to_string()
    } else {
        title.trim().to_string()
    };

    conn.execute(
        "INSERT INTO work_log (id, tree_id, title, content, create_time, update_time, is_deleted, sync_status)
         VALUES (?1, ?2, ?3, ?4, ?5, ?5, 0, 'unsynced')",
        params![id, tree_id, title, content, now],
    )
    .map_err(conn_err)?;
    db::record_change(&conn, "log", &id, "add").map_err(conn_err)?;

    Ok(WorkLog {
        id,
        tree_id: Some(tree_id),
        title,
        content,
        create_time: now,
        update_time: now,
        is_deleted: 0,
        sync_status: "unsynced".into(),
    })
}

#[tauri::command]
pub fn update_log(
    state: State<'_, AppState>,
    id: String,
    title: String,
    content: String,
) -> Result<WorkLog, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    let now = now_ms();
    let title = if title.trim().is_empty() {
        "未命名日志".to_string()
    } else {
        title.trim().to_string()
    };
    let n = conn
        .execute(
            "UPDATE work_log SET title = ?1, content = ?2, update_time = ?3, sync_status = 'unsynced'
             WHERE id = ?4 AND is_deleted = 0",
            params![title, content, now, id],
        )
        .map_err(conn_err)?;
    if n == 0 {
        return Err("日志不存在或已删除".into());
    }
    db::record_change(&conn, "log", &id, "update").map_err(conn_err)?;
    conn.query_row("SELECT * FROM work_log WHERE id = ?1", params![id], |r| {
        WorkLog::from_row(r)
    })
    .map_err(conn_err)
}

#[tauri::command]
pub fn delete_log(state: State<'_, AppState>, id: String) -> Result<(), String> {
    let conn = state.db.lock().map_err(conn_err)?;
    let now = now_ms();
    conn.execute(
        "UPDATE work_log SET is_deleted = 1, update_time = ?1, sync_status = 'unsynced' WHERE id = ?2",
        params![now, id],
    )
    .map_err(conn_err)?;
    db::record_change(&conn, "log", &id, "delete").map_err(conn_err)?;
    Ok(())
}

#[tauri::command]
pub fn search_logs(state: State<'_, AppState>, keyword: String) -> Result<Vec<LogSummary>, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    let like = format!("%{}%", keyword.trim());
    let mut stmt = conn
        .prepare(
            "SELECT id, tree_id, title, substr(content, 1, 240) AS preview,
                    create_time, update_time, sync_status
             FROM work_log
             WHERE is_deleted = 0 AND (title LIKE ?1 OR content LIKE ?1)
             ORDER BY update_time DESC LIMIT 200",
        )
        .map_err(conn_err)?;
    let rows = stmt
        .query_map(params![like], |r| {
            Ok(LogSummary {
                id: r.get(0)?,
                tree_id: r.get(1)?,
                title: r.get(2)?,
                preview: r.get(3)?,
                create_time: r.get(4)?,
                update_time: r.get(5)?,
                sync_status: r.get(6)?,
            })
        })
        .map_err(conn_err)?
        .filter_map(Result::ok)
        .collect();
    Ok(rows)
}

/* ============================== 配置 / 状态 ============================== */

#[tauri::command]
pub fn get_config(state: State<'_, AppState>) -> Result<Config, String> {
    Ok(state.cfg.lock().map_err(conn_err)?.clone())
}

#[tauri::command]
pub fn save_config(state: State<'_, AppState>, config: Config) -> Result<Config, String> {
    let mut cfg = state.cfg.lock().map_err(conn_err)?;
    // 保留 last_sync_at（由同步流程维护）
    let merged = Config {
        last_sync_at: cfg.last_sync_at,
        ..config
    };
    merged
        .save(&state.paths.config_file)
        .map_err(|e| format!("写入配置文件失败：{}", e))?;
    *cfg = merged.clone();
    Ok(merged)
}

#[tauri::command]
pub fn pending_changes(state: State<'_, AppState>) -> Result<i64, String> {
    let conn = state.db.lock().map_err(conn_err)?;
    db::pending_count(&conn).map_err(conn_err)
}

#[tauri::command]
pub fn app_info(state: State<'_, AppState>) -> Result<AppInfo, String> {
    Ok(AppInfo {
        db_path: state.paths.db_file.display().to_string(),
        backup_dir: state.paths.backup_dir.display().to_string(),
        desktop: true,
    })
}

#[tauri::command]
pub fn backup_db(state: State<'_, AppState>) -> Result<String, String> {
    crate::backup_now(&state).map_err(conn_err)
}

/* ============================== 网络 ============================== */

fn http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
        .map_err(conn_err)
}

fn api_url(base: &str, path: &str) -> String {
    format!("{}{}", base.trim_end_matches('/'), path)
}

#[tauri::command]
pub async fn net_status(state: State<'_, AppState>) -> Result<NetStatus, String> {
    let cfg = state.cfg.lock().map_err(conn_err)?.clone();
    if cfg.server_url.trim().is_empty() {
        return Ok(NetStatus {
            online: true,
            reachable: false,
        });
    }
    let client = http_client()?;
    let url = api_url(&cfg.server_url, "/api/ping");
    match client
        .get(url)
        .header("X-Api-Token", &cfg.token)
        .timeout(std::time::Duration::from_secs(4))
        .send()
        .await
    {
        Ok(resp) => Ok(NetStatus {
            online: true,
            reachable: resp.status().is_success(),
        }),
        Err(_) => Ok(NetStatus {
            online: true,
            reachable: false,
        }),
    }
}

#[tauri::command]
pub async fn test_connection(state: State<'_, AppState>) -> Result<String, String> {
    let cfg = state.cfg.lock().map_err(conn_err)?.clone();
    if cfg.server_url.trim().is_empty() {
        return Err("请先填写云服务器地址".into());
    }
    let client = http_client()?;
    let url = api_url(&cfg.server_url, "/api/ping");
    let resp = client
        .get(&url)
        .header("X-Api-Token", &cfg.token)
        .send()
        .await
        .map_err(|e| format!("无法连接服务器：{}", e))?;

    if resp.status() == reqwest::StatusCode::UNAUTHORIZED {
        return Err("Token 校验失败，请检查 API Token".into());
    }
    if !resp.status().is_success() {
        return Err(format!("服务器返回异常状态：{}", resp.status()));
    }
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("响应解析失败：{}", e))?;
    let ver = body
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    Ok(format!("连接成功，服务端版本 {}", ver))
}

/* ============================== 同步后本地合并 ============================== */

/// 拉取到的远端数据合并进本地库（本地未同步的修改优先保留）
pub fn apply_remote(
    conn: &Connection,
    trees: &[Tree],
    logs: &[WorkLog],
    server_time: i64,
    force: bool,
) -> Result<i64, String> {
    let mut applied = 0i64;
    for t in trees {
        let local: Option<Tree> = conn
            .query_row("SELECT * FROM tree WHERE id = ?1", params![t.id], |r| Tree::from_row(r))
            .optional()
            .map_err(conn_err)?;

        let skip = match &local {
            None => false,
            Some(l) => !force && db::has_pending(conn, "tree", &t.id) && l.update_time > t.update_time,
        };
        if skip {
            continue;
        }
        conn.execute(
            "INSERT INTO tree (id, parent_id, name, sort, create_time, update_time, is_deleted, sync_status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'synced')
             ON CONFLICT(id) DO UPDATE SET
                parent_id = excluded.parent_id,
                name = excluded.name,
                sort = excluded.sort,
                update_time = excluded.update_time,
                is_deleted = excluded.is_deleted,
                sync_status = 'synced'",
            params![t.id, t.parent_id, t.name, t.sort, t.create_time, t.update_time, t.is_deleted],
        )
        .map_err(conn_err)?;
        // 远端已确认，清掉本地变更记录
        conn.execute(
            "DELETE FROM change_log WHERE data_type = 'tree' AND data_id = ?1",
            params![t.id],
        )
        .map_err(conn_err)?;
        applied += 1;
    }

    for l in logs {
        let local: Option<WorkLog> = conn
            .query_row("SELECT * FROM work_log WHERE id = ?1", params![l.id], |r| {
                WorkLog::from_row(r)
            })
            .optional()
            .map_err(conn_err)?;
        let skip = match &local {
            None => false,
            Some(x) => !force && db::has_pending(conn, "log", &l.id) && x.update_time > l.update_time,
        };
        if skip {
            continue;
        }
        conn.execute(
            "INSERT INTO work_log (id, tree_id, title, content, create_time, update_time, is_deleted, sync_status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'synced')
             ON CONFLICT(id) DO UPDATE SET
                tree_id = excluded.tree_id,
                title = excluded.title,
                content = excluded.content,
                update_time = excluded.update_time,
                is_deleted = excluded.is_deleted,
                sync_status = 'synced'",
            params![l.id, l.tree_id, l.title, l.content, l.create_time, l.update_time, l.is_deleted],
        )
        .map_err(conn_err)?;
        conn.execute(
            "DELETE FROM change_log WHERE data_type = 'log' AND data_id = ?1",
            params![l.id],
        )
        .map_err(conn_err)?;
        applied += 1;
    }

    db::set_meta(conn, "last_pull_at", &server_time.to_string()).map_err(conn_err)?;
    Ok(applied)
}
