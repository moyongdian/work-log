# 个人工作日志笔记本系统

离线优先的工作日志工具：**左侧树形分支目录 + 右侧 Markdown 日志编辑器**，全部数据保存在本机 SQLite，断网可完整使用；联网后可将变更同步到自建云服务器。

顶部菜单栏提供**导入 / 导出备份、快捷键自定义、浅色 / 深色主题，以及用自己的图片做背景并调节背景透明度**。

**双击 `WorkLog.exe` 即可使用，无需安装任何运行环境。**

> 实现依据：`个人工作日志笔记本系统 项目设计说明书.md`

---

## 1. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 桌面外壳 | Python 3.13 + pywebview | 复用系统 **WebView2**，不内置 Chromium；PyInstaller 打包为单文件 exe |
| 界面 | Vue 3 + Vite | 无 UI 框架依赖，产物 gzip 约 56 KB |
| 本地存储 | Python 内置 sqlite3 | WAL 模式，毫秒级读写 |
| Markdown | 自研轻量编辑器 + marked | 不依赖 CDN 字体，保证离线可用 |
| 服务端 | Rust Axum + sqlx | 编译为单二进制，常驻内存 < 30 MB |
| 云端库 | MySQL 8 | 表结构与本地 SQLite 一致 |

> **关于 Tauri**：设计书原定 Tauri + Rust。本机没有 MSVC 生成工具（且无管理员权限，无法安装），
> Tauri 在 Windows 上必须依赖 MSVC 链接器，因此桌面外壳改用 Python + pywebview。
> 二者都用系统 WebView2 渲染界面、都不内置浏览器内核，**运行时内存与体积处于同一量级**。
> 设计书要求的 Tauri 工程源码仍保留在 `client/src-tauri/`，装好 Rust + MSVC 后可直接 `npm run tauri:build`。

---

## 2. 目录结构

```
notebook/
├── WorkLog-便携版.zip          ★ 便携分发包（程序 + 日志数据，解压即用）
├── dist-portable/WorkLog/      便携版目录（压缩前的原样）
│   ├── WorkLog.exe
│   ├── portable.mode           便携模式标记
│   ├── 使用说明.txt
│   └── data/                   数据库 / 配置 / 备份（随文件夹走）
├── WorkLog.exe                 构建产物（数据在 %APPDATA%，调试用）
├── desktop/                    桌面版源码
│   ├── main.py                 入口：本地服务 + 原生窗口 + JsApi（文件对话框）
│   ├── store.py                数据层：SQLite / change_log / 同步 / 导入导出 / 背景图
│   ├── api.py                  HTTP 命令接口 + 静态资源 + /media
│   ├── assets/app.ico          窗口图标
│   ├── web/                    前端构建产物（打包时自动同步）
│   └── build-exe.bat           一键重新打包 exe
├── client/                     Web 前端 + Tauri 备用工程
│   ├── src/                    Vue 界面
│   │   ├── App.vue             业务编排 / 菜单定义 / 快捷键分发
│   │   ├── theme.js            主题（深浅色 / 主色 / 透明度 / 背景图）
│   │   ├── shortcuts.js        快捷键定义、录制、匹配、冲突检测
│   │   ├── fileio.js           原生文件对话框桥接（浏览器回退）
│   │   ├── styles.css          全部 CSS 变量与组件样式
│   │   └── components/         MenuBar / SettingsDialog / TreeSidebar / LogPanel …
│   └── src-tauri/              Tauri + Rust 客户端（需 MSVC，未编译）
├── server/                     云同步服务端（Axum + MySQL）
│   ├── schema.sql              建表脚本
│   └── src/{main,routes,auth,models}.rs
├── preview/                    浏览器预览服务（Node，开发用）
├── scripts/
│   ├── gen_icons.py            图标生成
│   └── make-portable.py        生成便携压缩包
└── docs/部署文档.md
```

---

## 3. 使用方式

### 3.1 便携版（推荐：换电脑直接拷走）

