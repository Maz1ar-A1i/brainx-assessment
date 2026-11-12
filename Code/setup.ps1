$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "CaptchaLab AI setup (CPython 3.12 or 3.13 x64)" -ForegroundColor Cyan

# Use a known, reproducible interpreter. Do not reuse a venv copied from another PC.
$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $PyLauncher) {
    throw "Python Launcher (py.exe) was not found. Install Python 3.12 x64 from https://www.python.org/downloads/ and enable the Python Launcher."
}

$SelectedPython = $null
foreach ($Candidate in @("3.12", "3.13")) {
    $VersionText = (& py -$Candidate --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $VersionText -match "^Python $([regex]::Escape($Candidate))\.") {
        $ArchitectureText = (& py -$Candidate -c "import struct; print(struct.calcsize('P') * 8)" 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $ArchitectureText -eq "64") {
            $SelectedPython = $Candidate
            break
        }
    }
}
if (-not $SelectedPython) {
    throw "Python 3.12 or 3.13 (64-bit) is required. Install one supported version and rerun setup."
}
$VersionText = (& py -$SelectedPython --version 2>&1 | Out-String).Trim()
$ArchitectureText = (& py -$SelectedPython -c "import struct; print(struct.calcsize('P') * 8)" 2>&1 | Out-String).Trim()
Write-Host "Using $VersionText ($ArchitectureText-bit)" -ForegroundColor Green

# Never distribute or reuse a venv: compiled extensions are specific to Python and Windows.
if (Test-Path ".venv") {
    Write-Host "Removing existing .venv to eliminate incompatible compiled packages..." -ForegroundColor Yellow
    Remove-Item -LiteralPath ".venv" -Recurse -Force
}

Write-Host "[1/4] Creating a clean virtual environment..." -ForegroundColor Cyan
& py -$SelectedPython -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Could not create .venv with the selected supported Python version." }
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "[2/4] Updating pip tooling..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "pip tooling installation failed." }

Write-Host "[3/4] Installing pinned application and test dependencies..." -ForegroundColor Cyan
& $VenvPython -m pip install --no-cache-dir -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check your internet connection and rerun setup." }

Write-Host "[4/4] Installing Playwright Chromium..." -ForegroundColor Cyan
& $VenvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed." }

# Verify the interpreter and compiled packages are importable before declaring success.
& $VenvPython -c "import sys, fastapi, pydantic_core, numpy, cv2, greenlet, playwright; print('Python:', sys.version.split()[0]); print('NumPy:', numpy.__version__); print('OpenCV:', cv2.__version__); print('Playwright:', playwright.__file__)"
if ($LASTEXITCODE -ne 0) { throw "Post-install verification failed. Remove .venv and rerun setup." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Add your own Gemini key only if you intend to use the optional Gemini fallback." -ForegroundColor Yellow
} else {
    Write-Host "Existing .env preserved. Check ALLOWED_TARGET_HOSTS and rotate any key that has been shared." -ForegroundColor Yellow
    $EnvContent = Get-Content ".env" -Raw
    if ($EnvContent -match '(?im)^\s*ALLOWED_TARGET_HOSTS\s*=.*(?:\*|\ball\b)') {
        Write-Warning "Your .env contains a wildcard host entry. The application ignores wildcards. Remove it and list only owned/authorized test hostnames."
    }
    if ($EnvContent -match '(?im)^\s*GEMINI_API_KEY\s*=\s*\S+') {
        Write-Warning "A Gemini key is configured. Never include .env in the submission ZIP or source-control repository. Revoke/regenerate any key previously shared."
    }
}

Write-Host "Setup completed. Run .\run.ps1" -ForegroundColor Green
