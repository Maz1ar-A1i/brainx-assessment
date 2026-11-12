$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Environment not initialized. Run .\setup.ps1 first."
}
$VersionText = (& $VenvPython --version 2>&1 | Out-String).Trim()
if ($VersionText -notmatch '^Python 3\.(12|13)\.') {
    throw "The local .venv must use Python 3.12 or 3.13 ($VersionText). Run .\setup.ps1 to recreate it cleanly."
}
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
