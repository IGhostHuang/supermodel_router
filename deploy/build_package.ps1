<#
.SYNOPSIS
    SuperModel Router Windows 构建脚本
    自动: 检查 Python → 安装依赖 → PyInstaller 打包 → 输出 .exe
#>

param(
    [switch]$NoInstall  # 跳过依赖安装
)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$VERSION = "1.0.0"

Write-Host @"
═══════════════════════════════════════════════
  SuperModel Router v$VERSION — Windows 构建
═══════════════════════════════════════════════
"@ -ForegroundColor Cyan

# ── 1. 检查 Python ──────────────────────────────────────────
$python = $null
foreach ($cmd in @("python3", "python")) {
    $p = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($p) {
        $ver = & $cmd --version 2>&1
        if ($ver -match "3\.[89]|3\.1[0-9]|3\.1[2-9]") {
            $python = $cmd
            break
        } elseif ($ver -match "3\.([0-9]+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 8) {
                $python = $cmd
                break
            }
        }
    }
}

if (!$python) {
    Write-Host "❌ 需要 Python 3.8+" -ForegroundColor Red
    Write-Host "📥 下载: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "   安装时务必勾选 'Add Python to PATH'" -ForegroundColor Yellow
    exit 1
}

Write-Host "✅ Python: $(& $python --version)" -ForegroundColor Green

# ── 2. 创建 venv ────────────────────────────────────────────
$venvPath = "$SCRIPT_DIR\venv"
if (!(Test-Path $venvPath)) {
    Write-Host "🔧 创建虚拟环境..." -ForegroundColor Yellow
    & $python -m venv $venvPath
}

# 激活
$pip = "$venvPath\Scripts\pip.exe"
$py = "$venvPath\Scripts\python.exe"

# ── 3. 安装依赖 ─────────────────────────────────────────────
if (!$NoInstall) {
    Write-Host "📦 安装 Python 依赖..." -ForegroundColor Yellow
    $reqs = "$SCRIPT_DIR\requirements.txt"
    if (!(Test-Path $reqs)) {
        $reqs = "$SCRIPT_DIR\source\requirements.txt"
    }
    if (Test-Path $reqs) {
        & $pip install -r $reqs 2>&1 | Out-Null
    }
    & $pip install pyinstaller 2>&1 | Out-Null
    Write-Host "✅ 依赖安装完成" -ForegroundColor Green
}

# ── 4. 构建 .exe ────────────────────────────────────────────
Write-Host "🚀 编译 supermodel_router.exe ..." -ForegroundColor Yellow

# 找源码目录
$sourceDir = "$SCRIPT_DIR\source"
$runPy = "$SCRIPT_DIR\source\run_smr_pyinstaller.py"
$configYaml = "$SCRIPT_DIR\source\config.yaml"

if (!(Test-Path $runPy)) {
    # 从 deploy 模式或根目录
    $runPy = "$SCRIPT_DIR\run_smr_pyinstaller.py"
    $configYaml = "$SCRIPT_DIR\config.yaml"
    $sourceDir = "$SCRIPT_DIR"
}

if (!(Test-Path $runPy)) {
    Write-Host "❌ 未找到 run_smr_pyinstaller.py" -ForegroundColor Red
    Write-Host "   请将 build_package.ps1 放在 supermodel_router 项目根目录" -ForegroundColor Yellow
    exit 1
}

# 确保 supermodel_router 在 Python path 上
$env:PYTHONPATH = "$sourceDir;$env:PYTHONPATH"