1. 拿到 **`WorkLog-便携版.zip`**（13 MB）；
2. 解压到任意位置——本地磁盘、U 盘、网盘同步目录都行，路径不要有特殊权限要求；
3. 双击解压出来的 `WorkLog\WorkLog.exe`。

解压后的结构：

```
WorkLog\
├── WorkLog.exe          程序
├── portable.mode        便携模式标记（别删）
├── 使用说明.txt
└── data\
    ├── notebook.db      所有日志都在这里
    ├── config.json      云服务器配置
    └── backups\         每次退出自动备份，保留 5 份
```

- **换电脑**：把整个 `WorkLog` 文件夹拷过去，双击即可，日志一条不少；
- **数据完全跟着文件夹走**，不写注册表、不写 `%APPDATA%`、不联网；
- 重复双击不会开第二个窗口，只会把已有窗口切到前台。

> 注意：不要只拷 `WorkLog.exe` 而不带 `data` 文件夹，否则新电脑上会是空的（程序会自动新建数据库）。
> 也不要放在 `C:\Program Files` 这类需要管理员权限的目录，那里写不了文件，数据会退回 `%APPDATA%\WorkLog`。

### 3.2 单文件 exe（数据在用户目录）

项目根目录的 `WorkLog.exe` 是构建产物，**没有** `portable.mode` 标记，所以数据存在
`%APPDATA%\WorkLog\`。适合“只想要一个 exe、数据放在系统用户目录”的场景。

两种模式共用同一份程序代码，区别只在于 exe 旁边有没有 `portable.mode` 这个文件。

### 3.3 从源码运行桌面版

```bash
# 一次性准备（已在本机配好）
python -m venv .venv
.venv\Scripts\pip install pywebview pyinstaller pillow

# 构建前端并复制到 desktop/web
cd client && npm install && npm run build
# 然后把 client\dist 整个复制到 desktop\web

# 运行
cd desktop
python main.py
```

### 3.4 重新打包（exe + 便携包）

双击 `desktop\build-exe.bat` 打包出 `WorkLog.exe`，然后：

```bash
python scripts/make-portable.py
```

它会生成 `dist-portable\WorkLog\` 并把**当前 `%APPDATA%\WorkLog` 里的日志**一并带进去，
最后压缩成 `WorkLog-便携版.zip`。

### 3.5 重新打包 exe

双击 `desktop\build-exe.bat`，它会自动构建前端 → 同步资源 → 打包出根目录的 `WorkLog.exe`。

或手动执行：

```bash
cd desktop
pyinstaller --noconfirm --onefile --windowed --name WorkLog ^
  --icon ../client/src-tauri/icons/icon.ico ^
  --add-data "%CD%\web;web" --add-data "%CD%\assets;assets" ^
  --distpath .. --workpath ../.build/work --specpath ../.build main.py
