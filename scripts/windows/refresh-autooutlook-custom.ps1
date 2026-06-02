[CmdletBinding()]
param(
    [string]$Cycle = "latest",
    [string]$StartValid = "latest",
    [string]$EndValid = "latest",
    [string]$OutputDir = "",
    [switch]$Force,
    [switch]$NoCache,
    [switch]$Serve,
    [switch]$BuildFrontend,
    [switch]$IncludeHgt500,
    [switch]$OmitHgt500
)

$ErrorActionPreference = "Stop"

function Import-EnvFileIfPresent {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -le 0) {
            continue
        }
        $key = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim().Trim('"')
        Set-Item -Path "Env:$key" -Value $value
    }
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Script
    )
    Write-Host "==> $Name"
    & $Script
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @()
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath exited with code $LASTEXITCODE"
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Get-PythonExecutable {
    $configured = [Environment]::GetEnvironmentVariable("AUTOOUTLOOK_PYTHON", "Process")
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        return $configured
    }

    $venvPython = Join-Path $script:VenvDir "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        return $pyCommand.Source
    }

    throw "No Python executable found. Install Python 3.11+ or set AUTOOUTLOOK_PYTHON."
}

function Ensure-PythonEnvironment {
    $basePython = Get-PythonExecutable
    $venvPython = Join-Path $script:VenvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-Item -ItemType Directory -Force -Path $script:VenvDir | Out-Null
        Invoke-NativeCommand $basePython @("-m", "venv", $script:VenvDir)
    }

    $requirementsPath = Join-Path $script:RepoDir "backend\requirements.txt"
    $stateHashPath = Join-Path $script:StateDir "requirements.sha256"
    $currentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $requirementsPath).Hash
    $previousHash = if (Test-Path -LiteralPath $stateHashPath) { Get-Content -LiteralPath $stateHashPath -Raw } else { "" }

    if ($currentHash -ne $previousHash.Trim()) {
        Invoke-NativeCommand $venvPython @("-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools")
        Invoke-NativeCommand $venvPython @("-m", "pip", "install", "-r", $requirementsPath)
        $currentHash | Set-Content -LiteralPath $stateHashPath -Encoding ASCII
    }

    return $venvPython
}

function Ensure-NodeDependencies {
    $packageLockPath = Join-Path $script:RepoDir "package-lock.json"
    $stateHashPath = Join-Path $script:StateDir "package-lock.sha256"
    $nodeModulesPath = Join-Path $script:RepoDir "node_modules"
    $currentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $packageLockPath).Hash
    $previousHash = if (Test-Path -LiteralPath $stateHashPath) { Get-Content -LiteralPath $stateHashPath -Raw } else { "" }

    if ((-not (Test-Path -LiteralPath $nodeModulesPath)) -or $currentHash -ne $previousHash.Trim()) {
        Invoke-NativeCommand "npm" @("ci")
        $currentHash | Set-Content -LiteralPath $stateHashPath -Encoding ASCII
    }
}

