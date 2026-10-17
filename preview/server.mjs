/**
 * 个人工作日志笔记本系统 · 本地预览服务（零依赖）
 *
 * 作用：在没有 Rust / Tauri 工具链的环境下，直接运行完整软件进行体验与联调。
 *  - 使用 Node 内置 SQLite（node:sqlite），表结构与 client/src-tauri 完全一致；
 *  - 实现与 Rust 端同名同参数的命令集合，前端 src/api.js 通过 HTTP 调用；
 *  - 额外内置一个「模拟云端」（/cloud/api/*），接口与 server/src 的 Axum 服务一致，
 *    用于离线/在线、推送、拉取、冲突处理的完整演示。
 *
 * 运行：node --experimental-sqlite preview/server.mjs
 */

import { DatabaseSync } from "node:sqlite";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DIST = path.join(ROOT, "client", "dist");
const DATA_DIR = path.join(__dirname, "data");
const PORT = Number(process.env.PREVIEW_PORT || 8971);
const CLOUD_TOKEN = "preview-token";

fs.mkdirSync(DATA_DIR, { recursive: true });

/* ============================== 数据库 ============================== */

const SCHEMA = `
CREATE TABLE IF NOT EXISTS tree (
  id TEXT PRIMARY KEY, parent_id TEXT, name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0,
  create_time INTEGER NOT NULL, update_time INTEGER NOT NULL,
  is_deleted INTEGER NOT NULL DEFAULT 0, sync_status TEXT NOT NULL DEFAULT 'unsynced'
);
CREATE INDEX IF NOT EXISTS idx_tree_parent ON tree(parent_id, sort);
CREATE TABLE IF NOT EXISTS work_log (
  id TEXT PRIMARY KEY, tree_id TEXT, title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
  create_time INTEGER NOT NULL, update_time INTEGER NOT NULL,
  is_deleted INTEGER NOT NULL DEFAULT 0, sync_status TEXT NOT NULL DEFAULT 'unsynced'
);
CREATE INDEX IF NOT EXISTS idx_log_tree ON work_log(tree_id, update_time DESC);
CREATE TABLE IF NOT EXISTS change_log (
  id TEXT PRIMARY KEY, data_type TEXT NOT NULL, data_id TEXT NOT NULL,
  operate TEXT NOT NULL, operate_time INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_change_time ON change_log(operate_time);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
`;

function openDb(file) {
  const db = new DatabaseSync(file);
  db.exec("PRAGMA journal_mode=WAL;");
  db.exec(SCHEMA);
  return db;
}

const local = openDb(path.join(DATA_DIR, "notebook.db"));
const cloud = openDb(path.join(DATA_DIR, "cloud.db"));

const now = () => Date.now();
const uuid = () => randomUUID();

const treeOut = (r) =>
  r && {
    id: r.id,
    parentId: r.parent_id ?? null,
    name: r.name,
    sort: r.sort,
    createTime: r.create_time,
    updateTime: r.update_time,
    isDeleted: r.is_deleted,
    syncStatus: r.sync_status,
  };

const logOut = (r) =>
  r && {
    id: r.id,
    treeId: r.tree_id ?? null,
    title: r.title,
    content: r.content ?? "",
    createTime: r.create_time,
    updateTime: r.update_time,
    isDeleted: r.is_deleted,
    syncStatus: r.sync_status,
  };

const summaryOut = (r) => ({
  id: r.id,
  treeId: r.tree_id ?? null,
  title: r.title,
  preview: (r.preview || "").slice(0, 240),
  createTime: r.create_time,
  updateTime: r.update_time,
  syncStatus: r.sync_status,
});

function recordChange(db, dataType, dataId, operate) {
  db.prepare("DELETE FROM change_log WHERE data_type = ? AND data_id = ?").run(dataType, dataId);
  db.prepare(
    "INSERT INTO change_log (id, data_type, data_id, operate, operate_time) VALUES (?,?,?,?,?)"
  ).run(uuid(), dataType, dataId, operate, now());
}

const pendingCount = (db) => db.prepare("SELECT COUNT(*) AS n FROM change_log").get().n;

/* ============================== 配置 ============================== */

const CONFIG_FILE = path.join(DATA_DIR, "config.json");
const MEDIA_DIR = path.join(DATA_DIR, "media");

const DEFAULT_THEME = { mode: "light", bgImage: "", bgOpacity: 0.6 };
const DEFAULT_SHORTCUTS = {
  newLog: "Ctrl+N",
  newTree: "Ctrl+Shift+N",
  save: "Ctrl+S",
  sync: "F5",
  search: "Ctrl+F",
  settings: "Ctrl+,",
  toggleTheme: "Ctrl+Shift+L",
  exportData: "Ctrl+E",
};

const DEFAULT_CONFIG = {
  serverUrl: `http://127.0.0.1:${PORT}/cloud`,
  token: CLOUD_TOKEN,
  autoSave: true,
  autoDatePrefix: true,
  lastSyncAt: 0,
  theme: DEFAULT_THEME,
  shortcuts: DEFAULT_SHORTCUTS,
};

