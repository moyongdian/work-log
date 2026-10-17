//! REST 接口实现

use crate::auth::require_token;
use crate::models::*;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::routing::{delete, get, post};
use axum::{middleware, Json, Router};
use serde_json::{json, Value};
use sqlx::MySqlPool;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub pool: MySqlPool,
    pub token: String,
}

type ApiResult<T> = Result<Json<T>, (StatusCode, String)>;

fn internal<E: std::fmt::Display>(e: E) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}

fn bad_request(msg: &str) -> (StatusCode, String) {
    (StatusCode::BAD_REQUEST, msg.to_string())
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/api/ping", get(ping))
        // 目录分支
        .route("/api/tree/list", get(tree_list))
        .route("/api/tree/save", post(tree_save))
        .route("/api/tree/delete", delete(tree_delete))
        // 日志
        .route("/api/log/list", get(log_list))
        .route("/api/log/save", post(log_save))
        .route("/api/log/delete", delete(log_delete))
        // 批量同步
        .route("/api/sync/pull", get(sync_pull))
        .route("/api/sync/push", post(sync_push))
        // 健康检查（不需要 Token，便于运维探活）
        .route("/healthz", get(|| async { "ok" }))
        .layer(middleware::from_fn_with_state(state.clone(), require_token))
        .layer(CorsLayer::permissive())
        .with_state(state)
}

/* ============================== 基础 ============================== */

async fn ping() -> Json<Value> {
    Json(json!({
        "ok": true,
        "version": env!("CARGO_PKG_VERSION"),
        "serverTime": now_ms()
    }))
}

/* ============================== 目录分支 ============================== */

