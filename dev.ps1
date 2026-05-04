# AutoOutlook — unified dev launcher
# Opens two terminal windows: Flask backend (hot-reload) + Vite frontend (HMR).
# Usage:  .\dev.ps1
# Stop:   close the two spawned windows, or Ctrl+C in each.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python. Run: python -m venv .venv && .\.venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}

Write-Host ""
Write-Host "  AutoOutlook dev server" -ForegroundColor Cyan
Write-Host "  ----------------------" -ForegroundColor Cyan
Write-Host "  Backend  ->  http://127.0.0.1:8765  (Flask, hot-reload)" -ForegroundColor Green
Write-Host "  Frontend ->  http://localhost:5173   (Vite, HMR)" -ForegroundColor Green
Write-Host ""

# Backend: FLASK_DEBUG=1 enables Werkzeug file-watcher; any .py save restarts Flask
$backendCmd = "Set-Location '$root'; `$env:FLASK_DEBUG='1'; & '$python' -m backend.server"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd `
    -WindowStyle Normal

# Frontend: Vite does HMR out of the box
$frontendCmd = "Set-Location '$root'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd `
    -WindowStyle Normal

Write-Host "  Both windows launched. This window can be closed." -ForegroundColor Gray
Write-Host ""
