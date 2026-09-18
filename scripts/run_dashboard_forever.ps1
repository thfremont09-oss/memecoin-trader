# Runs the local web dashboard, restarting it automatically if it ever
# crashes. Meant to be launched by the "MemecoinTraderDashboard" scheduled
# task created by install_scheduled_tasks.ps1.
#
# Binds to 127.0.0.1 (this PC only) by default. If you want to check it from
# your phone on the same home network, change -host to 0.0.0.0 below and set
# DASHBOARD_USERNAME/DASHBOARD_PASSWORD as real Windows environment variables
# first (System Properties > Environment Variables), since anyone on your
# network could otherwise reach it.

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual environment not found at $venvPython. Run the Setup steps in README.md first."
    exit 1
}

$dataDir = Join-Path $repoRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$watchdogLog = Join-Path $dataDir "dashboard_watchdog.log"

while ($true) {
    "$(Get-Date -Format o) [watchdog] starting dashboard" | Add-Content -Path $watchdogLog
    & $venvPython -u -m memecoin_trader.cli dashboard --host 127.0.0.1 --port 8787 *>> $watchdogLog
    "$(Get-Date -Format o) [watchdog] dashboard exited (code $LASTEXITCODE); restarting in 5s" | Add-Content -Path $watchdogLog
    Start-Sleep -Seconds 5
}
