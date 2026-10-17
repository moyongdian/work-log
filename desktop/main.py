"""个人工作日志笔记本 · 桌面版入口

启动流程：
  1. 准备数据目录（%APPDATA%\\WorkLog）
  2. 在 127.0.0.1 上启动本地命令服务（离线核心）
  3. 用系统 WebView2 打开一个原生窗口，加载前端界面

全程不访问网络；仅当用户主动点击「同步」时才连接自建云服务器。
"""

from __future__ import annotations

import base64
import ctypes
import mimetypes
import os
import socket
import sys
import traceback

import webview

from api import AppServer
from store import Store

APP_TITLE = "工作日志笔记本"
# 本地命令服务端口；可用环境变量 WORKLOG_PORT 覆盖（便于同时调试多个副本）
PORT = int(os.environ.get("WORKLOG_PORT") or 8799)

IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
MAX_IMAGE_BYTES = 12 * 1024 * 1024

# pywebview 6 用 FileDialog 枚举，旧版用 SAVE_DIALOG / OPEN_DIALOG 常量
try:
    DIALOG_SAVE = webview.FileDialog.SAVE  # type: ignore[attr-defined]
    DIALOG_OPEN = webview.FileDialog.OPEN  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - 兼容旧版本
    DIALOG_SAVE = webview.SAVE_DIALOG
    DIALOG_OPEN = webview.OPEN_DIALOG


class JsApi:
    """暴露给前端的原生能力：window.pywebview.api.*

    前端 src/fileio.js 在桌面端优先走这些方法（原生对话框），
    浏览器预览模式下自动回退到 Blob 下载 / input[type=file]。
    """

    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------ 内部工具

    @staticmethod
    def _window():
        try:
            return webview.windows[0]
        except Exception:
            return None

    @staticmethod
    def _first_path(result) -> str | None:
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return str(result[0]) if result else None
        return str(result)

    # ------------------------------------------------------------ 文本文件

    def save_text(self, filename: str, text: str) -> dict:
        """弹出「另存为」对话框并写入文本，返回 { canceled, path }。"""
        win = self._window()
        if not win:
            return {"canceled": True, "path": ""}
        try:
            picked = win.create_file_dialog(
                DIALOG_SAVE,
                directory=os.path.expanduser("~"),
                save_filename=filename or "worklog-backup.json",
            )
        except Exception as e:
            raise RuntimeError(f"打开保存对话框失败：{e}") from e

        path = self._first_path(picked)
        if not path:
            return {"canceled": True, "path": ""}
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text or "")
        except Exception as e:
            raise RuntimeError(f"写入文件失败：{e}") from e
        return {"canceled": False, "path": path}

    def open_text(self, accept: str = ".json") -> dict:
        """弹出「打开」对话框读取文本，返回 { canceled, name, content, path }。"""
        win = self._window()
        if not win:
            return {"canceled": True}

        exts = [e.strip() for e in (accept or "").split(",") if e.strip().startswith(".")]
        patterns = ";".join(f"*{e}" for e in exts) or "*.*"
        label = "、".join(e.upper().lstrip(".") for e in exts) or "文本"
        try:
            picked = win.create_file_dialog(
                DIALOG_OPEN,
                directory=os.path.expanduser("~"),
                allow_multiple=False,
                file_types=(f"{label} 文件 ({patterns})", "所有文件 (*.*)"),
            )
        except Exception as e:
            raise RuntimeError(f"打开文件对话框失败：{e}") from e

        path = self._first_path(picked)
        if not path:
            return {"canceled": True}
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk", errors="replace") as f:
                content = f.read()
        except Exception as e:
            raise RuntimeError(f"读取文件失败：{e}") from e

        return {
            "canceled": False,
            "name": os.path.basename(path),
            "content": content,
            "path": path,
        }

    # ------------------------------------------------------------ 图片

    def pick_image(self) -> dict:
        """选择一张图片并转成 dataURL，交给前端预览、后端落盘。"""
        win = self._window()
        if not win:
            return {"canceled": True}
        try:
            picked = win.create_file_dialog(
                DIALOG_OPEN,
                directory=os.path.expanduser("~"),
                allow_multiple=False,
                file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.webp;*.gif)", "所有文件 (*.*)"),
            )
        except Exception as e:
            raise RuntimeError(f"打开图片对话框失败：{e}") from e

        path = self._first_path(picked)
        if not path:
            return {"canceled": True}

        ext = os.path.splitext(path)[1].lower()
        mime = IMAGE_MIME.get(ext, mimetypes.guess_type(path)[0] or "image/png")
        try:
            size = os.path.getsize(path)
            if size > MAX_IMAGE_BYTES:
                raise RuntimeError("图片过大，请选择 12MB 以内的图片")
            with open(path, "rb") as f:
                raw = f.read()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"读取图片失败：{e}") from e

        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        return {"canceled": False, "name": os.path.basename(path), "dataUrl": data_url}

    # ------------------------------------------------------------ 窗口

    def close_app(self) -> dict:
        win = self._window()
        if win:
            win.destroy()
        return {"ok": True}