function normTheme(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const clamp = (v, min, max, dft) => {
    const n = Number(v);
    return Number.isNaN(n) ? dft : Math.min(max, Math.max(min, n));
  };
  return {
    mode: src.mode === "dark" ? "dark" : "light",
    bgImage: typeof src.bgImage === "string" ? src.bgImage : "",
    bgOpacity: clamp(src.bgOpacity, 0, 1, 0.6),
  };
}

function normShortcuts(raw) {
  const out = { ...DEFAULT_SHORTCUTS };
  if (raw && typeof raw === "object") {
    for (const k of Object.keys(DEFAULT_SHORTCUTS)) {
      if (typeof raw[k] === "string" && raw[k].trim()) out[k] = raw[k].trim();
    }
  }
  return out;
}

function loadConfig() {
  let cfg = { ...DEFAULT_CONFIG };
  try {
    cfg = { ...cfg, ...JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8")) };
  } catch {
    /* 首次运行没有配置文件 */
  }
  cfg.theme = normTheme(cfg.theme);
  cfg.shortcuts = normShortcuts(cfg.shortcuts);
  return cfg;
}

function saveConfigFile(cfg) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2), "utf8");
}

/* ============================== 目录分支 ============================== */

function listTrees() {
  return local
    .prepare("SELECT * FROM tree WHERE is_deleted = 0 ORDER BY sort ASC, create_time ASC")
    .all()
    .map(treeOut);
}

function createTree({ parentId = null, name }) {
  const clean = String(name || "").trim();
  if (!clean) throw new Error("分支名称不能为空");
  if (parentId) {
    const p = local.prepare("SELECT id FROM tree WHERE id = ? AND is_deleted = 0").get(parentId);
    if (!p) throw new Error("父分支不存在");
  }
  const ts = now();
  const id = uuid();
  const sort =
    local
      .prepare(
        "SELECT COALESCE(MAX(sort),0)+10 AS s FROM tree WHERE is_deleted=0 AND ((parent_id IS NULL AND ? IS NULL) OR parent_id = ?)"
      )
      .get(parentId, parentId).s;
  local
    .prepare(
      "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) VALUES (?,?,?,?,?,?,0,'unsynced')"
    )
    .run(id, parentId, clean, sort, ts, ts);
  recordChange(local, "tree", id, "add");
  return treeOut(local.prepare("SELECT * FROM tree WHERE id = ?").get(id));
}

function renameTree({ id, name }) {
  const clean = String(name || "").trim();
  if (!clean) throw new Error("分支名称不能为空");
  const info = local
    .prepare("UPDATE tree SET name = ?, update_time = ?, sync_status='unsynced' WHERE id = ? AND is_deleted = 0")
    .run(clean, now(), id);
  if (!info.changes) throw new Error("分支不存在");
  recordChange(local, "tree", id, "update");
  return treeOut(local.prepare("SELECT * FROM tree WHERE id = ?").get(id));
}

function collectSubtree(rootId) {
  const ids = [rootId];
  for (let i = 0; i < ids.length; i++) {
    const children = local
      .prepare("SELECT id FROM tree WHERE parent_id = ? AND is_deleted = 0")
      .all(ids[i])
      .map((r) => r.id);
    for (const c of children) if (!ids.includes(c)) ids.push(c);
  }
  return ids;
}

const ARCHIVE_NAME = "未分类（归档）";

function ensureArchive() {
  const found = local
    .prepare("SELECT id FROM tree WHERE name = ? AND parent_id IS NULL AND is_deleted = 0")
    .get(ARCHIVE_NAME);
  if (found) return found.id;
  const ts = now();
  const id = uuid();
  const sort = local
    .prepare("SELECT COALESCE(MAX(sort),0)+10 AS s FROM tree WHERE parent_id IS NULL AND is_deleted = 0")
    .get().s;
  local
    .prepare(
      "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) VALUES (?,NULL,?,?,?,?,0,'unsynced')"
    )
    .run(id, ARCHIVE_NAME, sort, ts, ts);
  recordChange(local, "tree", id, "add");
  return id;
}

function deleteTree({ id, deleteLogs = true }) {
  const row = local.prepare("SELECT * FROM tree WHERE id = ?").get(id);
  if (!row) throw new Error("分支不存在");
  const ids = collectSubtree(id);
  const ts = now();
  const dest = deleteLogs ? null : row.parent_id || ensureArchive();

  local.exec("BEGIN");
  try {
    for (const tid of ids) {
      const logs = local.prepare("SELECT id FROM work_log WHERE tree_id = ? AND is_deleted = 0").all(tid);
      for (const l of logs) {
        if (dest) {
          local
            .prepare("UPDATE work_log SET tree_id = ?, update_time = ?, sync_status='unsynced' WHERE id = ?")
            .run(dest, ts, l.id);
          recordChange(local, "log", l.id, "update");
        } else {
          local
            .prepare("UPDATE work_log SET is_deleted = 1, update_time = ?, sync_status='unsynced' WHERE id = ?")
            .run(ts, l.id);
          recordChange(local, "log", l.id, "delete");
        }
      }
    }
    for (const tid of ids) {
      local
        .prepare("UPDATE tree SET is_deleted = 1, update_time = ?, sync_status='unsynced' WHERE id = ?")
        .run(ts, tid);
      recordChange(local, "tree", tid, "delete");
    }
    local.exec("COMMIT");
  } catch (e) {
    local.exec("ROLLBACK");
    throw e;
  }
  return { ok: true };
}

