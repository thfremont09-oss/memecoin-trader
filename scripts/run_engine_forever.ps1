# Runs the trading engine loop, restarting it automatically if it ever
# crashes (e.g. a transient network error). Meant to be launched by the
# "MemecoinTraderEngine" scheduled task created by install_scheduled_tasks.ps1,
# but you can also just run it directly in a terminal to watch it live.

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual environment not found at $venvPython. Run the Setup steps in README.md first."
    exit 1
}

$dataDir = Join-Path $repoRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$watchdogLog = Join-Path $dataDir "engine_watchdog.log"

while ($true) {
    "$(Get-Date -Format o) [watchdog] starting engine" | Add-Content -Path $watchdogLog
    & $venvPython -u -m memecoin_trader.cli run *>> $watchdogLog
    "$(Get-Date -Format o) [watchdog] engine exited (code $LASTEXITCODE); restarting in 5s" | Add-Content -Path $watchdogLog
    Start-Sleep -Seconds 5
}
