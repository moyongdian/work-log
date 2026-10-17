//! 服务端数据模型与接口 DTO

use serde::{Deserialize, Serialize};
use sqlx::mysql::MySqlRow;
use sqlx::Row;

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Tree {
    pub id: String,
    pub parent_id: Option<String>,
    pub name: String,
    pub sort: i64,
    pub create_time: i64,
    pub update_time: i64,
    pub is_deleted: i64,
    pub sync_status: String,
}

impl Tree {
    pub fn from_row(row: &MySqlRow) -> Result<Self, sqlx::Error> {
        Ok(Tree {
            id: row.try_get("id")?,
            parent_id: row.try_get("parent_id")?,
            name: row.try_get("name")?,
            sort: row.try_get("sort")?,
            create_time: row.try_get("create_time")?,
            update_time: row.try_get("update_time")?,
            is_deleted: row.try_get("is_deleted")?,
            sync_status: row.try_get("sync_status")?,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkLog {
    pub id: String,
    pub tree_id: Option<String>,
    pub title: String,
    pub content: String,
    pub create_time: i64,
    pub update_time: i64,
    pub is_deleted: i64,
    pub sync_status: String,
}

impl WorkLog {
    pub fn from_row(row: &MySqlRow) -> Result<Self, sqlx::Error> {
        let content: Option<String> = row.try_get("content")?;
        Ok(WorkLog {
            id: row.try_get("id")?,
            tree_id: row.try_get("tree_id")?,
            title: row.try_get("title")?,
            content: content.unwrap_or_default(),
            create_time: row.try_get("create_time")?,
            update_time: row.try_get("update_time")?,
            is_deleted: row.try_get("is_deleted")?,
            sync_status: row.try_get("sync_status")?,
        })
    }
}

/// 单条变更
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChangeItem {
    pub data_type: String,
    pub id: String,
    pub operate: String,
    pub update_time: i64,
    pub payload: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PushRequest {
    pub changes: Vec<ChangeItem>,
    #[serde(default)]
    pub force: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConflictOut {
    pub data_type: String,
    pub id: String,
    pub remote_update_time: i64,
    pub remote: serde_json::Value,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PushResponse {
    pub applied: i64,
    pub conflicts: Vec<ConflictOut>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PullResponse {
    pub server_time: i64,
    pub trees: Vec<Tree>,
    pub logs: Vec<WorkLog>,
}

#[derive(Debug, Deserialize)]
pub struct IdQuery {
    pub id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TreeQuery {
    pub tree_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct SinceQuery {
    pub since: Option<i64>,
}
