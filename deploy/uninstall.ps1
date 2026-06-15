<#
.SYNOPSIS
    SuperModel Router 卸载脚本
#>
param()

$ErrorActionPreference = "Stop"
$SERVICE_NAME = "SuperModelRouter"
$DATA_DIR = "$env:USERPROFILE\.supermodel_router"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "🗑️  SuperModel Router 卸载..." -ForegroundColor Yellow

# 停止并删除 Windows 服务
$nssm = Get-Command "nssm" -ErrorAction SilentlyContinue
if (!$nssm) {
    $nssm = Get-Command "$SCRIPT_DIR\nssm.exe" -ErrorAction SilentlyContinue
}
if ($nssm) {
    Write-Host "   ⏹️  停止服务..."
    & $nssm.Source stop $SERVICE_NAME 2>$null
    Start-Sleep -Seconds 2
    & $nssm.Source remove $SERVICE_NAME confirm 2>$null
    Write-Host "   ✅ 服务已移除" -ForegroundColor Green
}

# 防火墙
try {
    Remove-NetFirewallRule -DisplayName "SuperModel Router" -ErrorAction SilentlyContinue
    Write-Host "   ✅ 防火墙规则已移除" -ForegroundColor Green
} catch {}

# 数据目录
$keepConfig = Read-Host "`n🗑️  删除配置数据目录? ($DATA_DIR)`n包含配置文件(config.yaml)和日志`nY=删除 / N=保留 [N]"
if ($keepConfig -eq "y" -or $keepConfig -eq "Y") {
    if (Test-Path $DATA_DIR) {
        Remove-Item $DATA_DIR -Recurse -Force
        Write-Host "   ✅ 配置目录已删除" -ForegroundColor Green
    }
}

Write-Host @"

═══════════════════════════════════════════════
✅ 卸载完成

若要完全清理:
  删除程序目录: Remove-Item '$SCRIPT_DIR' -Recurse
═══════════════════════════════════════════════

"@ -ForegroundColor Green