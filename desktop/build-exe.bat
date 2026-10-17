@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title 打包 WorkLog.exe

echo.
echo  ============================================
echo   打包桌面版 WorkLog.exe
echo  ============================================
echo.

set PY=C:\Users\Lenovo\.workbuddy-ai\binaries\python\envs\notebook\Scripts\python.exe
if not exist "%PY%" (
    echo  [错误] 未找到打包环境：%PY%
    echo  请先创建虚拟环境并安装：pywebview pyinstaller pillow
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