& $py -m PyInstaller `
    --onefile `
    --name "supermodel_router" `
    --distpath "$SCRIPT_DIR" `
    --workpath "$SCRIPT_DIR\build" `
    --specpath "$SCRIPT_DIR\build" `
    --add-data "$configYaml;." `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import fastapi `
    --hidden-import pydantic `
    --hidden-import httpx `
    --hidden-import yaml `
    --collect-all supermodel_router `
    --console `
    $runPy

# ── 5. 验证 ────────────────────────────────────────────────
$exePath = "$SCRIPT_DIR\supermodel_router.exe"
if (Test-Path $exePath) {
    $sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host @"

✅ supermodel_router.exe 构建成功!
   📁 $exePath  ($sizeMB MB)

"@ -ForegroundColor Green
} else {
    Write-Host "❌ supermodel_router.exe 构建失败, 检查上方错误信息" -ForegroundColor Red
    exit 1
}

# ── 6. 打包 free-model-router (独立网关) ────────────────────
Write-Host ""
Write-Host "🚀 编译 free-model-router.exe ..." -ForegroundColor Yellow

$fmrSpec = "$sourceDir\fmr.spec"
$fmrRunPy = "$sourceDir\run_fmr_pyinstaller.py"
$fmrConfigExample = "$sourceDir\free_model_router\config.yaml.example"

if (!(Test-Path $fmrSpec)) {
    $fmrSpec = "$SCRIPT_DIR\fmr.spec"
    $fmrRunPy = "$SCRIPT_DIR\run_fmr_pyinstaller.py"
    $fmrConfigExample = "$SCRIPT_DIR\free_model_router\config.yaml.example"
}

if (Test-Path $fmrSpec) {
    # 用 spec 打包
    & $py -m PyInstaller `
        --clean `
        --noconfirm `
        --distpath "$SCRIPT_DIR" `
        --workpath "$SCRIPT_DIR\build_fmr" `
        --specpath "$SCRIPT_DIR\build_fmr" `
        $fmrSpec
} elseif (Test-Path $fmrRunPy) {
    # 命令行直接打包
    & $py -m PyInstaller `
        --onefile `
        --name "free-model-router" `
        --distpath "$SCRIPT_DIR" `
        --workpath "$SCRIPT_DIR\build_fmr" `
        --specpath "$SCRIPT_DIR\build_fmr" `
        --add-data "$fmrConfigExample;free_model_router" `
        --hidden-import httpx `
        --hidden-import yaml `
        --collect-all free_model_router `
        --console `
        $fmrRunPy
} else {
    Write-Host "⚠️  未找到 fmr.spec 或 run_fmr_pyinstaller.py, 跳过 fmr 打包" -ForegroundColor Yellow
}

# ── 7. 验证 fmr .exe ─────────────────────────────────────────
$fmrExe = "$SCRIPT_DIR\free-model-router.exe"
if (Test-Path $fmrExe) {
    $fmrSizeMB = [math]::Round((Get-Item $fmrExe).Length / 1MB, 1)
    Write-Host "✅ free-model-router.exe 构建成功! ($fmrSizeMB MB)" -ForegroundColor Green
} else {
    Write-Host "⚠️  free-model-router.exe 未生成 (可选模块)" -ForegroundColor Yellow
}

# ── 8. 最终输出 ──────────────────────────────────────────────
Write-Host @"

═══════════════════════════════════════════════
  构建完成 — v$VERSION
═══════════════════════════════════════════════

产物:
  • supermodel_router.exe  (主路由)
  • free-model-router.exe  (免费模型网关, 多 key 轮询)

用法 (supermodel_router):
  前台运行:  .\run.bat
  安装服务:  以管理员运行 .\install.ps1
  API:       http://127.0.0.1:1298
  管理面板:  http://127.0.0.1:1298/admin

用法 (free-model-router):
  前台运行:  .\free-model-router.exe --config config.yaml
  配置文件:  copy free_model_router\config.yaml.example config.yaml
  API:       http://127.0.0.1:5678
  管理面板:  http://127.0.0.1:5678/admin

"@ -ForegroundColor Green

# 清理构建缓存
Remove-Item "$SCRIPT_DIR\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$SCRIPT_DIR\build_fmr" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$SCRIPT_DIR\supermodel_router.spec" -Force -ErrorAction SilentlyContinue
Remove-Item "$SCRIPT_DIR\fmr.spec" -Force -ErrorAction SilentlyContinue