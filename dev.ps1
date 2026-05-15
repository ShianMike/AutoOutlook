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

$artifactBucket = "autooutlook-artifacts-project-f47ca9d9-31bc-4a21-963"
$artifactProject = "project-f47ca9d9-31bc-4a21-963"
$artifactRoot = Join-Path $root "backend\artifacts"
$gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
if ($gcloud) {
    Write-Host "  Syncing latest production artifacts from gs://$artifactBucket ..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
    foreach ($name in @("latest", "latest_incremental", "latest_incremental_complete")) {
        $target = Join-Path $artifactRoot $name
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        & gcloud storage rsync -r "gs://$artifactBucket/$name" $target --project $artifactProject | Out-Host
    }
} else {
    Write-Warning "gcloud was not found. Local dev will use any existing files under backend\artifacts."
}

# Backend: FLASK_DEBUG=1 enables Werkzeug file-watcher; any .py save restarts Flask
$backendCmd = @"
Set-Location '$root';
`$env:FLASK_DEBUG='1';
`$env:AUTOOUTLOOK_FORECAST_SOURCE='artifact';
`$env:AUTOOUTLOOK_ENABLE_LIVE_BUILD='false';
& '$python' -m backend.server
"@
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd `
    -WindowStyle Normal

# Frontend: Vite does HMR out of the box
$frontendCmd = "Set-Location '$root'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd `
    -WindowStyle Normal

Write-Host "  Both windows launched. This window can be closed." -ForegroundColor Gray
Write-Host ""