function reorderTrees({ updates = [] }) {
  const ts = now();
  local.exec("BEGIN");
  try {
    for (const u of updates) {
      local
        .prepare("UPDATE tree SET parent_id = ?, sort = ?, update_time = ?, sync_status='unsynced' WHERE id = ?")
        .run(u.parentId ?? null, u.sort, ts, u.id);
      recordChange(local, "tree", u.id, "update");
    }
    local.exec("COMMIT");
  } catch (e) {
    local.exec("ROLLBACK");
    throw e;
  }
  return { ok: true };
}

/* ============================== 日志 ============================== */

function listLogs({ treeId }) {
  return local
    .prepare(
      `SELECT id, tree_id, title, substr(content,1,240) AS preview, create_time, update_time, sync_status
       FROM work_log WHERE tree_id = ? AND is_deleted = 0 ORDER BY update_time DESC`
    )
    .all(treeId)
    .map(summaryOut);
}

const getLog = ({ id }) => {
  const r = local.prepare("SELECT * FROM work_log WHERE id = ?").get(id);
  if (!r) throw new Error("日志不存在");
  return logOut(r);
};

function createLog({ treeId, title, content = "" }) {
  const ts = now();
  const id = uuid();
  const t = String(title || "").trim() || "未命名日志";
  local
    .prepare(
      "INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status) VALUES (?,?,?,?,?,?,0,'unsynced')"
    )
    .run(id, treeId ?? null, t, content, ts, ts);
  recordChange(local, "log", id, "add");
  return logOut(local.prepare("SELECT * FROM work_log WHERE id = ?").get(id));
}

function updateLog({ id, title, content = "" }) {
  const t = String(title || "").trim() || "未命名日志";
  const info = local
    .prepare("UPDATE work_log SET title=?, content=?, update_time=?, sync_status='unsynced' WHERE id=? AND is_deleted=0")
    .run(t, content, now(), id);
  if (!info.changes) throw new Error("日志不存在或已删除");
  recordChange(local, "log", id, "update");
  return logOut(local.prepare("SELECT * FROM work_log WHERE id = ?").get(id));
}

function deleteLog({ id }) {
  local
    .prepare("UPDATE work_log SET is_deleted=1, update_time=?, sync_status='unsynced' WHERE id=?")
    .run(now(), id);
  recordChange(local, "log", id, "delete");
  return { ok: true };
}

function searchLogs({ keyword = "" }) {
  const like = `%${keyword.trim()}%`;
  return local
    .prepare(
      `SELECT id, tree_id, title, substr(content,1,240) AS preview, create_time, update_time, sync_status
       FROM work_log WHERE is_deleted = 0 AND (title LIKE ? OR content LIKE ?)
       ORDER BY update_time DESC LIMIT 200`
    )
    .all(like, like)
    .map(summaryOut);
}

/* ============================== 同步（与 Rust 端逻辑一致） ============================== */

function collectChanges() {
  const rows = local.prepare("SELECT * FROM change_log ORDER BY operate_time ASC").all();
  const out = [];
  for (const r of rows) {
    const payload =
      r.data_type === "tree"
        ? treeOut(local.prepare("SELECT * FROM tree WHERE id = ?").get(r.data_id))
        : logOut(local.prepare("SELECT * FROM work_log WHERE id = ?").get(r.data_id));
    if (!payload) continue;
    out.push({
      dataType: r.data_type,
      id: r.data_id,
      operate: r.operate,
      updateTime: payload.updateTime,
      payload,
    });
  }
  return out;
}

function markSynced(db, dataType, id) {
  const table = dataType === "tree" ? "tree" : "work_log";
  db.prepare(`UPDATE ${table} SET sync_status='synced' WHERE id = ?`).run(id);
  db.prepare("DELETE FROM change_log WHERE data_type = ? AND data_id = ?").run(dataType, id);
}

