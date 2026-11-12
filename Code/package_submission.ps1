$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectName = Split-Path -Leaf $ProjectRoot
$ParentDir = Split-Path -Parent $ProjectRoot
$OutputZip = Join-Path $ParentDir "CaptchaLab_AI_Submission.zip"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("captchalab-package-" + [guid]::NewGuid().ToString("N"))
$Stage = Join-Path $TempRoot $ProjectName

try {
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null

    # Only copy source and documentation. Never package local secrets, local databases,
    # virtual environments, generated screenshots, bytecode, caches, or benchmark outputs.
    $ExcludeDirs = @(".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "_root", "logs")

    Get-ChildItem -LiteralPath $ProjectRoot -Force -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($ProjectRoot.Length).TrimStart('\','/')
        $parts = $relative -split '[\\/]'
        if ($parts | Where-Object { $ExcludeDirs -contains $_ }) { return }
        $IsPrivateDotEnv = $_.Name -eq ".env" -or ($_.Name -like ".env.*" -and $_.Name -ne ".env.example")
        $IsRuntimeFile = $_.Name -match '(?i)\.(db|sqlite|sqlite3)(-(wal|shm))?$' -or $_.Extension -in @(".pyc", ".pyo", ".log")
        if (-not $_.PSIsContainer -and ($IsPrivateDotEnv -or $IsRuntimeFile)) { return }
        if ($relative -match '^(artifacts|data[\\/]synthetic_dataset[\\/]images)[\\/]') { return }
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path (Join-Path $Stage $relative) -Force | Out-Null
        } else {
            $destination = Join-Path $Stage $relative
            $destinationDir = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }

    New-Item -ItemType Directory -Path (Join-Path $Stage "artifacts") -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $Stage "artifacts/.gitkeep") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Stage "data/synthetic_dataset/images") -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $Stage "data/synthetic_dataset/images/.gitkeep") -Force | Out-Null

    if (Test-Path $OutputZip) { Remove-Item -LiteralPath $OutputZip -Force }
    # ZipFile.CreateFromDirectory includes hidden dotfiles such as .env.example and
    # .gitignore. Compress-Archive can omit hidden files on Windows PowerShell.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $Stage,
        $OutputZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    Write-Host "Clean submission archive created:" -ForegroundColor Green
    Write-Host $OutputZip -ForegroundColor Cyan
    Write-Host "Excluded .env, .venv, databases, caches, screenshots and generated image files." -ForegroundColor Green
} finally {
    if (Test-Path $TempRoot) { Remove-Item -LiteralPath $TempRoot -Recurse -Force }
}
