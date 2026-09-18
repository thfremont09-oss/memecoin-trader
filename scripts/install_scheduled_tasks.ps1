# One-time setup: registers two Windows Scheduled Tasks so the trading
# engine and dashboard start automatically whenever you log in, and restart
# themselves if they ever crash. Run this once from inside the repo folder:
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_scheduled_tasks.ps1
#
# To undo: .\scripts\uninstall_scheduled_tasks.ps1

$repoRoot = Split-Path -Parent $PSScriptRoot

$isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isElevated) {
    Write-Warning "This PowerShell window is not running as Administrator."
    Write-Warning "Register-ScheduledTask commonly fails with 'Access is denied' without it."
    Write-Warning "If the steps below fail, close this window, right-click PowerShell, choose"
    Write-Warning "'Run as Administrator', and re-run this script from there instead."
    Write-Host ""
}

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
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    }

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Memecoin Trader: keeps $TaskName running continuously (installed by install_scheduled_tasks.ps1)." `
        -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Registered and started task: $TaskName"
    return $true
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual environment not found at $venvPython. Run the Setup steps in README.md first (python -m venv .venv, then pip install -r requirements.txt)."
    exit 1
}

# Stop-ScheduledTask above only stops the task's wrapper process, not the
# python.exe child it launched -- clean up any still running before we
# start fresh copies, so re-running this script never leaves duplicates.
$orphans = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "*memecoin-trader*" }
foreach ($proc in $orphans) {
    Write-Host "Stopping orphaned process (PID $($proc.ProcessId))..."
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

$allSucceeded = $true
try {
    Register-WatchdogTask -TaskName "MemecoinTraderEngine" -ScriptPath (Join-Path $repoRoot "scripts\run_engine_forever.ps1") | Out-Null
} catch {
    Write-Error "Failed to register MemecoinTraderEngine: $($_.Exception.Message)"
    $allSucceeded = $false
}
try {
    Register-WatchdogTask -TaskName "MemecoinTraderDashboard" -ScriptPath (Join-Path $repoRoot "scripts\run_dashboard_forever.ps1") | Out-Null
} catch {
    Write-Error "Failed to register MemecoinTraderDashboard: $($_.Exception.Message)"
    $allSucceeded = $false
}

if (-not $allSucceeded) {
    Write-Host ""
    Write-Host "One or both tasks failed to register (see errors above) -- nothing is" -ForegroundColor Red
    Write-Host "running yet. Re-run this script from an Administrator PowerShell window." -ForegroundColor Red
    exit 1
}

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
