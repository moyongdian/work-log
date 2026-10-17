//! 个人工作日志笔记本系统 · 客户端入口
//!
//! 离线优先：本地 SQLite 是唯一主数据源，所有 CRUD 不依赖网络；
//! 联网后由用户手动触发同步，与自建云服务器交换变更数据。

mod commands;
mod config;
mod db;
mod models;
mod sync;

use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Manager;

pub struct Paths {
    pub db_file: PathBuf,
    pub backup_dir: PathBuf,
    pub config_file: PathBuf,
}

pub struct AppState {
    pub db: Mutex<rusqlite::Connection>,
    pub cfg: Mutex<config::Config>,
    pub paths: Paths,
    /// 本次同步中待用户决策的冲突
    pub conflicts: Mutex<Vec<models::ConflictItem>>,
}

/// 立即备份本地数据库（退出时自动调用，也可手动触发）
pub fn backup_now(state: &AppState) -> Result<String, String> {
    {
        let conn = state.db.lock().map_err(|e| e.to_string())?;
        // 先把 WAL 内容合并回主库，保证备份文件完整
        let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");
    }

    std::fs::create_dir_all(&state.paths.backup_dir).map_err(|e| e.to_string())?;
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let target = state.paths.backup_dir.join(format!("notebook-{}.db", secs));

    std::fs::copy(&state.paths.db_file, &target).map_err(|e| format!("备份失败：{}", e))?;

    // 只保留最近 5 份备份，避免占用磁盘
    if let Ok(entries) = std::fs::read_dir(&state.paths.backup_dir) {
        let mut files: Vec<PathBuf> = entries
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n.starts_with("notebook-") && n.ends_with(".db"))
                    .unwrap_or(false)
            })
            .collect();
        files.sort();
        while files.len() > 5 {
            let old = files.remove(0);
            let _ = std::fs::remove_file(old);
        }
    }

    Ok(target.display().to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;

            let paths = Paths {
                db_file: data_dir.join("notebook.db"),
                backup_dir: data_dir.join("backups"),
                config_file: data_dir.join("config.json"),
            };

            let conn = db::open(&paths.db_file)?;
            let cfg = config::Config::load(&paths.config_file);

            app.manage(AppState {
                db: Mutex::new(conn),
                cfg: Mutex::new(cfg),
                paths,
                conflicts: Mutex::new(Vec::new()),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::list_trees,
            commands::create_tree,
            commands::rename_tree,
            commands::delete_tree,
            commands::reorder_trees,
            commands::list_logs,
            commands::get_log,
            commands::create_log,
            commands::update_log,
            commands::delete_log,
            commands::search_logs,
            commands::get_config,
            commands::save_config,
            commands::pending_changes,
            commands::app_info,
            commands::backup_db,
            commands::net_status,
            commands::test_connection,
            sync::sync_now,
            sync::resolve_conflicts,
        ])
        .build(tauri::generate_context!())
        .expect("初始化 Tauri 应用失败");

    app.run(|handle, event| {
        // 退出时自动备份本地数据库（防止 SQLite 意外损坏导致数据丢失）
        if let tauri::RunEvent::Exit = event {
            let state = handle.state::<AppState>();
            if let Err(e) = backup_now(&state) {
                eprintln!("退出备份失败：{}", e);
            }
        }
    });
}
