/**
 * 统一数据访问层。
 *
 * 同一套前端代码可在两种运行时下工作：
 *  1) Tauri 桌面端：通过 invoke 调用 Rust 命令（本地 SQLite，离线优先）；
 *  2) 浏览器预览：通过 HTTP 调用 preview/server.mjs（同一套命令名 + 同一套表结构）。
 *
 * 业务代码只依赖本文件导出的函数，不关心底层运行时。
 */

const TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** 浏览器预览模式下的后端地址（默认同源，由 vite proxy 转发） */
const HTTP_BASE = TAURI ? "" : "";

async function invokeTauri(cmd, args) {
  const mod = await import("@tauri-apps/api/core");
  return mod.invoke(cmd, args || {});
}

async function invokeHttp(cmd, args) {
  const res = await fetch(`${HTTP_BASE}/api/cmd/${cmd}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args || {}),
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(text || `请求失败(${res.status})`);
  }
  if (!res.ok) {
    throw new Error((data && (data.error || data.message)) || `请求失败(${res.status})`);
  }
  return data;
}

export function call(cmd, args) {
  return TAURI ? invokeTauri(cmd, args) : invokeHttp(cmd, args);
}

export const isDesktop = TAURI;

/* ------------------------------ 目录分支 ------------------------------ */

export const listTrees = () => call("list_trees");
export const createTree = (parentId, name) => call("create_tree", { parentId, name });
export const renameTree = (id, name) => call("rename_tree", { id, name });
export const deleteTree = (id, deleteLogs) => call("delete_tree", { id, deleteLogs });
export const reorderTrees = (updates) => call("reorder_trees", { updates });

/* -------------------------------- 日志 -------------------------------- */

export const listLogs = (treeId) => call("list_logs", { treeId });
export const getLog = (id) => call("get_log", { id });
export const createLog = (treeId, title, content) => call("create_log", { treeId, title, content });
export const updateLog = (id, title, content) => call("update_log", { id, title, content });
export const deleteLog = (id) => call("delete_log", { id });
export const searchLogs = (keyword) => call("search_logs", { keyword });

/* ------------------------------ 配置与同步 ------------------------------ */

export const getConfig = () => call("get_config");
export const saveConfig = (config) => call("save_config", { config });
export const netStatus = () => call("net_status");
export const testConnection = () => call("test_connection");
export const syncNow = () => call("sync_now");
export const resolveConflicts = (choices) => call("resolve_conflicts", { choices });
export const pendingChanges = () => call("pending_changes");
export const appInfo = () => call("app_info");
export const backupDb = () => call("backup_db");

/* ---------------------------- 导入 / 导出 / 外观 ---------------------------- */

export const exportData = () => call("export_data");
export const importData = (payload, mode = "merge") => call("import_data", { payload, mode });
export const saveBackground = (dataUrl) => call("save_background", { dataUrl });
export const clearBackground = () => call("clear_background");
export const openDataDir = () => call("open_data_dir");
