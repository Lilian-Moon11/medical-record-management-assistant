# Local Patient Advocate — Windows Portable Build Script
# Run from the repository root: .\build\build_windows.ps1

$ErrorActionPreference = "Stop"

Write-Host "Building LPA portable (Windows)..." -ForegroundColor Cyan

# Ensure PyInstaller is available
if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
    pip install pyinstaller
}

# Clean previous build
pyinstaller --clean build/lpa.spec

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed. Check output above."
}

# Zip the onedir output
$zipDest = "dist\LPA-portable-win.zip"
if (Test-Path $zipDest) { Remove-Item $zipDest -Force }

Compress-Archive -Path "dist\lpa" -DestinationPath $zipDest -Force

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "  Folder : dist\lpa\lpa.exe"
Write-Host "  Archive: $zipDest"