function applyRemote(db, trees = [], logs = [], force = false) {
  let applied = 0;
  for (const t of trees) {
    const cur = db.prepare("SELECT * FROM tree WHERE id = ?").get(t.id);
    const pending = db.prepare("SELECT COUNT(*) AS n FROM change_log WHERE data_type='tree' AND data_id=?").get(t.id).n;
    if (!force && cur && pending && cur.update_time > t.updateTime) continue;
    db.prepare(
      `INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status)
       VALUES (?,?,?,?,?,?,?,'synced')
       ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, name=excluded.name, sort=excluded.sort,
         update_time=excluded.update_time, is_deleted=excluded.is_deleted, sync_status='synced'`
    ).run(t.id, t.parentId ?? null, t.name, t.sort, t.createTime, t.updateTime, t.isDeleted ?? 0);
    db.prepare("DELETE FROM change_log WHERE data_type='tree' AND data_id=?").run(t.id);
    applied++;
  }
  for (const l of logs) {
    const cur = db.prepare("SELECT * FROM work_log WHERE id = ?").get(l.id);
    const pending = db.prepare("SELECT COUNT(*) AS n FROM change_log WHERE data_type='log' AND data_id=?").get(l.id).n;
    if (!force && cur && pending && cur.update_time > l.updateTime) continue;
    db.prepare(
      `INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status)
       VALUES (?,?,?,?,?,?,?,'synced')
       ON CONFLICT(id) DO UPDATE SET tree_id=excluded.tree_id, title=excluded.title, content=excluded.content,
         update_time=excluded.update_time, is_deleted=excluded.is_deleted, sync_status='synced'`
    ).run(l.id, l.treeId ?? null, l.title, l.content ?? "", l.createTime, l.updateTime, l.isDeleted ?? 0);
    db.prepare("DELETE FROM change_log WHERE data_type='log' AND data_id=?").run(l.id);
    applied++;
  }
  return applied;
}

let pendingConflicts = [];

async function syncNow() {
  const cfg = loadConfig();
  if (!cfg.serverUrl) throw new Error("未配置云服务器地址，请先在设置中填写");
  const base = cfg.serverUrl.replace(/\/$/, "");
  const headers = { "Content-Type": "application/json", "X-Api-Token": cfg.token };

  // 1) 连通性检测
  let ping;
  try {
    ping = await fetch(`${base}/api/ping`, { headers });
  } catch (e) {
    throw new Error("无法连接云服务器：" + e.message);
  }
  if (ping.status === 401) throw new Error("Token 校验失败，请检查 API Token");
  if (!ping.ok) throw new Error("云服务器异常：HTTP " + ping.status);

  // 2) 推送
  const changes = collectChanges();
  let pushed = 0;
  let conflicts = [];

  if (changes.length) {
    const resp = await fetch(`${base}/api/sync/push`, {
      method: "POST",
      headers,
      body: JSON.stringify({ changes, force: false }),
    });
    if (!resp.ok) throw new Error("推送失败：HTTP " + resp.status);
    const pr = await resp.json();
    pushed = pr.applied || 0;
    conflicts = (pr.conflicts || []).map((c) => {
      const localRow =
        c.dataType === "tree"
          ? treeOut(local.prepare("SELECT * FROM tree WHERE id = ?").get(c.id))
          : logOut(local.prepare("SELECT * FROM work_log WHERE id = ?").get(c.id));
      return {
        dataType: c.dataType,
        id: c.id,
        localUpdateTime: localRow ? localRow.updateTime : 0,
        remoteUpdateTime: c.remoteUpdateTime,
        localName: localRow && localRow.name,
        remoteName: c.remote && c.remote.name,
        localTitle: localRow && localRow.title,
        remoteTitle: c.remote && c.remote.title,
        remote: c.remote,
      };
    });

    const conflictKeys = new Set(conflicts.map((c) => `${c.dataType}:${c.id}`));
    for (const ch of changes) {
      if (conflictKeys.has(`${ch.dataType}:${ch.id}`)) continue;
      markSynced(local, ch.dataType, ch.id);
    }
  }

  // 3) 拉取
  const pullUrl = `${base}/api/sync/pull?since=${cfg.lastSyncAt || 0}`;
  const pullResp = await fetch(pullUrl, { headers });
  if (!pullResp.ok) throw new Error(`拉取失败：HTTP ${pullResp.status}（已推送 ${pushed} 条）`);
  const pr = await pullResp.json();
  const pulled = applyRemote(local, pr.trees, pr.logs, false);

  const cfg2 = loadConfig();
  cfg2.lastSyncAt = pr.serverTime;
  saveConfigFile(cfg2);

  pendingConflicts = conflicts;
  return {
    pushed,
    pulled,
    failed: 0,
    conflicts,
    message: `上传 ${pushed} 条，拉取 ${pulled} 条${conflicts.length ? `，${conflicts.length} 条冲突待处理` : ""}`,
  };
}

