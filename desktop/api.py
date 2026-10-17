"""本地 HTTP 服务：为前端提供 /api/cmd/* 命令接口与静态资源。

只监听 127.0.0.1，不对外暴露；前端通过同源请求访问，无需 CORS。
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from store import ApiError, Store


class AppServer:
    def __init__(self, store: Store, web_dir: str, port: int):
        self.store = store
        self.web_dir = web_dir
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ 命令

    def dispatch(self, name: str, args: dict):
        s = self.store
        a = args or {}

        if name == "list_trees":
            return s.list_trees()
        if name == "create_tree":
            return s.create_tree(a.get("parentId"), a.get("name", ""))
        if name == "rename_tree":
            return s.rename_tree(a.get("id"), a.get("name", ""))
        if name == "delete_tree":
            return s.delete_tree(a.get("id"), bool(a.get("deleteLogs", True)))
        if name == "reorder_trees":
            return s.reorder_trees(a.get("updates") or [])

        if name == "list_logs":
            return s.list_logs(a.get("treeId"))
        if name == "get_log":
            return s.get_log(a.get("id"))
        if name == "create_log":
            return s.create_log(a.get("treeId"), a.get("title", ""), a.get("content", ""))
        if name == "update_log":
            return s.update_log(a.get("id"), a.get("title", ""), a.get("content", ""))
        if name == "delete_log":
            return s.delete_log(a.get("id"))
        if name == "search_logs":
            return s.search_logs(a.get("keyword", ""))

        if name == "get_config":
            return s.load_config()
        if name == "save_config":
            return s.save_config(a.get("config") or {})
        if name == "pending_changes":
            return s.pending_count()
        if name == "app_info":
            return {"dbPath": s.db_path, "backupDir": s.backup_dir, "desktop": True}
        if name == "backup_db":
            return s.backup()

        if name == "net_status":
            return s.net_status()
        if name == "test_connection":
            return s.test_connection()
        if name == "sync_now":
            return s.sync_now()
        if name == "resolve_conflicts":
            return s.resolve_conflicts(a.get("choices") or [])

        if name == "export_data":
            return s.export_data()
        if name == "import_data":
            return s.import_data(a.get("payload"), a.get("mode") or "merge")
        if name == "save_background":
            return s.save_background(a.get("dataUrl") or "")
        if name == "clear_background":
            return s.clear_background()
        if name == "open_data_dir":
            return s.open_data_dir()

        raise ApiError(f"未知命令：{name}")

    # ------------------------------------------------------------------ 服务

    def start(self) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # 静默，避免控制台噪音
                pass

            def _send_json(self, code: int, obj) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                if not raw:
                    return {}
                try:
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return {}

            def do_POST(self):
                path = urlparse(self.path).path
                if path.startswith("/api/cmd/"):
                    name = path[len("/api/cmd/"):]
                    try:
                        result = app.dispatch(name, self._read_body())
                        self._send_json(200, result if result is not None else None)
                    except ApiError as e:
                        self._send_json(500, {"error": str(e)})
                    except Exception as e:  # 兜底，避免服务崩溃
                        self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
                    return
                self._send_json(404, {"error": "not found"})

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/api/health":
                    self._send_json(200, {"ok": True})
                    return
                if path.startswith("/media/"):
                    self._serve_media(unquote(path))
                    return
                self._serve_static(unquote(path))

            def _serve_media(self, path: str) -> None:
                """背景图片等外观资源，只允许读取 media 目录内的文件。"""
                name = posixpath.basename(path)
                if not name or name.startswith("."):
                    self._send_json(404, {"error": "not found"})
                    return
                file_path = os.path.join(app.store.media_dir, name)
                if not os.path.isfile(file_path):
                    self._send_json(404, {"error": "not found"})
                    return
                ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                with open(file_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(data)

            def _serve_static(self, path: str) -> None:
                rel = posixpath.normpath(path).lstrip("/")
                if rel in ("", "."):
                    rel = "index.html"
                file_path = os.path.join(app.web_dir, rel)
                if not os.path.isfile(file_path):
                    file_path = os.path.join(app.web_dir, "index.html")
                if not os.path.isfile(file_path):
                    self._send_json(500, {"error": "前端资源缺失，请先构建 client/dist"})
                    return
                ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                with open(file_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