def resource_dir() -> str:
    """打包后资源解压到 sys._MEIPASS，开发时用脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def app_root() -> str:
    """程序所在目录（打包后是 exe 所在目录，开发时是脚本目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _ensure_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def is_portable() -> bool:
    """便携模式：程序目录下有 portable.mode 标记，数据与程序放在一起。"""
    return os.path.isfile(os.path.join(app_root(), "portable.mode"))


def data_dir() -> str:
    """便携模式数据在程序目录的 data\\ 下，否则在 %APPDATA%\\WorkLog。"""
    if is_portable():
        portable = os.path.join(app_root(), "data")
        if _ensure_writable(portable):
            return portable
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WorkLog")


def port_in_use(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def show_message(text: str, title: str = APP_TITLE, style: int = 0x40) -> None:
    """无控制台环境下的提示框（style 0x40=信息，0x10=错误）。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, style)
    except Exception:
        print(text)


def activate_existing() -> bool:
    """把已在运行的窗口切到前台。"""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


def window_geometry() -> tuple[int, int, int, int]:
    """按屏幕尺寸与 DPI 缩放计算合适的窗口大小并居中，避免超出屏幕。"""
    try:
        user32 = ctypes.windll.user32
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        try:
            dpi = user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        scale = (dpi / 96.0) if dpi and dpi > 0 else 1.0
        lw, lh = int(sw / scale), int(sh / scale)
        if lw < 900 or lh < 640:
            # 进程未做 DPI 虚拟化，此时 GetSystemMetrics 已是逻辑值
            lw, lh = sw, sh
        w = min(1240, int(lw * 0.92))
        h = min(820, int(lh * 0.88))
        x = max(0, (lw - w) // 2)
        y = max(0, (lh - h) // 2)
        return w, h, x, y
    except Exception:
        return 1240, 820, 60, 40


def main() -> None:
    if port_in_use(PORT):
        # 已有实例在运行：聚焦它，避免两个窗口同时写同一个数据库
        if not activate_existing():
            show_message("程序已经在运行中。\n\n如果看不到窗口，请在任务栏中查找。")
        return

    web_dir = os.path.join(resource_dir(), "web")
    if not os.path.isfile(os.path.join(web_dir, "index.html")):
        show_message("前端资源缺失，无法启动。\n\n请先执行：cd client && npm run build", style=0x10)
        return

    store = Store(data_dir(), seed_demo=True)
    server = AppServer(store, web_dir, PORT)
    server.start()

    win_w, win_h, win_x, win_y = window_geometry()
    window = webview.create_window(
        APP_TITLE,
        url=f"http://127.0.0.1:{PORT}/",
        width=win_w,
        height=win_h,
        x=win_x,
        y=win_y,
        min_size=(min(900, win_w), min(600, win_h)),
        background_color="#f4f5f7",
        text_select=True,
        js_api=JsApi(store),
    )

    icon_path = os.path.join(resource_dir(), "assets", "app.ico")
    try:
        webview.start(
            gui="edgechromium",
            debug=False,
            icon=icon_path if os.path.isfile(icon_path) else None,
        )
    finally:
        # 退出时自动备份本地数据库（设计书第 10 章风险应对）
        try:
            store.backup()
        except Exception:
            pass
        server.stop()
        store.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        log_path = os.path.join(data_dir(), "error.log")
        try:
            os.makedirs(data_dir(), exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(detail)
        except Exception:
            pass
        show_message(f"程序启动失败：\n\n{detail[-600:]}\n\n详细日志：{log_path}", style=0x10)
        sys.exit(1)