async function resolveConflicts({ choices = [] }) {
  const cfg = loadConfig();
  const base = cfg.serverUrl.replace(/\/$/, "");
  const headers = { "Content-Type": "application/json", "X-Api-Token": cfg.token };

  const keepLocalItems = [];
  const remoteTrees = [];
  const remoteLogs = [];

  for (const c of pendingConflicts) {
    const choice = choices.find((x) => x.dataType === c.dataType && x.id === c.id);
    const keepLocal = choice ? choice.keepLocal : true;
    if (keepLocal) {
      const payload =
        c.dataType === "tree"
          ? treeOut(local.prepare("SELECT * FROM tree WHERE id = ?").get(c.id))
          : logOut(local.prepare("SELECT * FROM work_log WHERE id = ?").get(c.id));
      if (payload) {
        keepLocalItems.push({
          dataType: c.dataType,
          id: c.id,
          operate: "update",
          updateTime: c.localUpdateTime,
          payload,
        });
      }
    } else if (c.dataType === "tree") {
      remoteTrees.push(c.remote);
    } else {
      remoteLogs.push(c.remote);
    }
  }

  let pushed = 0;
  if (keepLocalItems.length) {
    const resp = await fetch(`${base}/api/sync/push`, {
      method: "POST",
      headers,
      body: JSON.stringify({ changes: keepLocalItems, force: true }),
    });
    if (!resp.ok) throw new Error("覆盖云端失败：HTTP " + resp.status);
    pushed = (await resp.json()).applied || 0;
    for (const item of keepLocalItems) markSynced(local, item.dataType, item.id);
  }

  const pulled = applyRemote(local, remoteTrees, remoteLogs, true);
  pendingConflicts = [];
  return {
    pushed,
    pulled,
    failed: 0,
    conflicts: [],
    message: `冲突已处理：覆盖云端 ${pushed} 条，采用云端 ${pulled} 条`,
  };
}

/* ============================== 其它命令 ============================== */

async function testConnection() {
  const cfg = loadConfig();
  if (!cfg.serverUrl) throw new Error("请先填写云服务器地址");
  const resp = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/ping", {
    headers: { "X-Api-Token": cfg.token },
  });
  if (resp.status === 401) throw new Error("Token 校验失败，请检查 API Token");
  if (!resp.ok) throw new Error("服务器返回异常状态：" + resp.status);
  const body = await resp.json();
  return `连接成功，服务端版本 ${body.version || "unknown"}`;
}

async function netStatus() {
  const cfg = loadConfig();
  if (!cfg.serverUrl) return { online: true, reachable: false };
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 3000);
    const resp = await fetch(cfg.serverUrl.replace(/\/$/, "") + "/api/ping", {
      headers: { "X-Api-Token": cfg.token },
      signal: ctl.signal,
    });
    clearTimeout(timer);
    return { online: true, reachable: resp.ok };
  } catch {
    return { online: true, reachable: false };
  }
}

function backupDb() {
  const dir = path.join(DATA_DIR, "backups");
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, `notebook-${Math.floor(Date.now() / 1000)}.db`);
  fs.copyFileSync(path.join(DATA_DIR, "notebook.db"), target);
  const files = fs.readdirSync(dir).filter((f) => f.startsWith("notebook-")).sort();
  while (files.length > 5) fs.unlinkSync(path.join(dir, files.shift()));
  return target;
}

/* ============================== 模拟云端（接口与 Axum 服务一致） ============================== */

function cloudPing() {
  return { ok: true, version: "1.0.0-preview", serverTime: now() };
}

function cloudTreeList() {
  return cloud
    .prepare("SELECT * FROM tree WHERE is_deleted = 0 ORDER BY sort ASC, create_time ASC")
    .all()
    .map(treeOut);
}

function cloudLogList(treeId) {
  const rows = treeId
    ? cloud.prepare("SELECT * FROM work_log WHERE is_deleted = 0 AND tree_id = ? ORDER BY update_time DESC").all(treeId)
    : cloud.prepare("SELECT * FROM work_log WHERE is_deleted = 0 ORDER BY update_time DESC LIMIT 500").all();
  return rows.map(logOut);
}

function cloudUpsertTree(t) {
  cloud
    .prepare(
      `INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status)
       VALUES (?,?,?,?,?,?,?,'synced')
       ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, name=excluded.name, sort=excluded.sort,
         update_time=excluded.update_time, is_deleted=excluded.is_deleted, sync_status='synced'`
    )
    .run(t.id, t.parentId ?? null, t.name, t.sort, t.createTime, t.updateTime, t.isDeleted ?? 0);
}

function cloudUpsertLog(l) {
  cloud
    .prepare(
      `INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status)
       VALUES (?,?,?,?,?,?,?,'synced')
       ON CONFLICT(id) DO UPDATE SET tree_id=excluded.tree_id, title=excluded.title, content=excluded.content,
         update_time=excluded.update_time, is_deleted=excluded.is_deleted, sync_status='synced'`
    )
    .run(l.id, l.treeId ?? null, l.title, l.content ?? "", l.createTime, l.updateTime, l.isDeleted ?? 0);
}

