@echo off
chcp 65001 >nul
title SuperModel Router
echo ===============================================
echo    SuperModel Router v1.0 — Windows 启动器
echo ===============================================
echo.

:: 找 config.yaml
if not exist config.yaml (
    if exist config.yaml.example (
        copy config.yaml.example config.yaml >nul
        echo 📝 已从模板创建 config.yaml
    )
)

echo 🌐 启动服务: http://127.0.0.1:1298
echo 📊 管理面板: http://127.0.0.1:1298/admin
echo.
echo ⏎ 按 Ctrl+C 停止
echo ===============================================
echo.

.\supermodel_router.exe