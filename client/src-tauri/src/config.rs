//! 客户端配置（云服务器地址、Token、本地行为开关）

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct Config {
    /// 云服务器地址，例如 http://1.2.3.4:8787 ；留空表示纯本地模式
    pub server_url: String,
    /// 与服务端 API_TOKEN 一致的访问令牌
    pub token: String,
    /// 自动保存日志
    pub auto_save: bool,
    /// 新建目录自动添加日期前缀
    pub auto_date_prefix: bool,
    /// 上次成功同步的时间戳（毫秒），用于增量拉取
    pub last_sync_at: i64,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            server_url: String::new(),
            token: String::new(),
            auto_save: true,
            auto_date_prefix: true,
            last_sync_at: 0,
        }
    }
}

impl Config {
    pub fn load(path: &Path) -> Config {
        std::fs::read_to_string(path)
            .ok()
            .and_then(|s| serde_json::from_str::<Config>(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let text = serde_json::to_string_pretty(self).unwrap_or_else(|_| "{}".into());
        std::fs::write(path, text)
    }
}
