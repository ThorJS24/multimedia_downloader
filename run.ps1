# Spotify Downloader Studio - development launcher: opens the app directly (no console, no menu).
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectRoot

# Keep every write on this drive: temp files stay inside the project
$env:TEMP = "$ProjectRoot\temp"
$env:TMP = "$ProjectRoot\temp"
foreach ($d in "temp", "downloads", ".cache") {
    if (-not (Test-Path "$ProjectRoot\$d")) { New-Item -ItemType Directory -Path "$ProjectRoot\$d" -Force | Out-Null }
}

$PythonW = "$ProjectRoot\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $PythonW)) {
    Write-Host "Error: virtual environment not found at $PythonW" -ForegroundColor Red
    exit 1
}

# pythonw = no console window; any extra arguments (e.g. --browser) are passed through
Start-Process -FilePath $PythonW -ArgumentList (@("main.py") + $args) -WorkingDirectory $ProjectRoot
