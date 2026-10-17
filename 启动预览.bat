@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 工作日志笔记本 - 本地预览 (http://127.0.0.1:8971)

echo.
echo  ============================================
echo   个人工作日志笔记本系统 - 本地预览启动器
echo  ============================================
echo.

rem ---- 0. 服务已在运行则直接打开浏览器 ----
netstat -ano | findstr ":8971 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo  [提示] 服务已在运行，直接打开浏览器...
    start "" http://127.0.0.1:8971
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)

rem ---- 1. 检查 Node.js ----
where node >nul 2>nul
if errorlevel 1 (
    echo  [错误] 未找到 node 命令。
    echo.
    echo  请先安装 Node.js 22.5 或更高版本：https://nodejs.org/
    echo  安装后重新双击本文件。
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('node -v') do set NODE_VER=%%v
echo  [1/3] Node.js 版本 %NODE_VER%

rem ---- 2. 检查前端是否已构建 ----
if not exist "client\dist\index.html" (
    echo  [2/3] 前端尚未构建，正在安装依赖并构建...
    pushd client
    if not exist "node_modules" call npm install
    call npm run build
    popd
    if not exist "client\dist\index.html" (
        echo.
        echo  [错误] 前端构建失败，请检查网络后重试。
        pause
        exit /b 1
    )
) else (
    echo  [2/3] 前端已构建 client\dist
)

rem ---- 3. 启动服务并打开浏览器 ----
echo  [3/3] 正在启动预览服务...
echo.
echo  地址：http://127.0.0.1:8971
echo  数据：preview\data\notebook.db
echo  关闭本窗口即停止服务（数据已保存，不会丢失）
echo.

start "" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8971"
node --experimental-sqlite preview\server.mjs

echo.
echo  服务已停止。
pause