function cloudPush(body) {
  const { changes = [], force = false } = body;
  let applied = 0;
  const conflicts = [];

  for (const ch of changes) {
    if (!ch.payload) continue;
    const table = ch.dataType === "tree" ? "tree" : "work_log";
    if (ch.dataType !== "tree" && ch.dataType !== "log") continue;

    const cur = cloud.prepare(`SELECT * FROM ${table} WHERE id = ?`).get(ch.id);
    if (cur && !force && cur.update_time > ch.updateTime) {
      const remote = ch.dataType === "tree" ? treeOut(cur) : logOut(cur);
      conflicts.push({
        dataType: ch.dataType,
        id: ch.id,
        remoteUpdateTime: cur.update_time,
        remote,
      });
      continue;
    }

    if (ch.dataType === "tree") cloudUpsertTree(ch.payload);
    else cloudUpsertLog(ch.payload);
    applied++;
  }
  return { applied, conflicts };
}

function cloudPull(since) {
  return {
    serverTime: now(),
    trees: cloud
      .prepare("SELECT * FROM tree WHERE update_time > ? ORDER BY update_time ASC")
      .all(since)
      .map(treeOut),
    logs: cloud
      .prepare("SELECT * FROM work_log WHERE update_time > ? ORDER BY update_time ASC")
      .all(since)
      .map(logOut),
  };
}

/* ============================== 导入 / 导出 / 外观 ============================== */

function exportData() {
  const trees = local.prepare("SELECT * FROM tree ORDER BY sort ASC, create_time ASC").all().map(treeOut);
  const logs = local.prepare("SELECT * FROM work_log ORDER BY update_time ASC").all().map(logOut);
  return {
    format: "worklog-backup",
    version: 1,
    app: "工作日志笔记本",
    exportedAt: now(),
    trees,
    logs,
  };
}

function importData({ payload, mode = "merge" }) {
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.trees) || !Array.isArray(payload.logs)) {
    throw new Error("备份文件格式不正确");
  }
  const replace = mode === "replace";
  const tsNow = now();
  let nTree = 0;
  let nLog = 0;

  if (replace) {
    local.prepare("DELETE FROM change_log").run();
    local.prepare("DELETE FROM work_log").run();
    local.prepare("DELETE FROM tree").run();
  }

  for (const t of payload.trees) {
    if (!t || !t.id || !t.name) continue;
    const id = String(t.id);
    const cur = local.prepare("SELECT update_time FROM tree WHERE id = ?").get(id);
    const ut = Number(t.updateTime) || tsNow;
    if (cur && !replace && cur.update_time >= ut) continue;
    local
      .prepare(
        `INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status)
         VALUES (?,?,?,?,?,?,?,'unsynced')
         ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, name=excluded.name, sort=excluded.sort,
           create_time=excluded.create_time, update_time=excluded.update_time,
           is_deleted=excluded.is_deleted, sync_status='unsynced'`
      )
      .run(id, t.parentId ?? null, String(t.name), Number(t.sort) || 0, Number(t.createTime) || ut, ut, t.isDeleted ? 1 : 0);
    recordChange(local, "tree", id, cur ? "update" : "add");
    nTree += 1;
  }

  for (const lg of payload.logs) {
    if (!lg || !lg.id) continue;
    const id = String(lg.id);
    const cur = local.prepare("SELECT update_time FROM work_log WHERE id = ?").get(id);
    const ut = Number(lg.updateTime) || tsNow;
    if (cur && !replace && cur.update_time >= ut) continue;
    local
      .prepare(
        `INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status)
         VALUES (?,?,?,?,?,?,?,'unsynced')
         ON CONFLICT(id) DO UPDATE SET tree_id=excluded.tree_id, title=excluded.title, content=excluded.content,
           create_time=excluded.create_time, update_time=excluded.update_time,
           is_deleted=excluded.is_deleted, sync_status='unsynced'`
      )
      .run(
        id,
        lg.treeId ?? null,
        String(lg.title || ""),
        String(lg.content || ""),
        Number(lg.createTime) || ut,
        ut,
        lg.isDeleted ? 1 : 0
      );
    recordChange(local, "log", id, cur ? "update" : "add");
    nLog += 1;
  }

  return { trees: nTree, logs: nLog, mode: replace ? "replace" : "merge" };
}

const IMAGE_EXT = { jpeg: "jpg", jpg: "jpg", png: "png", webp: "webp", gif: "gif" };

function saveBackground({ dataUrl }) {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:image/")) throw new Error("图片数据无效");
  const idx = dataUrl.indexOf(",");
  const head = idx >= 0 ? dataUrl.slice(0, idx) : "";
  const b64 = idx >= 0 ? dataUrl.slice(idx + 1) : "";
  const m = /^data:image\/([a-zA-Z0-9+.-]+);base64/.exec(head);
  if (!m || !b64) throw new Error("仅支持 base64 编码的图片数据");
  const ext = IMAGE_EXT[m[1].toLowerCase()] || "png";
  const buf = Buffer.from(b64, "base64");
  if (!buf.length) throw new Error("图片内容为空");
  if (buf.length > 12 * 1024 * 1024) throw new Error("图片过大，请选择 12MB 以内的图片");

  fs.mkdirSync(MEDIA_DIR, { recursive: true });
  for (const f of fs.readdirSync(MEDIA_DIR)) {
    if (f.startsWith("bg.")) {
      try {
        fs.unlinkSync(path.join(MEDIA_DIR, f));
      } catch {
        /* ignore */
      }
    }
  }
  fs.writeFileSync(path.join(MEDIA_DIR, `bg.${ext}`), buf);
  return `/media/bg.${ext}?v=${Date.now()}`;
}

