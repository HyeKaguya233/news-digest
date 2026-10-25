$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "..\venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Push-Location $root
try {
    & $python -m pip install -r requirements.txt
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name NewsDigest `
        --icon assets\news.ico `
        --add-data "assets\news.ico;assets" `
        news_tray.py
    Write-Host "Built: $root\dist\NewsDigest.exe"
}
finally {
    Pop-Location
}
