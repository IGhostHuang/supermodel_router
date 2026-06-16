@echo off
chcp 65001 >nul
title SuperModel Router
echo ===============================================
echo    SuperModel Router v3.1 — Windows 启动器
echo ===============================================
echo.

:: 找 config.yaml
if not exist config.yaml (
    if exist config.yaml.example (
        copy config.yaml.example config.yaml >nul
        echo 📝 已从模板创建 config.yaml
    )
)

echo 🌐 启动服务: http://127.0.0.1:6473
echo 📊 管理面板: http://127.0.0.1:6473/admin
echo 📝 端口可改 config.yaml 里的 server.port
echo.
echo ⏎ 按 Ctrl+C 停止
echo ===============================================
echo.

.\supermodel_router.exe