function Start-LocalDevServers {
    param(
        [string]$Python,
        [string]$ArtifactDir,
        [string]$LogDir
    )

    $backendLog = Join-Path $LogDir "custom-backend.log"
    $frontendLog = Join-Path $LogDir "custom-frontend.log"
    Remove-Item -LiteralPath $backendLog, $frontendLog -ErrorAction SilentlyContinue

    $backendCmd = @"
Set-Location '$script:RepoDir';
`$env:FLASK_DEBUG='1';
`$env:AUTOOUTLOOK_FORECAST_SOURCE='artifact';
`$env:AUTOOUTLOOK_ENABLE_LIVE_BUILD='false';
`$env:AUTOOUTLOOK_ARTIFACT_DIR='$ArtifactDir';
`$env:AUTOOUTLOOK_INCREMENTAL_ARTIFACT_DIR='$ArtifactDir';
`$env:AUTOOUTLOOK_INCREMENTAL_COMPLETE_ARTIFACT_DIR='$ArtifactDir';
`$env:AUTOOUTLOOK_PORT='8765';
& '$Python' -m backend.server *>&1 | Tee-Object -FilePath '$backendLog'
"@
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd -WindowStyle Hidden

    $frontendCmd = @"
Set-Location '$script:RepoDir';
npm run dev *>&1 | Tee-Object -FilePath '$frontendLog'
"@
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd -WindowStyle Hidden
}

$envFile = [Environment]::GetEnvironmentVariable("AUTOOUTLOOK_ENV_FILE", "Process")
if ([string]::IsNullOrWhiteSpace($envFile)) {
    $envFile = Join-Path $env:ProgramData "AutoOutlook\refresh.env"
}
Import-EnvFileIfPresent -Path $envFile

$script:RepoDir = if ($env:AUTOOUTLOOK_REPO_DIR) { $env:AUTOOUTLOOK_REPO_DIR } else { (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$script:VenvDir = if ($env:AUTOOUTLOOK_VENV_DIR) { $env:AUTOOUTLOOK_VENV_DIR } else { Join-Path $env:ProgramData "AutoOutlook\.venv" }
$script:StateDir = if ($env:AUTOOUTLOOK_STATE_DIR) { Join-Path $env:AUTOOUTLOOK_STATE_DIR "custom" } else { Join-Path $env:ProgramData "AutoOutlook\state\custom" }
$logDir = if ($env:AUTOOUTLOOK_LOG_DIR) { Join-Path $env:AUTOOUTLOOK_LOG_DIR "custom" } else { Join-Path $env:ProgramData "AutoOutlook\logs\custom" }
$hrrrCacheDir = if ($env:AUTOOUTLOOK_HRRR_CACHE_DIR) { Join-Path $env:AUTOOUTLOOK_HRRR_CACHE_DIR "custom" } else { Join-Path $env:ProgramData "AutoOutlook\cache\hrrr_selected_custom" }

$env:AUTOOUTLOOK_HOUR_WORKERS = if ($env:AUTOOUTLOOK_HOUR_WORKERS) { $env:AUTOOUTLOOK_HOUR_WORKERS } else { "3" }
$env:AUTOOUTLOOK_RANGE_WORKERS = if ($env:AUTOOUTLOOK_RANGE_WORKERS) { $env:AUTOOUTLOOK_RANGE_WORKERS } else { "4" }
$env:AUTOOUTLOOK_GRID_STRIDE = if ($env:AUTOOUTLOOK_GRID_STRIDE) { $env:AUTOOUTLOOK_GRID_STRIDE } else { "2" }
$env:AUTOOUTLOOK_TILE_STRIDE = if ($env:AUTOOUTLOOK_TILE_STRIDE) { $env:AUTOOUTLOOK_TILE_STRIDE } else { "1" }

New-Item -ItemType Directory -Force -Path $script:StateDir, $logDir, $hrrrCacheDir | Out-Null
Set-Location -LiteralPath $script:RepoDir

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $script:RepoDir "backend\artifacts\custom_latest_incremental"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

$transcriptPath = Join-Path $logDir ("custom-refresh-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$transcriptStarted = $false

try {
    Start-Transcript -Path $transcriptPath -Append | Out-Null
    $transcriptStarted = $true

    $python = Invoke-Step "Prepare Python dependencies" { Ensure-PythonEnvironment }
    if ($BuildFrontend -or $Serve) {
        Invoke-Step "Prepare frontend dependencies" { Ensure-NodeDependencies }
    }
    Invoke-Step "Bootstrap runtime hazard models" {
        Invoke-NativeCommand $python @("-m", "backend.ml.bootstrap_models")
    }

    $generationArgs = @(
        "scripts/generate-custom-hrrr-artifacts.py",
        "--cycle", $Cycle,
        "--start-valid", $StartValid,
        "--end-valid", $EndValid,
        "--output-dir", $OutputDir,
        "--cache-dir", $hrrrCacheDir,
        "--hour-workers", $env:AUTOOUTLOOK_HOUR_WORKERS,
        "--range-workers", $env:AUTOOUTLOOK_RANGE_WORKERS,
        "--grid-stride", $env:AUTOOUTLOOK_GRID_STRIDE,
        "--tile-stride", $env:AUTOOUTLOOK_TILE_STRIDE
    )
    if ($OmitHgt500) {
        $generationArgs += "--omit-hgt500"
    }
    elseif ($IncludeHgt500) {
        $generationArgs += "--include-hgt500"
    }
    if ($Force) {
        $generationArgs += "--force"
    }
    if ($NoCache) {
        $generationArgs += "--no-cache"
    }

    Invoke-Step "Generate custom incremental artifacts" {
        Invoke-NativeCommand $python $generationArgs
    }

    if ($BuildFrontend) {
        Invoke-Step "Build frontend" { Invoke-NativeCommand "npm" @("run", "build") }
    }
    if ($Serve) {
        Invoke-Step "Start local dev servers" {
            Start-LocalDevServers -Python $python -ArtifactDir $OutputDir -LogDir $logDir
        }
        Write-Host "Backend  -> http://127.0.0.1:8765"
        Write-Host "Frontend -> http://localhost:5173"
        Write-Host "Artifacts -> $OutputDir"
    }
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
