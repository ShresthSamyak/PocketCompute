# PocketCompute one-command launcher (Windows).
#   Right-click > Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1
# Creates a local virtual environment on first run, installs dependencies,
# then starts the agent and shows the pairing QR code.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$agent = Join-Path $root "agent"
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "PocketCompute launcher" -ForegroundColor Cyan

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $venv
    & $py -m pip install --upgrade pip --quiet
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    & $py -m pip install -r (Join-Path $agent "requirements.txt") --quiet
}

# Optional: GPU metrics support (NVIDIA). Ignored if it fails.
& $py -c "import pynvml" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $py -m pip install nvidia-ml-py --quiet 2>$null
}

Set-Location $agent
$port = if ($args.Count -ge 1) { $args[0] } else { "8765" }
& $py -m pocketcompute --port $port
