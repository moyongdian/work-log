//! 个人工作日志笔记本系统 · 云端同步服务
//!
//! 单二进制部署，常驻内存极低（无框架额外开销）。
//! 环境变量：
//!   DATABASE_URL  MySQL 连接串，如 mysql://user:pass@127.0.0.1:3306/notebook
//!   API_TOKEN     客户端访问令牌（必填，客户端需配置相同值）
//!   BIND_ADDR     监听地址，默认 0.0.0.0:8787
//!   RUST_LOG      日志级别，默认 info

mod auth;
mod models;
mod routes;

use routes::AppState;
use sqlx::mysql::MySqlPoolOptions;
use std::net::SocketAddr;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let _ = dotenvy::dotenv();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_target(false)
        .init();

    let database_url = std::env::var("DATABASE_URL")
        .map_err(|_| "缺少环境变量 DATABASE_URL，例如 mysql://user:pass@127.0.0.1:3306/notebook")?;
    let token = std::env::var("API_TOKEN").map_err(|_| "缺少环境变量 API_TOKEN（客户端需配置相同值）")?;
    if token.trim().len() < 8 {
        return Err("API_TOKEN 太短，建议至少 16 位随机字符串".into());
    }
    let bind_addr = std::env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8787".to_string());

    let pool = MySqlPoolOptions::new()
        .max_connections(10)
        .min_connections(1)
        .acquire_timeout(std::time::Duration::from_secs(8))
        .connect(&database_url)
        .await
        .map_err(|e| format!("连接 MySQL 失败：{}", e))?;

    let state = AppState {
        pool: pool.clone(),
        token: token.clone(),
    };
    let app = routes::router(state);

    let addr: SocketAddr = bind_addr.parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;

    tracing::info!("工作日志笔记本 · 同步服务已启动，监听 http://{}", addr);
    tracing::info!("提示：生产环境建议前置 Nginx 并启用 HTTPS");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    pool.close().await;
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c().await.expect("监听 Ctrl+C 失败");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("监听 SIGTERM 失败")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
    tracing::info!("收到退出信号，正在关闭…");
}
