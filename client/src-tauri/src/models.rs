//! 数据模型与通用工具

use serde::{Deserialize, Serialize};
use std::time::{SystemTime, UNIX_EPOCH};

/// 当前毫秒时间戳
pub fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

pub fn err_str<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

/// 目录分支（对应本地 SQLite / 云端 MySQL 的 tree 表）
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
    pub fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<Tree> {
        Ok(Tree {
            id: row.get("id")?,
            parent_id: row.get("parent_id")?,
            name: row.get("name")?,
            sort: row.get("sort")?,
            create_time: row.get("create_time")?,
            update_time: row.get("update_time")?,
            is_deleted: row.get("is_deleted")?,
            sync_status: row.get("sync_status")?,
        })
    }
}

/// 日志（work_log 表）
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
    pub fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<WorkLog> {
        Ok(WorkLog {
            id: row.get("id")?,
            tree_id: row.get("tree_id")?,
            title: row.get("title")?,
            content: row.get("content")?,
            create_time: row.get("create_time")?,
            update_time: row.get("update_time")?,
            is_deleted: row.get("is_deleted")?,
            sync_status: row.get("sync_status")?,
        })
    }
}

/// 日志列表项（不返回完整正文，降低内存占用）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LogSummary {
    pub id: String,
    pub tree_id: Option<String>,
    pub title: String,
    pub preview: String,
    pub create_time: i64,
    pub update_time: i64,
    pub sync_status: String,
}

/// 拖拽排序项
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TreeSort {
    pub id: String,
    pub parent_id: Option<String>,
    pub sort: i64,
}

/// 同步冲突（本地与云端都改过同一条数据）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConflictItem {
    pub data_type: String,
    pub id: String,
    pub local_update_time: i64,
    pub remote_update_time: i64,
    pub local_name: Option<String>,
    pub remote_name: Option<String>,
    pub local_title: Option<String>,
    pub remote_title: Option<String>,
    pub remote: serde_json::Value,
}

/// 冲突处理选择
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConflictChoice {
    pub data_type: String,
    pub id: String,
    pub keep_local: bool,
}

/// 同步结果
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncReport {
    pub pushed: i64,
    pub pulled: i64,
    pub failed: i64,
    pub conflicts: Vec<ConflictItem>,
    pub message: String,
}

impl SyncReport {
    pub fn empty(message: &str) -> Self {
        SyncReport {
            pushed: 0,
            pulled: 0,
            failed: 0,
            conflicts: Vec::new(),
            message: message.to_string(),
        }
    }
}

/// 网络状态
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NetStatus {
    pub online: bool,
    pub reachable: bool,
}

/// 运行环境信息
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppInfo {
    pub db_path: String,
    pub backup_dir: String,
    pub desktop: bool,
}
