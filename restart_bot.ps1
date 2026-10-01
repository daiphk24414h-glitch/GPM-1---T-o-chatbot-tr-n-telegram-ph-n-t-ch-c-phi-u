$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $projectDir "run_forever.ps1"
$python = Join-Path $env:USERPROFILE ".venv\Scripts\python.exe"
$stopped = 0
$runtime = Join-Path $projectDir "runtime"

foreach ($pidName in @("bot.pid", "supervisor.pid")) {
    $pidFile = Join-Path $runtime $pidName
    if (Test-Path -LiteralPath $pidFile) {
        $savedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$savedPid) -and $savedPid -gt 0) {
            $process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
            if ($process) {
                Stop-Process -Id $savedPid -Force -ErrorAction SilentlyContinue
                $stopped++
            }
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

try {
    Stop-ScheduledTask -TaskName "VNStockTelegramBot" -ErrorAction SilentlyContinue
} catch {
    # Startup-folder installations do not require Task Scheduler access.
}

try {
    for ($pass = 0; $pass -lt 3; $pass++) {
        $targets = Get-CimInstance Win32_Process | Where-Object {
            if ($_.ProcessId -eq $PID -or -not $_.CommandLine) { return $false }
            $projectRunner = ($_.CommandLine.IndexOf($projectDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                              $_.CommandLine -match "run_forever\.ps1")
            # Older runners launch `python.exe main.py` after changing their
            # working directory, so the project path is absent from the child
            # command line. Match the exact shared venv executable plus main.py.
            $orphanBot = ($_.Name -match "^python(w)?\.exe$" -and
                          $_.ExecutablePath -and
                          $_.ExecutablePath.Equals($python, [System.StringComparison]::OrdinalIgnoreCase) -and
                          $_.CommandLine -match '(^|[\\\s"])main\.py([\s"]|$)')
            return $projectRunner -or $orphanBot
        }
        foreach ($target in $targets) {
            Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
            $stopped++
        }
        if (-not $targets) { break }
        Start-Sleep -Seconds 2
    }
} catch {
    if ($stopped -eq 0) {
        Write-Host "Cannot inspect the legacy background process. Run PowerShell as administrator once; future versions use PID files."
        exit 1
    }
}

Start-Sleep -Seconds 3
Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ('"{0}"' -f $runner)
)
Start-Sleep -Seconds 8
Write-Host "Stopped $stopped old process(es) and launched the new version."
Write-Host "Send /start in Telegram to verify."
Write-Host "Log: $(Join-Path $projectDir 'runtime\supervisor.log')"