function clearBackground() {
  if (fs.existsSync(MEDIA_DIR)) {
    for (const f of fs.readdirSync(MEDIA_DIR)) {
      if (f.startsWith("bg.")) {
        try {
          fs.unlinkSync(path.join(MEDIA_DIR, f));
        } catch {
          /* ignore */
        }
      }
    }
  }
  return { ok: true };
}

function openDataDir() {
  // 浏览器预览模式没有原生资源管理器，返回路径让界面提示用户
  return DATA_DIR;
}

/* ============================== HTTP 路由 ============================== */

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(body);
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

function serveStatic(res, urlPath) {
  if (!fs.existsSync(DIST)) {
    res.writeHead(503, { "Content-Type": "text/html; charset=utf-8" });
    res.end(
      `<meta charset="utf-8"><div style="font-family:system-ui;padding:40px;line-height:2">
       <h2>前端尚未构建</h2>
       <p>请先执行：<code>cd client &amp;&amp; npm install &amp;&amp; npm run build</code></p>
       <p>或开发模式：<code>cd client &amp;&amp; npm run dev</code></p></div>`
    );
    return;
  }
  let rel = decodeURIComponent(urlPath.split("?")[0]);
  if (rel === "/") rel = "/index.html";
  let file = path.join(DIST, rel);
  if (!file.startsWith(DIST)) file = path.join(DIST, "index.html");
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, "index.html");
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}

const COMMANDS = {
  list_trees: () => listTrees(),
  create_tree: (a) => createTree(a),
  rename_tree: (a) => renameTree(a),
  delete_tree: (a) => deleteTree(a),
  reorder_trees: (a) => reorderTrees(a),
  list_logs: (a) => listLogs(a),
  get_log: (a) => getLog(a),
  create_log: (a) => createLog(a),
  update_log: (a) => updateLog(a),
  delete_log: (a) => deleteLog(a),
  search_logs: (a) => searchLogs(a),
  get_config: () => loadConfig(),
  save_config: ({ config }) => {
    const cur = loadConfig();
    const merged = {
      ...config,
      lastSyncAt: cur.lastSyncAt,
      theme: normTheme(config && config.theme),
      shortcuts: normShortcuts(config && config.shortcuts),
    };
    saveConfigFile(merged);
    return merged;
  },
  pending_changes: () => pendingCount(local),
  app_info: () => ({
    dbPath: path.join(DATA_DIR, "notebook.db"),
    backupDir: path.join(DATA_DIR, "backups"),
    desktop: false,
  }),
  backup_db: () => backupDb(),
  net_status: () => netStatus(),
  test_connection: () => testConnection(),
  sync_now: () => syncNow(),
  resolve_conflicts: (a) => resolveConflicts(a),
  export_data: () => exportData(),
  import_data: (a) => importData(a),
  save_background: (a) => saveBackground(a),
  clear_background: () => clearBackground(),
  open_data_dir: () => openDataDir(),
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  // 模拟云端：/cloud/api/*
  if (url.pathname.startsWith("/cloud/api/")) {
    const token = req.headers["x-api-token"];
    if (token !== CLOUD_TOKEN) return json(res, 401, { error: "unauthorized" });
    const p = url.pathname.slice("/cloud/api".length);
    try {
      if (p === "/ping") return json(res, 200, cloudPing());
      if (p === "/tree/list") return json(res, 200, cloudTreeList());
      if (p === "/log/list") return json(res, 200, cloudLogList(url.searchParams.get("treeId")));
      if (p === "/sync/push" && req.method === "POST") return json(res, 200, cloudPush(await readBody(req)));
      if (p === "/sync/pull") return json(res, 200, cloudPull(Number(url.searchParams.get("since") || 0)));
      return json(res, 404, { error: "not found" });
    } catch (e) {
      return json(res, 500, { error: e.message });
    }
  }

  // 客户端命令
  if (url.pathname.startsWith("/api/cmd/")) {
    const cmd = url.pathname.slice("/api/cmd/".length);
    const fn = COMMANDS[cmd];
    if (!fn) return json(res, 404, { error: `未知命令：${cmd}` });
    try {
      const args = await readBody(req);
      const result = await fn(args || {});
      return json(res, 200, result === undefined ? null : result);
    } catch (e) {
      return json(res, 500, { error: e.message || String(e) });
    }
  }

  if (url.pathname === "/api/health") return json(res, 200, { ok: true });

  // 外观资源（背景图片）
  if (url.pathname.startsWith("/media/")) {
    const name = path.basename(decodeURIComponent(url.pathname));
    const file = path.join(MEDIA_DIR, name);
    if (!name || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      return res.end("not found");
    }
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream",
      "Cache-Control": "max-age=86400",
    });
    return fs.createReadStream(file).pipe(res);
  }

  serveStatic(res, url.pathname);
});

