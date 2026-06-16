<#
.SYNOPSIS
    SuperModel Router — Windows 安装/启动脚本
.DESCRIPTION
    自动安装、配置、启动 SuperModel Router 作为 Windows 服务或前台进程
    需要管理员权限来安装服务和配置防火墙

    用法:
        .\install.ps1             交互式安装
        .\install.ps1 -Uninstall  卸载
        .\install.ps1 -Start      启动服务
        .\install.ps1 -Stop       停止服务
        .\install.ps1 -Interactive 前台运行 (不安装服务)

.PARAMETER Uninstall
    卸载服务并清理
.PARAMETER Start
    启动已安装的服务
.PARAMETER Stop
    停止服务
.PARAMETER Interactive
    以控制台前台运行 (适合调试)
#>

param(
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
$VERSION = "1.0.0"
$SERVICE_NAME = "SuperModelRouter"
$DATA_DIR = "$env:USERPROFILE\.supermodel_router"
$CONFIG_PATH = "$DATA_DIR\config.yaml"
$INSTALL_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── banner ────────────────────────────────────────────────
function Show-Banner {
    Write-Host @"

██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗ ███╗   ███╗ ██████╗ ██████╗ ███████╗██╗
╚██╗██╔╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗████╗ ████║██╔═══██╗██╔══██╗██╔════╝██║
 ╚███╔╝  ╚████╔╝ ██████╔╝█████╗  ██████╔╝██╔████╔██║██║   ██║██████╔╝█████╗  ██║
 ██╔██╗   ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║╚██╔╝██║██║   ██║██╔══██╗██╔══╝  ██║
██╔╝ ██╗   ██║   ██║     ███████╗██║  ██║██║ ╚═╝ ██║╚██████╔╝██║  ██║███████╗██████╗
╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝

    v$VERSION — 多 Provider / 多 Key / 智能路由
    SuperModel Router

"@ -ForegroundColor Cyan
}

# ── 管理员检查 ─────────────────────────────────────────────
function Test-Admin {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($user)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ── 写入默认配置 ──────────────────────────────────────────────
function Write-DefaultConfig {
    if (!(Test-Path $DATA_DIR)) { New-Item -ItemType Directory -Path $DATA_DIR -Force | Out-Null }

    $config = @"
# ============================================================
# SuperModel Router Config v3.1 — 多 Provider / 多 Key / 智能路由
# 默认端口 6473, 可手动修改下方 server.port 后重启服务生效
# ============================================================
server:
  host: "0.0.0.0"
  port: 6473
  api_key: ""

routing:
  strategy: "round-robin"
  failover_threshold: 3
  recovery_interval: 300
  max_retry: 2
  first_token_timeout_ms: 10000
  retry_backoff_ms: [0, 500]

providers:
  openrouter:
    enabled: true
    base_url: "https://openrouter.ai/api/v1"
    api_keys:
      - ""  # ← 填入你的 API Key
    model_rules:
      mode: "pattern"
      pattern: ".*free.*"
    max_concurrent: 2
    health_check_interval: 300

  deepseek:
    enabled: false
    base_url: "https://api.deepseek.com/v1"
    api_keys:
      - ""
    model_rules:
      mode: "all"
      exclude: []
    max_concurrent: 3
    health_check_interval: 300
"@
    Set-Content -Path $CONFIG_PATH -Value $config -Encoding UTF8
    Write-Host "📝 已创建默认配置: $CONFIG_PATH" -ForegroundColor Green
    Write-Host "⚠️  请编辑该文件填入你的 API Keys (端口默认 6473, 可改 server.port)" -ForegroundColor Yellow
}

# ── 检查 NSSM ──────────────────────────────────────────────
function Find-NSSM {
    $nssm = Get-Command "nssm" -ErrorAction SilentlyContinue
    if ($nssm) { return $nssm.Source }

    $localPath = "$INSTALL_DIR\nssm.exe"
    if (Test-Path $localPath) { return $localPath }

    return $null
}

# ── 安装 NSSM ──────────────────────────────────────────────
function Install-NSSM {
    $nssmUrl = "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip"
    $zipPath = "$env:TEMP\nssm.zip"
    $extractPath = "$env:TEMP\nssm"

    Write-Host "📥 下载 NSSM (Windows Service Manager)..." -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri $nssmUrl -OutFile $zipPath -UseBasicParsing
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

        # 自动选合适架构
        if ([Environment]::Is64BitOperatingSystem) {
            $arch = "win64"
        } else {
            $arch = "win32"
        }
        Copy-Item "$extractPath\nssm-$arch\nssm.exe" "$INSTALL_DIR\nssm.exe" -Force
        Write-Host "✅ NSSM 已安装到: $INSTALL_DIR\nssm.exe" -ForegroundColor Green
        return "$INSTALL_DIR\nssm.exe"
    } catch {
        Write-Host "⚠️  NSSM 下载失败: $_" -ForegroundColor Red
        Write-Host "   请手动下载 https://nssm.cc/download 放到本目录" -ForegroundColor Yellow
        return $null
    } finally {
        Remove-Item $zipPath -ErrorAction SilentlyContinue
        Remove-Item $extractPath -Recurse -ErrorAction SilentlyContinue
    }
}

# ── 安装 Windows 服务 ──────────────────────────────────────
function Install-Service {
    $exePath = "$INSTALL_DIR\supermodel_router.exe"
    if (!(Test-Path $exePath)) {
        Write-Host "❌ 未找到 supermodel_router.exe" -ForegroundColor Red
        Write-Host "   请确认该程序在本目录中" -ForegroundColor Yellow
        return $false
    }

    # 先卸载已存在的服务
    nssm stop $SERVICE_NAME 2>$null
    nssm remove $SERVICE_NAME confirm 2>$null
    Start-Sleep -Seconds 2

    # 创建服务
    & (Find-NSSM) install $SERVICE_NAME $exePath
    & (Find-NSSM) set $SERVICE_NAME AppDirectory $INSTALL_DIR
    & (Find-NSSM) set $SERVICE_NAME AppStdout "$DATA_DIR\logs\stdout.log"
    & (Find-NSSM) set $SERVICE_NAME AppStderr "$DATA_DIR\logs\stderr.log"
    & (Find-NSSM) set $SERVICE_NAME AppRotateFiles 1
    & (Find-NSSM) set $SERVICE_NAME AppRotateSeconds 86400
    & (Find-NSSM) set $SERVICE_NAME AppRotateBytes 10485760
    & (Find-NSSM) set $SERVICE_NAME Description "SuperModel Router — 多 Provider 多 Key 智能路由聚合器"
    & (Find-NSSM) set $SERVICE_NAME Start SERVICE_AUTO_START

    Write-Host "✅ 服务 '$SERVICE_NAME' 已安装 (自动启动)" -ForegroundColor Green
    return $true
}

# ── 从 config.yaml 读取端口 (用户可改) ──────────────────────
function Get-ConfigPort {
    if (!(Test-Path $CONFIG_PATH)) { return 6473 }
    try {
        $content = Get-Content $CONFIG_PATH -Raw
        # 匹配 server: 段下的 port 字段
        if ($content -match '(?ms)^\s*server:\s*\n(?:\s+[^\n]+\n)*?\s+port:\s*(\d+)') {
            return [int]$Matches[1]
        }
    } catch {}
    return 6473
}

# ── 防火墙 ──────────────────────────────────────────────────
function Configure-Firewall {
    $port = Get-ConfigPort
    try {
        $rule = Get-NetFirewallRule -DisplayName "SuperModel Router" -ErrorAction SilentlyContinue
        if (!$rule) {
            New-NetFirewallRule `
                -DisplayName "SuperModel Router" `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort $port `
                -Action Allow `
                -Profile Any | Out-Null
            Write-Host "🛡️  防火墙: 已放行 TCP $port 入站" -ForegroundColor Green
        }
    } catch {
        Write-Host "⚠️  防火墙规则创建失败 (非管理员?)" -ForegroundColor Yellow
    }
}

# ── 启动服务 ────────────────────────────────────────────────
function Start-Service {
    $nssm = Find-NSSM
    if (!$nssm) { Write-Host "❌ NSSM 未安装"; return }

    & $nssm start $SERVICE_NAME
    Start-Sleep -Seconds 3

    $status = & $nssm status $SERVICE_NAME
    if ($status -eq "SERVICE_RUNNING") {
        $displayPort = Get-ConfigPort
        Write-Host "✅ SuperModel Router 服务已启动" -ForegroundColor Green
        Write-Host "🌐 http://127.0.0.1:$displayPort" -ForegroundColor Cyan
        Write-Host "📊 http://127.0.0.1:$displayPort/admin" -ForegroundColor Cyan
    } else {
        Write-Host "❌ 服务启动失败, 查看日志: $DATA_DIR\logs\" -ForegroundColor Red
    }
}

# ── 停止服务 ────────────────────────────────────────────────
function Stop-Service {
    $nssm = Find-NSSM
    if (!$nssm) { return }

    & $nssm stop $SERVICE_NAME
    Write-Host "⏹️  服务已停止" -ForegroundColor Yellow
}

# ── 卸载 ────────────────────────────────────────────────────
function Uninstall-All {
    Write-Host "🗑️  卸载 SuperModel Router..." -ForegroundColor Yellow

    $nssm = Find-NSSM
    if ($nssm) {
        & $nssm stop $SERVICE_NAME 2>$null
        & $nssm remove $SERVICE_NAME confirm 2>$null
        Write-Host "✅ 服务已移除" -ForegroundColor Green
    }

    # 防火墙
    try {
        Remove-NetFirewallRule -DisplayName "SuperModel Router" -ErrorAction SilentlyContinue
    } catch {}

    # 数据目录（保留配置文件不删，用户可能想备份）
    Write-Host "ℹ️  配置文件保留在: $CONFIG_PATH" -ForegroundColor Gray
    Write-Host "   手动删除: Remove-Item '$DATA_DIR' -Recurse" -ForegroundColor Gray

    Write-Host "✅ 卸载完成" -ForegroundColor Green
}

# ── 交互式前台运行 ──────────────────────────────────────────
function Run-Interactive {
    $exePath = "$INSTALL_DIR\supermodel_router.exe"
    if (!(Test-Path $exePath)) {
        Write-Host "❌ 未找到 supermodel_router.exe" -ForegroundColor Red
        return
    }

    Write-Host "🚀 前台模式启动 (Ctrl+C 停止)" -ForegroundColor Green
    Write-Host "═" * 50
    & $exePath
}

# ════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════
Show-Banner

# 参数模式
if ($Uninstall) { Uninstall-All; return }
if ($Start) { Start-Service; return }
if ($Stop) { Stop-Service; return }
if ($Interactive) { Run-Interactive; return }

# ── 交互安装 ────────────────────────────────────────────────
if (!(Test-Path $CONFIG_PATH)) {
    Write-DefaultConfig
}

if (!(Test-Path "$INSTALL_DIR\supermodel_router.exe")) {
    Write-Host "❌ 未找到 supermodel_router.exe" -ForegroundColor Red
    Write-Host "   请先下载或编译, 确认该文件与 install.ps1 同目录" -ForegroundColor Yellow
    exit 1
}

Write-Host "🔧 安装中..." -ForegroundColor Cyan

# 检查环境
$isAdmin = Test-Admin
if (!$isAdmin) {
    Write-Host "⚠️  未以管理员运行, 部分功能受限:" -ForegroundColor Yellow
    Write-Host "   服务安装 ❌  防火墙配置 ❌" -ForegroundColor Yellow
    Write-Host "   请以管理员身份重新运行本脚本以获得完整功能" -ForegroundColor Yellow
}

# 配置目录
if (!(Test-Path $DATA_DIR)) {
    New-Item -ItemType Directory -Path "$DATA_DIR\logs" -Force | Out-Null
    Write-Host "📁 数据目录: $DATA_DIR" -ForegroundColor Green
}

# 复制 config
if (Test-Path "$INSTALL_DIR\config.yaml") {
    Copy-Item "$INSTALL_DIR\config.yaml" $CONFIG_PATH -Force
    Write-Host "📄 配置已部署" -ForegroundColor Green
}

# 防火墙
if ($isAdmin) { Configure-Firewall }

# 安装服务的交互选项
$choice = Read-Host "`n安装为 Windows 服务? (Y=是, 开机自启 / N=仅复制文件) [Y/n]"
if ($choice -ne "n" -and $choice -ne "N") {
    $nssm = Find-NSSM
    if (!$nssm) {
        Write-Host "🔍 NSSM 未找到, 尝试自动下载..." -ForegroundColor Yellow
        $nssm = Install-NSSM
    }

    if ($nssm) {
        if ($isAdmin) {
            $ok = Install-Service
            if ($ok) {
                $startNow = Read-Host "`n立即启动服务? (Y/n) [Y]"
                if ($startNow -ne "n" -and $startNow -ne "N") {
                    Start-Service
                }
            }
        } else {
            Write-Host "❌ 需要管理员权限来安装服务" -ForegroundColor Red
            Write-Host "   请右键 → 以管理员身份运行" -ForegroundColor Yellow
        }
    }
}

# 准备显示用端口 (最终横幅用)
$displayPort = Get-ConfigPort

Write-Host @"

═══════════════════════════════════════════════
✅ 安装完成!

📁 程序位置:  $INSTALL_DIR
📄 配置文件:  $CONFIG_PATH
📁 日志目录:  $DATA_DIR\logs

管理命令:
  .\install.ps1 -Interactive   前台运行 (调试)
  .\install.ps1 -Start         启动服务
  .\install.ps1 -Stop          停止服务
  .\install.ps1 -Uninstall     卸载
  .\run.bat                    前台启动

访问 (端口可手动改 $CONFIG_PATH 里的 server.port 后重启):
  🌐  API:  http://127.0.0.1:$displayPort
  📊 管理:  http://127.0.0.1:$displayPort/admin
═══════════════════════════════════════════════

"@ -ForegroundColor Green