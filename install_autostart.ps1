$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $projectDir "run_forever.ps1"
$taskName = "VNStockTelegramBot"
$startupDir = [Environment]::GetFolderPath("Startup")
$startupLauncher = Join-Path $startupDir "VNStockTelegramBot.cmd"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Runner not found: $runner"
}

try {
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask -and $existingTask.State -eq "Running") {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $deadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 500
            $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        } while ($existingTask.State -eq "Running" -and (Get-Date) -lt $deadline)
        if ($existingTask.State -eq "Running") {
            throw "Existing bot task did not stop within 15 seconds"
        }
    }
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $runner
    )
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
    $installed = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if ($installed.State -ne "Running") {
        throw "Task state is $($installed.State)"
    }
    Remove-Item -LiteralPath $startupLauncher -Force -ErrorAction SilentlyContinue
    Write-Host "Installed with Windows Task Scheduler: $taskName"
}
catch {
    Write-Host "Task Scheduler unavailable; using the Windows Startup folder."
    $lines = @(
        "@echo off",
        ('start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $runner)
    )
    Set-Content -LiteralPath $startupLauncher -Value $lines -Encoding ASCII
    Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ('"{0}"' -f $runner)
    )
    Start-Sleep -Seconds 3
    Write-Host "Installed in Windows Startup: $startupLauncher"
}

Write-Host "Bot launch requested. Wait 15 seconds, then send /start in Telegram."
Write-Host "Log: $(Join-Path $projectDir 'runtime\supervisor.log')"
