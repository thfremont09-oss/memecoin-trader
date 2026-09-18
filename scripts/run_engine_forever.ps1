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

# Guards against two copies of this script fighting over the same log file
# (e.g. a Task-Scheduler-managed instance plus someone manually running this
# file directly) -- that collision causes IOExceptions on every log write and
# looks like the engine is silently doing nothing. Automatically released if
# the owning process exits or crashes, so no stale-lock cleanup is needed.
$mutex = New-Object System.Threading.Mutex($false, "Global\MemecoinTraderEngineWatchdog")
if (-not $mutex.WaitOne(0)) {
    Write-Error "Another copy of run_engine_forever.ps1 is already running. Not starting a second one -- stop that one first (Task Scheduler, or Ctrl+C its window)."
    exit 1
}

function Write-WatchdogLog {
    param([string]$Message)
    for ($i = 0; $i -lt 5; $i++) {
        try {
            $Message | Add-Content -Path $watchdogLog -ErrorAction Stop
            return
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
}

$stdOutLog = Join-Path $dataDir "engine_stdout.log"
$stdErrLog = Join-Path $dataDir "engine_stderr.log"

try {
    while ($true) {
        Write-WatchdogLog "$(Get-Date -Format o) [watchdog] starting engine"
        # Start-Process's own -RedirectStandardOutput/-Error (backed by .NET's
        # Process class) instead of PowerShell's `*>>` operator, which turned
        # out to silently swallow output when launched hidden by Task
        # Scheduler -- the engine was running fine the whole time (see
        # data\trader.log, written directly by Python itself), only this
        # redirect path was broken.
        $proc = Start-Process -FilePath $venvPython `
            -ArgumentList @("-u", "-m", "memecoin_trader.cli", "run") `
            -RedirectStandardOutput $stdOutLog -RedirectStandardError $stdErrLog `
            -NoNewWindow -PassThru -Wait
        Write-WatchdogLog "$(Get-Date -Format o) [watchdog] engine exited (code $($proc.ExitCode)); restarting in 5s"
        Start-Sleep -Seconds 5
    }
} finally {
    $mutex.ReleaseMutex()
}
