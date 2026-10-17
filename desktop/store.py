"""本地数据存储与业务逻辑（离线核心）。

对应设计书第 3.3 / 3.4 节：
- 本地 SQLite 是唯一主数据源，所有 CRUD 直接落库，不依赖网络；
- change_log 记录每一条离线变更，同步时只推送变更数据；
- 同步成功后删除对应 change_log，失败则保留等待下次重试。
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

ARCHIVE_NAME = "未分类（归档）"


def now_ms() -> int:
    return int(time.time() * 1000)


SCHEMA = """
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
"""

DEFAULT_THEME = {
    "mode": "light",  # light | dark
    "bgImage": "",  # 自定义背景图（/media/xxx）
    "bgOpacity": 0.6,  # 背景透明度 0~1：越大背景越清晰
}

DEFAULT_SHORTCUTS = {
    "newLog": "Ctrl+N",
    "newTree": "Ctrl+Shift+N",
    "save": "Ctrl+S",
    "sync": "F5",
    "search": "Ctrl+F",
    "settings": "Ctrl+,",
    "toggleTheme": "Ctrl+Shift+L",
    "exportData": "Ctrl+E",
}

DEFAULT_CONFIG = {
    "serverUrl": "",
    "token": "",
    "autoSave": True,
    "autoDatePrefix": True,
    "lastSyncAt": 0,
    "theme": DEFAULT_THEME,
    "shortcuts": DEFAULT_SHORTCUTS,
}

IMAGE_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}
MAX_BG_BYTES = 12 * 1024 * 1024


def _norm_theme(raw) -> dict:
    """与前端 theme.js 的 normalizeTheme 保持一致。"""
    src = raw if isinstance(raw, dict) else {}
    try:
        opacity = float(src.get("bgOpacity", DEFAULT_THEME["bgOpacity"]))
    except (TypeError, ValueError):
        opacity = DEFAULT_THEME["bgOpacity"]
    return {
        "mode": "dark" if src.get("mode") == "dark" else "light",
        "bgImage": src.get("bgImage") if isinstance(src.get("bgImage"), str) else "",
        "bgOpacity": min(1.0, max(0.0, opacity)),
    }


def _norm_shortcuts(raw) -> dict:
    out = dict(DEFAULT_SHORTCUTS)
    if isinstance(raw, dict):
        for k in DEFAULT_SHORTCUTS:
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
    return out


def tree_out(row) -> dict:
    return {
        "id": row["id"],
        "parentId": row["parent_id"],
        "name": row["name"],
        "sort": row["sort"],
        "createTime": row["create_time"],
        "updateTime": row["update_time"],
        "isDeleted": row["is_deleted"],
        "syncStatus": row["sync_status"],
    }


def log_out(row) -> dict:
    return {
        "id": row["id"],
        "treeId": row["tree_id"],
        "title": row["title"],
        "content": row["content"] or "",
        "createTime": row["create_time"],
        "updateTime": row["update_time"],
        "isDeleted": row["is_deleted"],
        "syncStatus": row["sync_status"],
    }


class ApiError(Exception):
    """业务异常，直接展示给用户"""


class Store:
    def __init__(self, data_dir: str, seed_demo: bool = False):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.backup_dir = os.path.join(data_dir, "backups")
        self.media_dir = os.path.join(data_dir, "media")
        self.db_path = os.path.join(data_dir, "notebook.db")
        self.config_path = os.path.join(data_dir, "config.json")

        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

        self.pending_conflicts: list[dict] = []
        if seed_demo and self._is_empty():
            self._seed_demo()

    # ------------------------------------------------------------------ 基础

    def _is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM tree").fetchone()[0] == 0

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------ 变更记录

    def _record(self, data_type: str, data_id: str, operate: str) -> None:
        """记录一条变更；同一数据多次修改只保留最后一条，避免重复推送。"""
        self.conn.execute(
            "DELETE FROM change_log WHERE data_type = ? AND data_id = ?", (data_type, data_id)
        )
        self.conn.execute(
            "INSERT INTO change_log (id, data_type, data_id, operate, operate_time) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), data_type, data_id, operate, now_ms()),
        )

    def pending_count(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM change_log").fetchone()[0]

    def _has_pending(self, data_type: str, data_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT COUNT(*) FROM change_log WHERE data_type = ? AND data_id = ?",
                (data_type, data_id),
            ).fetchone()[0]
            > 0
        )

    def _mark_synced(self, data_type: str, data_id: str) -> None:
        table = "tree" if data_type == "tree" else "work_log"
        self.conn.execute(f"UPDATE {table} SET sync_status = 'synced' WHERE id = ?", (data_id,))
        self.conn.execute(
            "DELETE FROM change_log WHERE data_type = ? AND data_id = ?", (data_type, data_id)
        )

    # ---------------------------------------------------------------- 配置

    def load_config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
        cfg["theme"] = _norm_theme(cfg.get("theme"))
        cfg["shortcuts"] = _norm_shortcuts(cfg.get("shortcuts"))
        return cfg

    def save_config(self, cfg: dict) -> dict:
        merged = dict(DEFAULT_CONFIG)
        merged.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG})
        # lastSyncAt 由同步流程维护，不允许前端覆盖
        merged["lastSyncAt"] = self.load_config().get("lastSyncAt", 0)
        merged["theme"] = _norm_theme(merged.get("theme"))
        merged["shortcuts"] = _norm_shortcuts(merged.get("shortcuts"))
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        return merged

    def _set_last_sync(self, ts: int) -> None:
        cfg = self.load_config()
        cfg["lastSyncAt"] = ts
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ 目录分支

    def list_trees(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM tree WHERE is_deleted = 0 ORDER BY sort ASC, create_time ASC"
            ).fetchall()
            return [tree_out(r) for r in rows]

    def create_tree(self, parent_id: str | None, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ApiError("分支名称不能为空")
        with self._lock:
            if parent_id:
                row = self.conn.execute(
                    "SELECT id FROM tree WHERE id = ? AND is_deleted = 0", (parent_id,)
                ).fetchone()
                if not row:
                    raise ApiError("父分支不存在")

            ts = now_ms()
            tid = str(uuid.uuid4())
            sort = self.conn.execute(
                "SELECT COALESCE(MAX(sort),0)+10 FROM tree "
                "WHERE is_deleted = 0 AND ((parent_id IS NULL AND ? IS NULL) OR parent_id = ?)",
                (parent_id, parent_id),
            ).fetchone()[0]

            self.conn.execute(
                "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) "
                "VALUES (?,?,?,?,?,?,0,'unsynced')",
                (tid, parent_id, name, sort, ts, ts),
            )
            self._record("tree", tid, "add")
            self.conn.commit()
            return tree_out(self.conn.execute("SELECT * FROM tree WHERE id = ?", (tid,)).fetchone())

    def rename_tree(self, tid: str, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ApiError("分支名称不能为空")
        with self._lock:
            cur = self.conn.execute(
                "UPDATE tree SET name = ?, update_time = ?, sync_status = 'unsynced' "
                "WHERE id = ? AND is_deleted = 0",
                (name, now_ms(), tid),
            )
            if cur.rowcount == 0:
                raise ApiError("分支不存在")
            self._record("tree", tid, "update")
            self.conn.commit()
            return tree_out(self.conn.execute("SELECT * FROM tree WHERE id = ?", (tid,)).fetchone())

    def _collect_subtree(self, root_id: str) -> list[str]:
        ids = [root_id]
        i = 0
        while i < len(ids):
            children = self.conn.execute(
                "SELECT id FROM tree WHERE parent_id = ? AND is_deleted = 0", (ids[i],)
            ).fetchall()
            for c in children:
                if c["id"] not in ids:
                    ids.append(c["id"])
            i += 1
        return ids

    def _ensure_archive(self) -> str:
        row = self.conn.execute(
            "SELECT id FROM tree WHERE name = ? AND parent_id IS NULL AND is_deleted = 0",
            (ARCHIVE_NAME,),
        ).fetchone()
        if row:
            return row["id"]
        ts = now_ms()
        tid = str(uuid.uuid4())
        sort = self.conn.execute(
            "SELECT COALESCE(MAX(sort),0)+10 FROM tree WHERE parent_id IS NULL AND is_deleted = 0"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) "
            "VALUES (?,NULL,?,?,?,?,0,'unsynced')",
            (tid, ARCHIVE_NAME, sort, ts, ts),
        )
        self._record("tree", tid, "add")
        return tid

    def delete_tree(self, tid: str, delete_logs: bool = True) -> dict:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tree WHERE id = ?", (tid,)).fetchone()
            if not row:
                raise ApiError("分支不存在")

            ids = self._collect_subtree(tid)
            ts = now_ms()
            # 不删除日志时，日志迁移到上一级；顶级分支则归入归档节点
            dest = None if delete_logs else (row["parent_id"] or self._ensure_archive())

            for t in ids:
                logs = self.conn.execute(
                    "SELECT id FROM work_log WHERE tree_id = ? AND is_deleted = 0", (t,)
                ).fetchall()
                for lg in logs:
                    if dest:
                        self.conn.execute(
                            "UPDATE work_log SET tree_id = ?, update_time = ?, sync_status = 'unsynced' "
                            "WHERE id = ?",
                            (dest, ts, lg["id"]),
                        )
                        self._record("log", lg["id"], "update")
                    else:
                        self.conn.execute(
                            "UPDATE work_log SET is_deleted = 1, update_time = ?, sync_status = 'unsynced' "
                            "WHERE id = ?",
                            (ts, lg["id"]),
                        )
                        self._record("log", lg["id"], "delete")

            for t in ids:
                self.conn.execute(
                    "UPDATE tree SET is_deleted = 1, update_time = ?, sync_status = 'unsynced' WHERE id = ?",
                    (ts, t),
                )
                self._record("tree", t, "delete")

            self.conn.commit()
            return {"ok": True}

    def reorder_trees(self, updates: list[dict]) -> dict:
        with self._lock:
            ts = now_ms()
            for u in updates or []:
                self.conn.execute(
                    "UPDATE tree SET parent_id = ?, sort = ?, update_time = ?, sync_status = 'unsynced' "
                    "WHERE id = ?",
                    (u.get("parentId"), int(u.get("sort") or 0), ts, u.get("id")),
                )
                self._record("tree", u.get("id"), "update")
            self.conn.commit()
            return {"ok": True}

    # ---------------------------------------------------------------- 日志

    def list_logs(self, tree_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, tree_id, title, substr(content,1,240) AS preview, "
                "create_time, update_time, sync_status "
                "FROM work_log WHERE tree_id = ? AND is_deleted = 0 ORDER BY update_time DESC",
                (tree_id,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "treeId": r["tree_id"],
                    "title": r["title"],
                    "preview": r["preview"] or "",
                    "createTime": r["create_time"],
                    "updateTime": r["update_time"],
                    "syncStatus": r["sync_status"],
                }
                for r in rows
            ]

    def get_log(self, log_id: str) -> dict:
        with self._lock:
            row = self.conn.execute("SELECT * FROM work_log WHERE id = ?", (log_id,)).fetchone()
            if not row:
                raise ApiError("日志不存在")
            return log_out(row)

    def create_log(self, tree_id: str, title: str, content: str = "") -> dict:
        with self._lock:
            ts = now_ms()
            lid = str(uuid.uuid4())
            t = (title or "").strip() or "未命名日志"
            self.conn.execute(
                "INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status) "
                "VALUES (?,?,?,?,?,?,0,'unsynced')",
                (lid, tree_id, t, content or "", ts, ts),
            )
            self._record("log", lid, "add")
            self.conn.commit()
            return log_out(self.conn.execute("SELECT * FROM work_log WHERE id = ?", (lid,)).fetchone())

    def update_log(self, log_id: str, title: str, content: str = "") -> dict:
        with self._lock:
            t = (title or "").strip() or "未命名日志"
            cur = self.conn.execute(
                "UPDATE work_log SET title = ?, content = ?, update_time = ?, sync_status = 'unsynced' "
                "WHERE id = ? AND is_deleted = 0",
                (t, content or "", now_ms(), log_id),
            )
            if cur.rowcount == 0:
                raise ApiError("日志不存在或已删除")
            self._record("log", log_id, "update")
            self.conn.commit()
            return log_out(self.conn.execute("SELECT * FROM work_log WHERE id = ?", (log_id,)).fetchone())

    def delete_log(self, log_id: str) -> dict:
        with self._lock:
            self.conn.execute(
                "UPDATE work_log SET is_deleted = 1, update_time = ?, sync_status = 'unsynced' WHERE id = ?",
                (now_ms(), log_id),
            )
            self._record("log", log_id, "delete")
            self.conn.commit()
            return {"ok": True}

    def search_logs(self, keyword: str) -> list[dict]:
        like = f"%{(keyword or '').strip()}%"
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, tree_id, title, substr(content,1,240) AS preview, "
                "create_time, update_time, sync_status FROM work_log "
                "WHERE is_deleted = 0 AND (title LIKE ? OR content LIKE ?) "
                "ORDER BY update_time DESC LIMIT 200",
                (like, like),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "treeId": r["tree_id"],
                    "title": r["title"],
                    "preview": r["preview"] or "",
                    "createTime": r["create_time"],
                    "updateTime": r["update_time"],
                    "syncStatus": r["sync_status"],
                }
                for r in rows
            ]

    # ---------------------------------------------------------------- 备份

    def backup(self) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        with self._lock:
            self.conn.commit()
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target = os.path.join(self.backup_dir, f"notebook-{int(time.time())}.db")
            src = sqlite3.connect(self.db_path)
            try:
                dst = sqlite3.connect(target)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()

            files = sorted(
                f for f in os.listdir(self.backup_dir) if f.startswith("notebook-") and f.endswith(".db")
            )
            while len(files) > 5:
                try:
                    os.remove(os.path.join(self.backup_dir, files.pop(0)))
                except OSError:
                    break
        return target

    # ---------------------------------------------------------------- 同步

    def _http(self, url: str, method: str = "GET", token: str = "", body=None, timeout: int = 15):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Api-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ApiError("Token 校验失败，请检查 API Token") from e
            raise ApiError(f"服务器返回异常：HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise ApiError(f"无法连接云服务器：{e.reason}") from e
        except TimeoutError as e:
            raise ApiError("连接云服务器超时") from e

    def _collect_changes(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM change_log ORDER BY operate_time ASC"
        ).fetchall()
        items = []
        for r in rows:
            if r["data_type"] == "tree":
                row = self.conn.execute("SELECT * FROM tree WHERE id = ?", (r["data_id"],)).fetchone()
                payload = tree_out(row) if row else None
            else:
                row = self.conn.execute("SELECT * FROM work_log WHERE id = ?", (r["data_id"],)).fetchone()
                payload = log_out(row) if row else None
            if not payload:
                continue
            items.append(
                {
                    "dataType": r["data_type"],
                    "id": r["data_id"],
                    "operate": r["operate"],
                    "updateTime": payload["updateTime"],
                    "payload": payload,
                }
            )
        return items

    def _apply_remote(self, trees: list[dict], logs: list[dict], force: bool = False) -> int:
        applied = 0
        for t in trees or []:
            cur = self.conn.execute("SELECT * FROM tree WHERE id = ?", (t["id"],)).fetchone()
            if not force and cur and self._has_pending("tree", t["id"]) and cur["update_time"] > t["updateTime"]:
                continue
            self.conn.execute(
                "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) "
                "VALUES (?,?,?,?,?,?,?,'synced') "
                "ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, name=excluded.name, "
                "sort=excluded.sort, update_time=excluded.update_time, is_deleted=excluded.is_deleted, "
                "sync_status='synced'",
                (
                    t["id"],
                    t.get("parentId"),
                    t["name"],
                    t.get("sort", 0),
                    t.get("createTime", 0),
                    t["updateTime"],
                    t.get("isDeleted", 0),
                ),
            )
            self.conn.execute("DELETE FROM change_log WHERE data_type='tree' AND data_id=?", (t["id"],))
            applied += 1

        for lg in logs or []:
            cur = self.conn.execute("SELECT * FROM work_log WHERE id = ?", (lg["id"],)).fetchone()
            if not force and cur and self._has_pending("log", lg["id"]) and cur["update_time"] > lg["updateTime"]:
                continue
            self.conn.execute(
                "INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status) "
                "VALUES (?,?,?,?,?,?,?,'synced') "
                "ON CONFLICT(id) DO UPDATE SET tree_id=excluded.tree_id, title=excluded.title, "
                "content=excluded.content, update_time=excluded.update_time, "
                "is_deleted=excluded.is_deleted, sync_status='synced'",
                (
                    lg["id"],
                    lg.get("treeId"),
                    lg.get("title", ""),
                    lg.get("content", ""),
                    lg.get("createTime", 0),
                    lg["updateTime"],
                    lg.get("isDeleted", 0),
                ),
            )
            self.conn.execute("DELETE FROM change_log WHERE data_type='log' AND data_id=?", (lg["id"],))
            applied += 1
        return applied

    def sync_now(self) -> dict:
        cfg = self.load_config()
        base = (cfg.get("serverUrl") or "").rstrip("/")
        if not base:
            raise ApiError("未配置云服务器地址，请先在设置中填写")
        token = cfg.get("token") or ""

        # 1) 连通性检测（避免网络误判导致半途失败）
        self._http(f"{base}/api/ping", token=token, timeout=8)

        # 2) 推送本地变更
        with self._lock:
            changes = self._collect_changes()

        pushed = 0
        conflicts: list[dict] = []
        if changes:
            result = self._http(
                f"{base}/api/sync/push", method="POST", token=token,
                body={"changes": changes, "force": False},
            )
            pushed = int(result.get("applied") or 0)
            remote_conflicts = result.get("conflicts") or []

            with self._lock:
                conflict_keys = {(c.get("dataType"), c.get("id")) for c in remote_conflicts}
                for ch in changes:
                    if (ch["dataType"], ch["id"]) in conflict_keys:
                        continue
                    self._mark_synced(ch["dataType"], ch["id"])

                for c in remote_conflicts:
                    local_row = (
                        self.conn.execute("SELECT * FROM tree WHERE id = ?", (c["id"],)).fetchone()
                        if c.get("dataType") == "tree"
                        else self.conn.execute("SELECT * FROM work_log WHERE id = ?", (c["id"],)).fetchone()
                    )
                    local = (
                        tree_out(local_row) if c.get("dataType") == "tree" and local_row
                        else (log_out(local_row) if local_row else None)
                    )
                    remote = c.get("remote") or {}
                    conflicts.append(
                        {
                            "dataType": c.get("dataType"),
                            "id": c.get("id"),
                            "localUpdateTime": local["updateTime"] if local else 0,
                            "remoteUpdateTime": c.get("remoteUpdateTime") or remote.get("updateTime", 0),
                            "localName": local.get("name") if local else None,
                            "remoteName": remote.get("name"),
                            "localTitle": local.get("title") if local else None,
                            "remoteTitle": remote.get("title"),
                            "remote": remote,
                        }
                    )
                self.conn.commit()

        # 3) 拉取云端增量
        since = int(cfg.get("lastSyncAt") or 0)
        pulled_data = self._http(f"{base}/api/sync/pull?since={since}", token=token, timeout=30)
        with self._lock:
            pulled = self._apply_remote(
                pulled_data.get("trees") or [], pulled_data.get("logs") or [], force=False
            )
            self.conn.commit()
        self._set_last_sync(int(pulled_data.get("serverTime") or now_ms()))

        self.pending_conflicts = conflicts
        msg = f"上传 {pushed} 条，拉取 {pulled} 条"
        if conflicts:
            msg += f"，{len(conflicts)} 条冲突待处理"
        return {"pushed": pushed, "pulled": pulled, "failed": 0, "conflicts": conflicts, "message": msg}

    def resolve_conflicts(self, choices: list[dict]) -> dict:
        cfg = self.load_config()
        base = (cfg.get("serverUrl") or "").rstrip("/")
        if not base:
            raise ApiError("未配置云服务器地址")
        if not self.pending_conflicts:
            return {"pushed": 0, "pulled": 0, "failed": 0, "conflicts": [], "message": "没有待处理的冲突"}

        token = cfg.get("token") or ""
        keep_local_items = []
        remote_trees, remote_logs = [], []

        for c in self.pending_conflicts:
            choice = next(
                (x for x in choices if x.get("dataType") == c["dataType"] and x.get("id") == c["id"]), None
            )
            keep_local = True if choice is None else bool(choice.get("keepLocal"))

            if keep_local:
                with self._lock:
                    row = (
                        self.conn.execute("SELECT * FROM tree WHERE id = ?", (c["id"],)).fetchone()
                        if c["dataType"] == "tree"
                        else self.conn.execute("SELECT * FROM work_log WHERE id = ?", (c["id"],)).fetchone()
                    )
                if row:
                    payload = tree_out(row) if c["dataType"] == "tree" else log_out(row)
                    keep_local_items.append(
                        {
                            "dataType": c["dataType"],
                            "id": c["id"],
                            "operate": "update",
                            "updateTime": c["localUpdateTime"],
                            "payload": payload,
                        }
                    )
            elif c["dataType"] == "tree":
                remote_trees.append(c["remote"])
            else:
                remote_logs.append(c["remote"])

        pushed = 0
        if keep_local_items:
            result = self._http(
                f"{base}/api/sync/push", method="POST", token=token,
                body={"changes": keep_local_items, "force": True},
            )
            pushed = int(result.get("applied") or 0)
            with self._lock:
                for item in keep_local_items:
                    self._mark_synced(item["dataType"], item["id"])
                self.conn.commit()

        pulled = 0
        if remote_trees or remote_logs:
            with self._lock:
                pulled = self._apply_remote(remote_trees, remote_logs, force=True)
                self.conn.commit()

        self.pending_conflicts = []
        return {
            "pushed": pushed,
            "pulled": pulled,
            "failed": 0,
            "conflicts": [],
            "message": f"冲突已处理：覆盖云端 {pushed} 条，采用云端 {pulled} 条",
        }

    def test_connection(self) -> str:
        cfg = self.load_config()
        base = (cfg.get("serverUrl") or "").rstrip("/")
        if not base:
            raise ApiError("请先填写云服务器地址")
        body = self._http(f"{base}/api/ping", token=cfg.get("token") or "", timeout=8)
        return f"连接成功，服务端版本 {body.get('version', 'unknown')}"

    def net_status(self) -> dict:
        cfg = self.load_config()
        base = (cfg.get("serverUrl") or "").rstrip("/")
        if not base:
            return {"online": True, "reachable": False}
        try:
            self._http(f"{base}/api/ping", token=cfg.get("token") or "", timeout=4)
            return {"online": True, "reachable": True}
        except ApiError:
            return {"online": True, "reachable": False}

    # ------------------------------------------------------- 导入 / 导出

    def export_data(self) -> dict:
        """导出全部分支与日志（含软删除标记），用于备份或迁移到另一台电脑。"""
        with self._lock:
            trees = [
                tree_out(r)
                for r in self.conn.execute("SELECT * FROM tree ORDER BY sort ASC, create_time ASC").fetchall()
            ]
            logs = [
                log_out(r)
                for r in self.conn.execute("SELECT * FROM work_log ORDER BY update_time ASC").fetchall()
            ]
        return {
            "format": "worklog-backup",
            "version": 1,
            "app": "工作日志笔记本",
            "exportedAt": now_ms(),
            "trees": trees,
            "logs": logs,
        }

    def import_data(self, payload, mode: str = "merge") -> dict:
        """导入备份。mode=merge 按 ID 合并（更新时间新者优先），mode=replace 先清空。"""
        if not isinstance(payload, dict):
            raise ApiError("备份文件格式不正确")
        trees = payload.get("trees")
        logs = payload.get("logs")
        if not isinstance(trees, list) or not isinstance(logs, list):
            raise ApiError("备份文件缺少 trees / logs 字段")

        replace = mode == "replace"
        n_tree = 0
        n_log = 0
        ts_now = now_ms()

        with self._lock:
            if replace:
                self.conn.execute("DELETE FROM change_log")
                self.conn.execute("DELETE FROM work_log")
                self.conn.execute("DELETE FROM tree")

            for t in trees:
                if not isinstance(t, dict) or not t.get("id") or not t.get("name"):
                    continue
                tid = str(t["id"])
                cur = self.conn.execute("SELECT update_time FROM tree WHERE id = ?", (tid,)).fetchone()
                ut = int(t.get("updateTime") or ts_now)
                if cur and not replace and cur["update_time"] >= ut:
                    continue
                self.conn.execute(
                    "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) "
                    "VALUES (?,?,?,?,?,?,?,'unsynced') "
                    "ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, name=excluded.name, "
                    "sort=excluded.sort, create_time=excluded.create_time, update_time=excluded.update_time, "
                    "is_deleted=excluded.is_deleted, sync_status='unsynced'",
                    (
                        tid,
                        t.get("parentId"),
                        str(t["name"]),
                        int(t.get("sort") or 0),
                        int(t.get("createTime") or ut),
                        ut,
                        1 if t.get("isDeleted") else 0,
                    ),
                )
                self._record("tree", tid, "add" if not cur else "update")
                n_tree += 1

            for lg in logs:
                if not isinstance(lg, dict) or not lg.get("id"):
                    continue
                lid = str(lg["id"])
                cur = self.conn.execute("SELECT update_time FROM work_log WHERE id = ?", (lid,)).fetchone()
                ut = int(lg.get("updateTime") or ts_now)
                if cur and not replace and cur["update_time"] >= ut:
                    continue
                self.conn.execute(
                    "INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status) "
                    "VALUES (?,?,?,?,?,?,?,'unsynced') "
                    "ON CONFLICT(id) DO UPDATE SET tree_id=excluded.tree_id, title=excluded.title, "
                    "content=excluded.content, create_time=excluded.create_time, update_time=excluded.update_time, "
                    "is_deleted=excluded.is_deleted, sync_status='unsynced'",
                    (
                        lid,
                        lg.get("treeId"),
                        str(lg.get("title") or ""),
                        str(lg.get("content") or ""),
                        int(lg.get("createTime") or ut),
                        ut,
                        1 if lg.get("isDeleted") else 0,
                    ),
                )
                self._record("log", lid, "add" if not cur else "update")
                n_log += 1

            self.conn.commit()

        return {"trees": n_tree, "logs": n_log, "mode": "replace" if replace else "merge"}

    # ------------------------------------------------------------ 外观资源

    def save_background(self, data_url: str) -> str:
        """把前端选中的图片（dataURL）写入 media 目录，返回可直接访问的 URL。"""
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            raise ApiError("图片数据无效")
        head, _, b64 = data_url.partition(",")
        m = re.match(r"data:image/([a-zA-Z0-9+.\-]+);base64", head or "")
        if not m or not b64:
            raise ApiError("仅支持 base64 编码的图片数据")
        ext = IMAGE_EXT.get(m.group(1).lower(), "png")
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as e:
            raise ApiError(f"图片解码失败：{e}") from e
        if not raw:
            raise ApiError("图片内容为空")
        if len(raw) > MAX_BG_BYTES:
            raise ApiError("图片过大，请选择 12MB 以内的图片")

        with self._lock:
            os.makedirs(self.media_dir, exist_ok=True)
            for f in os.listdir(self.media_dir):
                if f.startswith("bg."):
                    try:
                        os.remove(os.path.join(self.media_dir, f))
                    except OSError:
                        pass
            name = f"bg.{ext}"
            with open(os.path.join(self.media_dir, name), "wb") as fh:
                fh.write(raw)

        return f"/media/{name}?v={int(time.time() * 1000)}"

    def clear_background(self) -> dict:
        with self._lock:
            if os.path.isdir(self.media_dir):
                for f in os.listdir(self.media_dir):
                    if f.startswith("bg."):
                        try:
                            os.remove(os.path.join(self.media_dir, f))
                        except OSError:
                            pass
        return {"ok": True}

    def open_data_dir(self) -> str:
        """在资源管理器中打开数据目录（便携版就是程序旁边的 data 文件夹）。"""
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(self.data_dir)  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", self.data_dir])
        except Exception as e:
            raise ApiError(f"无法打开目录：{e}") from e
        return self.data_dir

    # ------------------------------------------------------------ 演示数据

    def _seed_demo(self) -> None:
        """首次启动填充少量示例内容，方便上手（可在界面里直接删除）。"""
        from datetime import date, timedelta

        today = date.today()
        ymd = today.isoformat()
        prev = (today - timedelta(days=1)).isoformat()

        def mk(parent, name, sort):
            ts = now_ms() - sort * 1000
            tid = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO tree (id,parent_id,name,sort,create_time,update_time,is_deleted,sync_status) "
                "VALUES (?,?,?,?,?,?,0,'synced')",
                (tid, parent, name, sort, ts, ts),
            )
            return tid

        a = mk(None, f"{ymd} 项目A 巡检", 10)
        b = mk(None, f"{prev} 数据库运维", 20)
        c = mk(None, f"{ymd} 日常任务", 30)
        mk(a, f"{ymd} 应用服务器巡检", 10)
        mk(a, f"{ymd} 网关流量排查", 20)

        def log(tree, title, content):
            ts = now_ms()
            self.conn.execute(
                "INSERT INTO work_log (id,tree_id,title,content,create_time,update_time,is_deleted,sync_status) "
                "VALUES (?,?,?,?,?,?,0,'synced')",
                (str(uuid.uuid4()), tree, title, content, ts, ts),
            )

        log(
            a,
            "巡检记录：应用集群状态正常",
            "# 巡检记录\n\n## 基本信息\n\n- **时间**：今日 09:30 - 10:15\n"
            "- **范围**：应用服务器 4 台、网关 2 台\n- **结论**：整体正常\n\n"
            "## 检查项\n\n| 检查项 | 结果 | 备注 |\n| --- | --- | --- |\n"
            "| CPU 使用率 | 正常 | 峰值 38% |\n| 内存使用率 | 正常 | 峰值 61% |\n"
            "| 磁盘空间 | 关注 | /data 剩余 18% |\n\n## 待办\n\n"
            "- [ ] 清理 /data 下 30 天前的日志文件\n- [ ] 补充磁盘告警阈值配置\n\n"
            "> 磁盘容量需要本周内处理，已同步给运维组。",
        )
        log(
            a,
            "网关流量异常排查",
            "# 网关流量异常排查\n\n## 现象\n\n昨日下午 15:20 起，网关出口流量突增 3 倍，"
            "伴随部分接口超时。\n\n## 排查过程\n\n1. 查看网关监控，定位到某个业务 IP 持续大流量；\n"
            "2. 抓包确认是重复调用导致的重试风暴；\n3. 上游服务超时配置不合理（默认 60s）。\n\n"
            "## 处理\n\n```bash\n# 调整上游超时与重试次数\nupstream_timeout = 10s\nmax_retries = 2\n```\n\n"
            "## 结论\n\n调整后流量恢复正常，需推动业务侧增加幂等处理。",
        )
        log(
            b,
            "MySQL 慢查询优化",
            "# 慢查询优化\n\n发现一条 8s 的慢查询，缺少联合索引。\n\n"
            "```sql\nALTER TABLE work_log ADD INDEX idx_tree_update (tree_id, update_time);\n```\n\n"
            "优化后降到 30ms。",
        )
        log(c, "今日任务清单", "# 今日任务\n\n- [x] 巡检记录整理\n- [ ] 备份策略评审\n- [ ] 周报草稿")
        self.conn.commit()
