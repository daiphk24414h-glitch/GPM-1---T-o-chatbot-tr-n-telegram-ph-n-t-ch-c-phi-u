$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:USERPROFILE ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Không tìm thấy môi trường Python tại $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectDir ".env"))) {
    throw "Thiếu file .env trong $projectDir"
}

$env:PYTHONUTF8 = "1"
$env:MPLCONFIGDIR = Join-Path $projectDir "runtime\mpl"
Set-Location -LiteralPath $projectDir
& $python "main.py"