/* ============================== 首次运行填充演示数据 ============================== */

function seedIfEmpty() {
  const n = local.prepare("SELECT COUNT(*) AS n FROM tree").get().n;
  if (n > 0) return;

  const d = new Date();
  const pad = (x) => String(x).padStart(2, "0");
  const today = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const yesterday = new Date(d.getTime() - 86400000);
  const ymd = `${yesterday.getFullYear()}-${pad(yesterday.getMonth() + 1)}-${pad(yesterday.getDate())}`;

  const mk = (parentId, name, sort) => {
    const ts = now() - sort * 1000;
    const id = uuid();
    local
      .prepare(
        "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) VALUES (?,?,?,?,?,?,0,'unsynced')"
      )
      .run(id, parentId, name, sort, ts, ts);
    return id;
  };

  const a = mk(null, `${today} 项目A 巡检`, 10);
  const b = mk(null, `${ymd} 数据库运维`, 20);
  const c = mk(null, `${today} 日常任务`, 30);
  mk(a, `${today} 应用服务器巡检`, 10);
  mk(a, `${today} 网关流量排查`, 20);

  const log = (treeId, title, content) => {
    const ts = now();
    local
      .prepare(
        "INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status) VALUES (?,?,?,?,?,?,0,'unsynced')"
      )
      .run(uuid(), treeId, title, content, ts, ts);
  };

  log(
    a,
    "巡检记录：应用集群状态正常",
    `# 巡检记录\n\n## 基本信息\n\n- **时间**：今日 09:30 - 10:15\n- **范围**：应用服务器 4 台、网关 2 台\n- **结论**：整体正常\n\n## 检查项\n\n| 检查项 | 结果 | 备注 |\n| --- | --- | --- |\n| CPU 使用率 | 正常 | 峰值 38% |\n| 内存使用率 | 正常 | 峰值 61% |\n| 磁盘空间 | 关注 | /data 剩余 18% |\n\n## 待办\n\n- [ ] 清理 /data 下 30 天前的日志文件\n- [ ] 补充磁盘告警阈值配置\n\n> 磁盘容量需要本周内处理，已同步给运维组。`
  );
  log(
    a,
    "网关流量异常排查",
    `# 网关流量异常排查\n\n## 现象\n\n昨日下午 15:20 起，网关出口流量突增 3 倍，伴随部分接口超时。\n\n## 排查过程\n\n1. 查看网关监控，定位到某个业务 IP 持续大流量；\n2. 抓包确认是重复调用导致的重试风暴；\n3. 上游服务超时配置不合理（默认 60s）。\n\n## 处理\n\n\`\`\`bash\n# 调整上游超时与重试次数\nupstream_timeout = 10s\nmax_retries = 2\n\`\`\`\n\n## 结论\n\n调整后流量恢复正常，需推动业务侧增加幂等处理。`
  );
  log(
    b,
    "MySQL 慢查询优化",
    `# 慢查询优化\n\n发现一条 8s 的慢查询，缺少联合索引。\n\n\`\`\`sql\nALTER TABLE work_log ADD INDEX idx_tree_update (tree_id, update_time);\n\`\`\`\n\n优化后降到 30ms。`
  );
  log(c, "今日任务清单", `# 今日任务\n\n- [x] 巡检记录整理\n- [ ] 备份策略评审\n- [ ] 周报草稿`);

  // 演示数据先标记为已同步，便于体验「同步」流程
  local.prepare("UPDATE tree SET sync_status='synced'").run();
  local.prepare("UPDATE work_log SET sync_status='synced'").run();
  local.prepare("DELETE FROM change_log").run();

  // 云端预置相同数据（模拟另一台设备已同步过的状态）
  for (const t of local.prepare("SELECT * FROM tree").all()) cloudUpsertTree(treeOut(t));
  for (const l of local.prepare("SELECT * FROM work_log").all()) cloudUpsertLog(logOut(l));

  const cfg = loadConfig();
  cfg.lastSyncAt = now();
  saveConfigFile(cfg);

  console.log("已填充演示数据");
}

seedIfEmpty();

server.listen(PORT, "127.0.0.1", () => {
  console.log(`\n  工作日志笔记本 · 本地预览已启动`);
  console.log(`  打开浏览器访问：http://127.0.0.1:${PORT}`);
  console.log(`  本地数据库：${path.join(DATA_DIR, "notebook.db")}`);
  console.log(`  模拟云端：http://127.0.0.1:${PORT}/cloud（Token: ${CLOUD_TOKEN}）\n`);
});
