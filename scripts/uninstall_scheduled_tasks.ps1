# Removes the scheduled tasks created by install_scheduled_tasks.ps1 and
# stops the bot. Trade history in data\trader.db is left untouched.

$names = @("MemecoinTraderEngine", "MemecoinTraderDashboard")
foreach ($name in $names) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed task: $name"
    } else {
        Write-Host "Task not found (already removed?): $name"
    }
}

# Stop-ScheduledTask only stops the task's wrapper process, not the
# python.exe child it launched -- that can keep running in the background
# indefinitely otherwise, so find and stop it explicitly.
Start-Sleep -Seconds 1
$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "*memecoin-trader*" }
foreach ($proc in $procs) {
    Write-Host "Stopping orphaned process (PID $($proc.ProcessId))..."
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Scheduled tasks removed and the engine/dashboard processes stopped."
Write-Host "Trade history in data\trader.db is untouched."
