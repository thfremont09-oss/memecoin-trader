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

Write-Host ""
Write-Host "Scheduled tasks removed and stopped (this also ends the engine/dashboard"
Write-Host "processes they were running). Trade history in data\trader.db is untouched."
