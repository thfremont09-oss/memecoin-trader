# One-time setup: registers two Windows Scheduled Tasks so the trading
# engine and dashboard start automatically whenever you log in, and restart
# themselves if they ever crash. Run this once from inside the repo folder:
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_scheduled_tasks.ps1
#
# To undo: .\scripts\uninstall_scheduled_tasks.ps1

$repoRoot = Split-Path -Parent $PSScriptRoot

function Register-WatchdogTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath
    )

    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Memecoin Trader: keeps $TaskName running continuously (installed by install_scheduled_tasks.ps1)." `
        | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Registered and started task: $TaskName"
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual environment not found at $venvPython. Run the Setup steps in README.md first (python -m venv .venv, then pip install -r requirements.txt)."
    exit 1
}

Register-WatchdogTask -TaskName "MemecoinTraderEngine" -ScriptPath (Join-Path $repoRoot "scripts\run_engine_forever.ps1")
Register-WatchdogTask -TaskName "MemecoinTraderDashboard" -ScriptPath (Join-Path $repoRoot "scripts\run_dashboard_forever.ps1")

Write-Host ""
Write-Host "Done. Both tasks are running now and will start automatically every time"
Write-Host "you log in to Windows. They restart themselves within 5 seconds if they crash."
Write-Host ""
Write-Host "Dashboard:  http://127.0.0.1:8787"
Write-Host "Logs:       data\engine_watchdog.log and data\dashboard_watchdog.log"
Write-Host "Task Scheduler: search 'Task Scheduler' in the Start menu to see/stop them,"
Write-Host "or run .\scripts\uninstall_scheduled_tasks.ps1 to remove them."
Write-Host ""
Write-Host "One more thing: Windows sleep will pause everything. To stop your PC from"
Write-Host "sleeping while plugged in, run (as Administrator if it's rejected):"
Write-Host "  powercfg /change standby-timeout-ac 0"
