"""生成便携版压缩包：程序 + 数据放在同一个文件夹，解压即用。

用法（项目根目录）：
    python scripts/make-portable.py

产物：
    dist-portable/WorkLog/           便携文件夹
    WorkLog-便携版.zip               分发包（解压后得到 WorkLog 文件夹）
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "WorkLog.exe")
OUT_DIR = os.path.join(ROOT, "dist-portable")
APP_DIR = os.path.join(OUT_DIR, "WorkLog")
ZIP_PATH = os.path.join(ROOT, "WorkLog-便携版.zip")

APPDATA_DATA = os.path.join(os.environ.get("APPDATA", ""), "WorkLog")

README = """个人工作日志笔记本 · 便携版
==================================

【怎么用】
双击 WorkLog.exe 即可，不需要安装 Python、Node 或任何运行环境。

【界面速览】
· 顶部菜单栏：文件 / 视图 / 设置 / 帮助，右侧显示在线状态与待同步条数
· 左侧：目录分支（可无限级、可拖拽排序）
· 右侧：日志列表 + Markdown 编辑器（左写右预览）

【文件菜单】
· 导入数据：从 .json 备份恢复（可选「合并」或「覆盖」）
· 导出数据：把全部分支与日志导出成一个 .json 备份文件
· 备份本地数据库 / 打开数据目录 / 退出

【设置菜单】
· 打开设置：云服务器地址、Token、自动保存等
· 快捷键设置：点击按键区域后直接按下组合键即可录制（Esc 取消、Backspace 清除）
· 主题设置：切换浅色 / 深色样式；也可以用自己的图片做背景，并调节背景透明度

【数据在哪】
所有日志都在 data\\notebook.db，就在程序旁边。
备份在 data\\backups\\（每次退出自动生成，保留最近 5 份）。
自定义背景图在 data\\media\\。

【换电脑】
把整个 WorkLog 文件夹（或这个压缩包）拷到新电脑，解压到任意位置，
双击 WorkLog.exe，日志、主题、快捷键设置都在。
也可以只在新电脑上「文件 → 导入数据」导入旧电脑导出的 .json 备份。

【注意】
1. 请不要只拷 WorkLog.exe —— 要连 data 文件夹一起拷，
   否则新电脑上是空的（程序会自动新建一个数据库）。
2. 同一台电脑不要同时开两个副本（程序会避免重复启动）。
3. 不要放在 C:\\Program Files 这类需要管理员权限的目录，
   那里写不了文件，数据会退回存到 %APPDATA%\\WorkLog。
4. 程序完全离线运行，只有点「同步」时才连你自己配置的云服务器。

【版本】v1.1.0
"""


def copy_data(src: str, dst: str) -> int:
    """把现有用户数据复制进便携目录，返回复制的日志条数。"""
    os.makedirs(dst, exist_ok=True)
    if not os.path.isdir(src):
        print("  ! 未找到已有数据，便携包将以空库启动（首次运行会带示例内容）")
        return 0

    count = 0
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if name.endswith(("-wal", "-shm")):
            continue  # WAL 临时文件不带走，避免数据不一致
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

    db = os.path.join(dst, "notebook.db")
    if os.path.isfile(db):
        try:
            conn = sqlite3.connect(db)
            count = conn.execute("SELECT COUNT(*) FROM work_log WHERE is_deleted = 0").fetchone()[0]
            conn.close()
        except Exception:
            count = 0
    return count


def main() -> None:
    if not os.path.isfile(EXE):
        print(f"找不到 {EXE}，请先执行 desktop\\build-exe.bat 打包 exe")
        sys.exit(1)

    print("[1/4] 准备便携目录 ...")
    if os.path.isdir(APP_DIR):
        shutil.rmtree(APP_DIR, ignore_errors=True)
    os.makedirs(APP_DIR, exist_ok=True)

    print("[2/4] 复制程序 ...")
    shutil.copy2(EXE, os.path.join(APP_DIR, "WorkLog.exe"))
    # 便携模式标记：有这个文件，程序就把数据放在自己旁边的 data\ 里
    with open(os.path.join(APP_DIR, "portable.mode"), "w", encoding="utf-8") as f:
        f.write("1\n")
    with open(os.path.join(APP_DIR, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(README)

    print("[3/4] 打包现有日志数据 ...")
    n = copy_data(APPDATA_DATA, os.path.join(APP_DIR, "data"))
    print(f"      已带入 {n} 条日志")

    print("[4/4] 压缩 ...")
    if os.path.isfile(ZIP_PATH):
        os.remove(ZIP_PATH)
    total = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for base, _dirs, files in os.walk(OUT_DIR):
            for name in files:
                full = os.path.join(base, name)
                arc = os.path.relpath(full, OUT_DIR)
                zf.write(full, arc)
                total += os.path.getsize(full)

    size = os.path.getsize(ZIP_PATH)
    print()
    print(f"完成：{ZIP_PATH}")
    print(f"      原始 {total / 1024 / 1024:.1f} MB  →  压缩后 {size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
