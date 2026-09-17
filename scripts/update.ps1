# Pulls the latest code, updates dependencies, and restarts the engine +
# dashboard tasks so new changes actually take effect. Run this any time
# you're told there's an update to grab:
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\update.ps1

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "Pulling latest changes..."
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Error "git pull failed (see above) -- not touching anything else until that's resolved."
    exit 1
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual environment not found at $venvPython. Run the Setup steps in README.md first."
    exit 1
}

Write-Host "Updating Python dependencies..."
& $venvPython -m pip install -q -r requirements.txt

& $venvPython -c "import sklearn" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Updating ML dependencies too (scikit-learn was already installed)..."
    & $venvPython -m pip install -q -r requirements-ml.txt
}

Write-Host "Restarting the engine and dashboard..."
$tasks = @("MemecoinTraderEngine", "MemecoinTraderDashboard")
$anyFailed = $false
foreach ($task in $tasks) {
    if (-not (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)) {
        Write-Warning "Task '$task' isn't registered yet -- run .\scripts\install_scheduled_tasks.ps1 first."
        $anyFailed = $true
        continue
    }
    try {
        Stop-ScheduledTask -TaskName $task -ErrorAction Stop
    } catch {
        # wasn't running -- fine, we're about to start it anyway
    }
    Start-Sleep -Seconds 1
    try {
        Start-ScheduledTask -TaskName $task -ErrorAction Stop
        Write-Host "Restarted: $task"
    } catch {
        Write-Error "Failed to restart '$task': $($_.Exception.Message). Try running this from an Administrator PowerShell."
        $anyFailed = $true
    }
}

if ($anyFailed) {
    exit 1
}

Write-Host ""
Write-Host "Update complete. Dashboard: http://127.0.0.1:8787"
