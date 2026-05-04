# Run AutoOutlook backend on Windows.
# Usage:  .\backend\run.ps1
#
# 1. Ensures netCDF4 is installed (one-time).
# 2. Starts the Flask service on http://127.0.0.1:8765.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $root

Write-Host "[AutoOutlook] checking netCDF4..." -ForegroundColor Cyan
$ncdfOk = $true
try {
    & python -c "import netCDF4" 2>$null
    if ($LASTEXITCODE -ne 0) { $ncdfOk = $false }
} catch { $ncdfOk = $false }

if (-not $ncdfOk) {
    Write-Host "[AutoOutlook] installing netCDF4 (one-time)..." -ForegroundColor Yellow
    & python -m pip install --quiet --no-input "netCDF4>=1.6"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install netCDF4. Run 'python -m pip install netCDF4' manually."
        exit 1
    }
}

Write-Host "[AutoOutlook] starting backend on http://127.0.0.1:8765 ..." -ForegroundColor Green
Set-Location $projectRoot
& python -m backend.server
