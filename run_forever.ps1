$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:USERPROFILE ".venv\Scripts\python.exe"
$runtime = Join-Path $projectDir "runtime"
$logFile = Join-Path $runtime "supervisor.log"
$supervisorPidFile = Join-Path $runtime "supervisor.pid"
$createdNew = $false
$supervisorMutex = New-Object System.Threading.Mutex($true, "Local\VNStockTelegramBotSupervisor", [ref]$createdNew)
if (-not $createdNew) {
    exit 0
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$PID | Set-Content -LiteralPath $supervisorPidFile -Encoding ascii
$env:PYTHONUTF8 = "1"
$env:MPLCONFIGDIR = Join-Path $runtime "mpl"
Set-Location -LiteralPath $projectDir

if (-not (Test-Path -LiteralPath $python)) {
    "$(Get-Date -Format s) Python not found: $python" | Add-Content -LiteralPath $logFile
    exit 1
}

try {
    while ($true) {
        "$(Get-Date -Format s) Starting Telegram bot" | Add-Content -LiteralPath $logFile -Encoding UTF8
        & $python "main.py" *>> $logFile
        $exitCode = $LASTEXITCODE
        "$(Get-Date -Format s) Bot stopped with exit code $exitCode; restarting in 15 seconds" | Add-Content -LiteralPath $logFile -Encoding UTF8
        Start-Sleep -Seconds 15
    }
}
finally {
    Remove-Item -LiteralPath $supervisorPidFile -Force -ErrorAction SilentlyContinue
    $supervisorMutex.ReleaseMutex()
    $supervisorMutex.Dispose()
}
