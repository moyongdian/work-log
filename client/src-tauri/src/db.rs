//! 本地 SQLite 存储层（离线核心）
//!
//! 设计要点：
//! - 本地库是唯一主数据源，所有 CRUD 直接落库，不等待网络；
//! - change_log 记录每一条离线变更，同步时只推送变更数据；
//! - 同步成功后删除对应 change_log，失败则保留等待下次重试。

use rusqlite::{params, Connection};
use std::path::Path;

pub const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS tree (
    id          TEXT PRIMARY KEY,
    parent_id   TEXT,
    name        TEXT NOT NULL,
    sort        INTEGER NOT NULL DEFAULT 0,
    create_time INTEGER NOT NULL,
    update_time INTEGER NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'unsynced'
);
CREATE INDEX IF NOT EXISTS idx_tree_parent ON tree(parent_id, sort);
CREATE INDEX IF NOT EXISTS idx_tree_update ON tree(update_time);

CREATE TABLE IF NOT EXISTS work_log (
    id          TEXT PRIMARY KEY,
    tree_id     TEXT,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    create_time INTEGER NOT NULL,
    update_time INTEGER NOT NULL,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'unsynced'
);
CREATE INDEX IF NOT EXISTS idx_log_tree ON work_log(tree_id, update_time DESC);
CREATE INDEX IF NOT EXISTS idx_log_update ON work_log(update_time);

CREATE TABLE IF NOT EXISTS change_log (
    id           TEXT PRIMARY KEY,
    data_type    TEXT NOT NULL,
    data_id      TEXT NOT NULL,
    operate      TEXT NOT NULL,
    operate_time INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_change_time ON change_log(operate_time);
CREATE INDEX IF NOT EXISTS idx_change_data ON change_log(data_type, data_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"#;

/// 打开（或创建）本地数据库并初始化表结构
pub fn open(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open(path)?;
    // WAL 模式：读写并发更好，掉电时数据更安全
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA synchronous=NORMAL;
         PRAGMA temp_store=MEMORY;",
    )?;
    conn.execute_batch(SCHEMA)?;
    Ok(conn)
}

/// 记录一条变更（同一数据多次修改只保留最后一条，避免重复推送）
pub fn record_change(
    conn: &Connection,
    data_type: &str,
    data_id: &str,
    operate: &str,
) -> rusqlite::Result<()> {
    conn.execute(
        "DELETE FROM change_log WHERE data_type = ?1 AND data_id = ?2",
        params![data_type, data_id],
    )?;
    conn.execute(
        "INSERT INTO change_log (id, data_type, data_id, operate, operate_time)
         VALUES (?1, ?2, ?3, ?4, ?5)",
        params![
            uuid::Uuid::new_v4().to_string(),
            data_type,
            data_id,
            operate,
            super::models::now_ms()
        ],
    )?;
    Ok(())
}

/// 读取待同步变更数量
pub fn pending_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT COUNT(*) FROM change_log", [], |r| r.get(0))
}

/// 是否存在未同步的变更
pub fn has_pending(conn: &Connection, data_type: &str, data_id: &str) -> bool {
    conn.query_row(
        "SELECT COUNT(*) FROM change_log WHERE data_type = ?1 AND data_id = ?2",
        params![data_type, data_id],
        |r| r.get::<_, i64>(0),
    )
    .map(|n| n > 0)
    .unwrap_or(false)
}

/// 把一条数据标记为已同步
pub fn mark_synced(conn: &Connection, data_type: &str, data_id: &str) -> rusqlite::Result<()> {
    let table = if data_type == "tree" { "tree" } else { "work_log" };
    conn.execute(
        &format!("UPDATE {} SET sync_status = 'synced' WHERE id = ?1", table),
        params![data_id],
    )?;
    conn.execute(
        "DELETE FROM change_log WHERE data_type = ?1 AND data_id = ?2",
        params![data_type, data_id],
    )?;
    Ok(())
}

/// 读取/写入 meta 键值
pub fn get_meta(conn: &Connection, key: &str) -> Option<String> {
    conn.query_row("SELECT value FROM meta WHERE key = ?1", params![key], |r| r.get(0))
        .ok()
}

pub fn set_meta(conn: &Connection, key: &str, value: &str) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        params![key, value],
    )?;
    Ok(())
}