```

### 3.6 浏览器预览（开发调试用）

```bash
node --experimental-sqlite preview/server.mjs
# 打开 http://127.0.0.1:8971
```

预览版用 Node 内置 SQLite，并在 `/cloud/api/*` 内置了一个与 Axum 接口一致的「模拟云端」，
方便在没有服务器的情况下联调同步与冲突处理。**数据与桌面版互相独立。**

---

## 4. 菜单栏 · 导入导出 · 快捷键 · 主题

### 4.1 顶部菜单栏

```
[Logo] 工作日志笔记本 │ 文件  视图  设置  帮助 │ ………  ● 在线   待同步 3
```

| 菜单 | 项目 |
| --- | --- |
| 文件 | 新建日志 `Ctrl+N`、新建分支 `Ctrl+Shift+N`、导入数据…、导出数据… `Ctrl+E`、备份本地数据库、打开数据目录、退出 |
| 视图 | 切换深浅色 `Ctrl+Shift+L`、搜索日志 `Ctrl+F`、刷新数据 |
| 设置 | 打开设置… `Ctrl+,`、快捷键设置…、主题设置…（后两项直接定位到设置对应标签页） |
| 帮助 | 关于工作日志笔记本、使用说明 |

菜单右侧常驻显示在线状态与待同步条数。侧边栏只保留「新建分支 / 同步」，设置入口已统一收进菜单。

### 4.2 导入 / 导出

- **导出**：把全部分支与日志（含软删除标记）写成一份 JSON，默认文件名 `工作日志备份-YYYY-MM-DD.json`。
  桌面版弹出系统「另存为」对话框；浏览器预览回退为直接下载。
- **导入**：读取同一格式的 JSON，两种方式二选一：
  - **合并**（推荐）：按 ID 合并，同一条数据以 `updateTime` 较新的为准，本地独有的数据保留；
  - **覆盖**：先清空本地全部分支与日志，再写入文件内容（界面会二次提醒，建议先导出备份）。
- 导入的数据会写入 `change_log`，下次同步时自动推送到云端，因此「换电脑」也可以只走
  「旧机导出 → 新机导入」这条路径，不必拷 `data` 文件夹。

实现：前端 `fileio.js`（原生对话框 / Blob 回退）+ 后端 `export_data` / `import_data` 命令。

### 4.3 快捷键

设置 → 快捷键，点击右侧按键区域后**直接按下想要的组合键**即可录制；`Esc` 取消录制，
`Backspace` / `Delete` 清除绑定，重复绑定会标红提示。共 8 个动作：

| 动作 | 默认 | 动作 | 默认 |
| --- | --- | --- | --- |
| 新建日志 | `Ctrl+N` | 打开设置 | `Ctrl+,` |
| 新建分支 | `Ctrl+Shift+N` | 切换深浅色 | `Ctrl+Shift+L` |
| 保存日志 | `Ctrl+S` | 导出数据 | `Ctrl+E` |
| 同步到云端 | `F5` | 搜索日志 | `Ctrl+F` |

快捷键表存在 `config.json` 的 `shortcuts` 字段，随数据目录一起迁移。
在输入框内只响应带修饰键的组合，不影响正常打字；弹窗打开时全局快捷键暂停。

### 4.4 主题（更换样式 + 自定义背景）

设置 → 主题，改动**即时预览**，点「保存」永久生效，点「取消」回滚到打开前的样式。

| 分组 | 项 | 说明 |
| --- | --- | --- |
| 主题样式 | 浅色 / 深色 | 整体配色切换，写入 `<html data-theme>` |
| 自定义样式 | 背景图片 | 自己的 JPG / PNG / WebP，落盘到 `data/media/bg.*`，经本地 `/media/` 提供 |
| 自定义样式 | 背景透明度 | 0% ~ 100%，越大背景越清晰；调到 0% 等于不显示背景 |

背景透明度同时驱动两件事，保证「数值调高 = 背景更清楚」符合直觉：

```
--bg-opacity     背景图层自身的不透明度
--panel-opacity  面板不透明度 = 1 - bgOpacity × 0.45   （背景越清晰，面板越透，否则永远看不到背景）
```

所以 60%（默认）对应面板 73%，100% 对应面板 55%。没有设置背景图时面板保持完全不透明，
滑杆也会置灰并提示先选图片。

实现要点：

- 全部颜色收敛成 CSS 变量（`styles.css` 的 `:root` 与 `[data-theme="dark"]`），组件不写死颜色；
- 半透明只在最外层（菜单栏 / 侧边栏 / 主区）应用一次，内部分区用**透明薄着色**（`--sub-tint`）区分层次，
  这样背景图能真正透出来，而不会被子面板二次遮挡；
- `index.html` 内联脚本在首屏渲染前套用 `localStorage` 缓存的主题，避免打开瞬间闪白；
- 主题存在 `config.json` 的 `theme` 字段（`{ mode, bgImage, bgOpacity }`），换电脑后随数据目录一起生效。

---

## 5. 功能实现对照

| 设计书要求 | 实现位置 |
| --- | --- |
| 左侧 25% 树形多级目录 / 右侧 75% 日志区 | `App.vue` + `styles.css` |
| 新建分支自动预填 `YYYY-MM-DD ` 前缀，光标落在日期后 | `App.vue#openAdd`（`datePrefix()` + `setSelectionRange(len,len)`） |
| 日期可自由修改/删除 | 输入框为普通文本，前缀仅作初始值 |
| 无限级子分支 | `TreeNode.vue` 递归渲染，`parent_id` 自关联 |
| 重命名 / 删除分支（可选是否删除日志） | 右键菜单 → `rename_tree` / `delete_tree(deleteLogs)` |
| 删除分支时保留日志 | 日志迁移到上一级；顶级分支归入「未分类（归档）」 |
| 拖拽调整顺序 | 上/下 1/3 同级排序，中间 1/3 移入该分支 |
| 日志新增/修改/删除、Markdown 编辑 | `LogPanel.vue` + `MarkdownEditor.vue` |
| 手动 / 自动保存 | 保存按钮 + 停止输入 1.8s 自动保存（可关闭） |
| 离线优先 | 所有写操作直接落本地 SQLite，不等待网络 |
| change_log 变更记录 | `store.py`：同一数据多次修改只保留最后一条 |
| 同步按钮离线置灰 | `TreeSidebar.vue`：`online && serverReady` 才可点 |
| 同步前连通性检测 | `store.py#sync_now` 先请求 `/api/ping` |
| 冲突策略（updateTime + 弹窗选择） | `sync_now` → `ConflictDialog.vue` → `resolve_conflicts` |
| 同步结果提示 | 底部 toast + `SyncReport` |
| 配置页离线可查看、不可测试 | `SettingsDialog.vue` |
| 退出自动备份 | `main.py`：窗口关闭后 `store.backup()` |
| 系统时间不准的提示 | 设置页警示条 + 新建分支弹窗说明 |
| 顶部菜单栏（文件/视图/设置/帮助） | `MenuBar.vue` + `App.vue#menus` |
| 文件菜单导入 / 导出 | `fileio.js` + `export_data` / `import_data` |
| 设置移入菜单栏 | `TreeSidebar.vue` 移除设置按钮，改由 `openSettings(tab)` 打开 |
| 设置内配置快捷键 | `SettingsDialog.vue`「快捷键」标签 + `shortcuts.js`（录制 / 冲突检测 / 分发） |
| 主题可改样式 | `theme.js` + `styles.css` CSS 变量 + `[data-theme="dark"]` |
| 自定义样式：自己的图片做背景 | `save_background` 落盘 `data/media/`，`body::before` 铺背景层 |
| 背景可调透明度 | `--bg-opacity` 控制背景层，`--panel-opacity` 随之联动 |
| 原生文件对话框 | `main.py#JsApi`（`save_text` / `open_text` / `pick_image` / `close_app`） |

---

## 6. 数据模型

本地 SQLite（`%APPDATA%\WorkLog\notebook.db`）：

- `tree(id, parent_id, name, sort, create_time, update_time, is_deleted, sync_status)`
- `work_log(id, tree_id, title, content, create_time, update_time, is_deleted, sync_status)`
- `change_log(id, data_type, data_id, operate, operate_time)`
- `meta(key, value)`

云端 MySQL 只有 `tree`、`work_log` 两张表，字段一致，**不含 change_log**。

---

## 7. 同步机制

```
点击同步
  ├─ 1. GET  /api/ping                      连通性检测（失败即中止，本地无改动）
  ├─ 2. 读取 change_log  → 组装推送数据
  ├─ 3. POST /api/sync/push                 服务端逐条比对 update_time
  │      ├─ 云端较新 → 返回 conflicts（不落库）
  │      └─ 否则 upsert，返回 applied
  ├─ 4. 非冲突项：本地标记 synced 并删除对应 change_log
  ├─ 5. GET  /api/sync/pull?since=上次同步时间   增量拉取
  ├─ 6. 本地合并（本地有未同步修改且更新更晚时保留本地）
  └─ 7. 有冲突 → 弹窗逐条选择「保留本地 / 采用云端」
         ├─ 保留本地 → 带 force=true 重新推送覆盖云端
         └─ 采用云端 → 用云端数据覆盖本地
```

同步中断时：已推送部分保留、未推送的 `change_log` 保留，下次自动重试，本地数据不受影响。

---

## 8. 服务端接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/ping` | GET | 连通性检测 |
| `/api/tree/list` | GET | 云端全部目录分支 |
| `/api/tree/save` | POST | 新增/更新分支 |
| `/api/tree/delete` | DELETE | 删除分支（软删除，`?id=`） |
| `/api/log/list` | GET | 云端日志（`?treeId=`） |
| `/api/log/save` | POST | 新增/更新日志 |
| `/api/log/delete` | DELETE | 删除日志（`?id=`） |
| `/api/sync/pull` | GET | 批量拉取（`?since=`） |
| `/api/sync/push` | POST | 批量推送（`{changes:[], force:bool}`） |
| `/healthz` | GET | 运维探活（不需要 Token） |

鉴权：`X-Api-Token: <token>` 或 `Authorization: Bearer <token>`。

---

## 9. 验收自查

| 验收项 | 结果 |
| --- | --- |
| 断网下分支/日志增删改查全部可用 | ✅ 全部走本地 SQLite |
| 新建分支自动预填日期前缀，光标在日期后 | ✅ 实测光标索引 = 前缀长度 |
| 多级分支 + Markdown 编辑保存 | ✅ 递归树 + 分屏预览 |
| 联网同步上传离线修改 | ✅ 实测「上传 2 条，拉取 2 条」 |
| 另一设备拉取可获取全部数据 | ✅ 增量拉取 + 按 update_time 合并 |
| 同步失败不丢本地数据 | ✅ 先落库后推送，失败保留 change_log |
| 冲突弹窗由用户选择版本 | ✅ 实测采用云端后本地被正确覆盖 |
| 双击 exe 直接打开独立窗口 | ✅ 单文件 14 MB，重复启动不新开窗口 |
| 便携版解压到新电脑即可用 | ✅ 解压到新目录 + 伪造空 `%APPDATA%`，读到全部日志，且未写入系统目录 |
| 顶部菜单栏可用 | ✅ 4 个菜单、快捷键提示、在线状态与待同步条数均正常 |
| 导入导出闭环 | ✅ 导出 5 分支 4 日志；合并导入 +1 且重复导入幂等；覆盖导入可完全还原；非法文件被拒绝 |
| 快捷键可录制并生效 | ✅ 录制 `Ctrl+Alt+J`、冲突标红；`Ctrl+Shift+L` 实测切换主题并写入配置 |
| 主题 / 自定义背景 / 背景透明度 | ✅ 深浅色切换、选图后滑杆启用、80% → 背景层 0.8 / 面板 0.64、0% → 背景隐藏且面板恢复不透明、刷新后自动套用 |
| 原生文件对话框 | ✅ 桩窗口单测覆盖另存为 / 打开（含 BOM）/ 选图 / 取消 / 超大图拒绝 |

---

## 10. 已知限制

1. **内存**：桌面版常驻约 100–120 MB（WebView2 运行时占大头），高于设计书的 80 MB 目标。
   任何基于 WebView 的方案（含 Tauri）都会受 WebView2 自身占用影响，这是运行时下限而非本程序开销。
2. `client/src-tauri/`（Tauri 工程）与 `server/`（Axum 服务端）**未经 `cargo build` 验证**——
   本机无 Rust 工具链。桌面版走的是 `desktop/` 这套 Python 实现，功能完整。
3. 预览服务的「模拟云端」只在本地进程内，多设备同步需部署 `server/`。
4. 一期未实现全文检索（FTS）、Markdown 导出、附件同步，属设计书第 11 章二期范围。
