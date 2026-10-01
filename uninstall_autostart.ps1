$ErrorActionPreference = "Stop"
$taskName = "VNStockTelegramBot"
$startupLauncher = Join-Path ([Environment]::GetFolderPath("Startup")) "VNStockTelegramBot.cmd"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Đã gỡ tự khởi động: $taskName"
} else {
    Write-Host "Không tìm thấy tác vụ $taskName"
}
Remove-Item -LiteralPath $startupLauncher -Force -ErrorAction SilentlyContinue