const TREE_UPSERT: &str = r#"
INSERT INTO `tree` (id, parent_id, name, `sort`, create_time, update_time, is_deleted, sync_status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  parent_id = VALUES(parent_id),
  name = VALUES(name),
  `sort` = VALUES(`sort`),
  update_time = VALUES(update_time),
  is_deleted = VALUES(is_deleted),
  sync_status = VALUES(sync_status)
"#;

async fn tree_list(State(app): State<AppState>) -> ApiResult<Vec<Tree>> {
    let rows = sqlx::query("SELECT * FROM `tree` WHERE is_deleted = 0 ORDER BY `sort` ASC, create_time ASC")
        .fetch_all(&app.pool)
        .await
        .map_err(internal)?;

    let list = rows
        .iter()
        .filter_map(|r| Tree::from_row(r).ok())
        .collect::<Vec<_>>();
    Ok(Json(list))
}

async fn tree_save(State(app): State<AppState>, Json(t): Json<Tree>) -> ApiResult<Value> {
    if t.id.trim().is_empty() {
        return Err(bad_request("缺少分支 id"));
    }
    sqlx::query(TREE_UPSERT)
        .bind(&t.id)
        .bind(&t.parent_id)
        .bind(&t.name)
        .bind(t.sort)
        .bind(t.create_time)
        .bind(t.update_time)
        .bind(t.is_deleted)
        .bind("synced")
        .execute(&app.pool)
        .await
        .map_err(internal)?;
    Ok(Json(json!({ "ok": true, "id": t.id })))
}

async fn tree_delete(State(app): State<AppState>, Query(q): Query<IdQuery>) -> ApiResult<Value> {
    sqlx::query("UPDATE `tree` SET is_deleted = 1, update_time = ? WHERE id = ?")
        .bind(now_ms())
        .bind(&q.id)
        .execute(&app.pool)
        .await
        .map_err(internal)?;
    Ok(Json(json!({ "ok": true })))
}

/* ============================== 日志 ============================== */

const LOG_UPSERT: &str = r#"
INSERT INTO `work_log` (id, tree_id, title, content, create_time, update_time, is_deleted, sync_status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
  tree_id = VALUES(tree_id),
  title = VALUES(title),
  content = VALUES(content),
  update_time = VALUES(update_time),
  is_deleted = VALUES(is_deleted),
  sync_status = VALUES(sync_status)
"#;

async fn log_list(State(app): State<AppState>, Query(q): Query<TreeQuery>) -> ApiResult<Vec<WorkLog>> {
    let rows = match q.tree_id {
        Some(tid) => {
            sqlx::query("SELECT * FROM `work_log` WHERE is_deleted = 0 AND tree_id = ? ORDER BY update_time DESC")
                .bind(tid)
                .fetch_all(&app.pool)
                .await
        }
        None => {
            sqlx::query("SELECT * FROM `work_log` WHERE is_deleted = 0 ORDER BY update_time DESC LIMIT 500")
                .fetch_all(&app.pool)
                .await
        }
    }
    .map_err(internal)?;

    let list = rows
        .iter()
        .filter_map(|r| WorkLog::from_row(r).ok())
        .collect::<Vec<_>>();
    Ok(Json(list))
}

async fn log_save(State(app): State<AppState>, Json(l): Json<WorkLog>) -> ApiResult<Value> {
    if l.id.trim().is_empty() {
        return Err(bad_request("缺少日志 id"));
    }
    sqlx::query(LOG_UPSERT)
        .bind(&l.id)
        .bind(&l.tree_id)
        .bind(&l.title)
        .bind(&l.content)
        .bind(l.create_time)
        .bind(l.update_time)
        .bind(l.is_deleted)
        .bind("synced")
        .execute(&app.pool)
        .await
        .map_err(internal)?;
    Ok(Json(json!({ "ok": true, "id": l.id })))
}

async fn log_delete(State(app): State<AppState>, Query(q): Query<IdQuery>) -> ApiResult<Value> {
    sqlx::query("UPDATE `work_log` SET is_deleted = 1, update_time = ? WHERE id = ?")
        .bind(now_ms())
        .bind(&q.id)
        .execute(&app.pool)
        .await
        .map_err(internal)?;
    Ok(Json(json!({ "ok": true })))
}

/* ============================== 批量同步 ============================== */

async fn sync_pull(State(app): State<AppState>, Query(q): Query<SinceQuery>) -> ApiResult<PullResponse> {
    let since = q.since.unwrap_or(0);

    let tree_rows = sqlx::query("SELECT * FROM `tree` WHERE update_time > ? ORDER BY update_time ASC")
        .bind(since)
        .fetch_all(&app.pool)
        .await
        .map_err(internal)?;

    let log_rows = sqlx::query("SELECT * FROM `work_log` WHERE update_time > ? ORDER BY update_time ASC")
        .bind(since)
        .fetch_all(&app.pool)
        .await
        .map_err(internal)?;

    Ok(Json(PullResponse {
        server_time: now_ms(),
        trees: tree_rows.iter().filter_map(|r| Tree::from_row(r).ok()).collect(),
        logs: log_rows.iter().filter_map(|r| WorkLog::from_row(r).ok()).collect(),
    }))
}

async fn sync_push(State(app): State<AppState>, Json(req): Json<PushRequest>) -> ApiResult<PushResponse> {
    let mut applied = 0i64;
    let mut conflicts: Vec<ConflictOut> = Vec::new();

    for ch in req.changes {
        let payload = match ch.payload {
            Some(p) => p,
            None => continue,
        };

        let is_tree = ch.data_type == "tree";
        if !is_tree && ch.data_type != "log" {
            continue;
        }

        // 冲突检测：云端更新时间更晚，且客户端未要求强制覆盖
        let existing: Option<i64> = if is_tree {
            sqlx::query_scalar("SELECT update_time FROM `tree` WHERE id = ?")
                .bind(&ch.id)
                .fetch_optional(&app.pool)
                .await
                .map_err(internal)?
        } else {
            sqlx::query_scalar("SELECT update_time FROM `work_log` WHERE id = ?")
                .bind(&ch.id)
                .fetch_optional(&app.pool)
                .await
                .map_err(internal)?
        };

        if let Some(remote_time) = existing {
            if !req.force && remote_time > ch.update_time {
                // 取云端当前版本返回给客户端，由用户决定保留哪一份
                let remote_json: Option<Value> = if is_tree {
                    sqlx::query("SELECT * FROM `tree` WHERE id = ?")
                        .bind(&ch.id)
                        .fetch_optional(&app.pool)
                        .await
                        .map_err(internal)?
                        .and_then(|r| Tree::from_row(&r).ok())
                        .and_then(|t| serde_json::to_value(t).ok())
                } else {
                    sqlx::query("SELECT * FROM `work_log` WHERE id = ?")
                        .bind(&ch.id)
                        .fetch_optional(&app.pool)
                        .await
                        .map_err(internal)?
                        .and_then(|r| WorkLog::from_row(&r).ok())
                        .and_then(|l| serde_json::to_value(l).ok())
                };

                if let Some(remote) = remote_json {
                    conflicts.push(ConflictOut {
                        data_type: ch.data_type.clone(),
                        id: ch.id.clone(),
                        remote_update_time: remote_time,
                        remote,
                    });
                    continue;
                }
            }
        }

        if is_tree {
            let t: Tree = serde_json::from_value(payload).map_err(|e| bad_request(&format!("分支数据格式错误：{}", e)))?;
            sqlx::query(TREE_UPSERT)
                .bind(&t.id)
                .bind(&t.parent_id)
                .bind(&t.name)
                .bind(t.sort)
                .bind(t.create_time)
                .bind(t.update_time)
                .bind(t.is_deleted)
                .bind("synced")
                .execute(&app.pool)
                .await
                .map_err(internal)?;
        } else {
            let l: WorkLog =
                serde_json::from_value(payload).map_err(|e| bad_request(&format!("日志数据格式错误：{}", e)))?;
            sqlx::query(LOG_UPSERT)
                .bind(&l.id)
                .bind(&l.tree_id)
                .bind(&l.title)
                .bind(&l.content)
                .bind(l.create_time)
                .bind(l.update_time)
                .bind(l.is_deleted)
                .bind("synced")
                .execute(&app.pool)
                .await
                .map_err(internal)?;
        }

        applied += 1;
    }

    Ok(Json(PushResponse { applied, conflicts }))
}
