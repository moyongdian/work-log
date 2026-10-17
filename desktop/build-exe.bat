@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title 打包 WorkLog.exe

echo.
echo  ============================================
echo   打包桌面版 WorkLog.exe
echo  ============================================
echo.

rem ---- 查找可用的 Python：%PY% 环境变量 > 项目内 .venv > PATH 里的 python ----
if defined PY (
    if exist "%PY%" goto :py_found
    echo  [警告] 环境变量 PY 指向的路径不存在：%PY%
)

if exist "%~dp0..\.venv\Scripts\python.exe" (
    set "PY=%~dp0..\.venv\Scripts\python.exe"
    goto :py_found
)

for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"
if defined PY goto :py_found

where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
)
if defined PY goto :py_found

echo  [错误] 未找到 Python 解释器。
echo.
echo  请先安装 Python 3.10+ 并勾选 "Add Python to PATH"，然后执行：
echo      pip install pywebview pyinstaller pillow
echo.
echo  或在本项目根目录创建虚拟环境：
echo      python -m venv .venv
echo      .venv\Scripts\pip install pywebview pyinstaller pillow
echo.
echo  也可以手动指定：set PY=D:\Python313\python.exe
pause
exit /b 1

:py_found
echo  使用 Python：%PY%
"%PY%" -c "import webview, PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo  [错误] 当前 Python 缺少依赖，请先安装：
    echo      "%PY%" -m pip install pywebview pyinstaller pillow
    pause
    exit /b 1
)

echo  [1/3] 构建前端...
pushd client
call npm run build
popd
if not exist "client\dist\index.html" (
    echo  [错误] 前端构建失败
    pause
    exit /b 1
)

echo  [2/3] 同步前端资源到 desktop\web ...
if exist "desktop\web" rmdir /s /q "desktop\web"
xcopy /e /i /q "client\dist" "desktop\web" >nul

echo  [3/3] 打包单文件 exe（约 30 秒）...
"%PY%" -m PyInstaller --noconfirm --onefile --windowed ^
  --name WorkLog ^
  --icon client\src-tauri\icons\icon.ico ^
  --add-data "%CD%\desktop\web;web" ^
  --add-data "%CD%\desktop\assets;assets" ^
  --distpath . --workpath .build\work --specpath .build ^
  desktop\main.py

if exist "WorkLog.exe" (
    echo.
    echo  打包完成：%CD%\WorkLog.exe
) else (
    echo.
    echo  [错误] 打包失败，请查看上方日志
)
pause
