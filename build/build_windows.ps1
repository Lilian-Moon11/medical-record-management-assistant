# Medical Record Management Assistant — Windows Portable Build Script
# Run from the repository root: .\build\build_windows.ps1

$ErrorActionPreference = "Stop"

Write-Host "Building MRMA portable (Windows)..." -ForegroundColor Cyan

# Resolve pyinstaller from the venv (works without system-level install)
$PyInstaller = ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    Write-Host "Installing PyInstaller into venv..." -ForegroundColor Yellow
    .venv\Scripts\pip.exe install pyinstaller
}

# Clean previous build
& $PyInstaller --noconfirm --clean build/mrma.spec
$buildExit = $LASTEXITCODE

if ($buildExit -ne 0) {
    Write-Error "PyInstaller failed (exit $buildExit). Check output above."
    exit $buildExit
}

# Zip the onedir output
$zipDest = "dist\MRMA-portable-win.zip"
if (Test-Path $zipDest) { Remove-Item $zipDest -Force }

Compress-Archive -Path "dist\mrma" -DestinationPath $zipDest -Force

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green
Write-Host "  Folder : dist\mrma\mrma.exe"
Write-Host "  Archive: $zipDest"
