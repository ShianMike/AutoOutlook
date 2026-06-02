[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipEnvFile
)

$ErrorActionPreference = "Stop"

function Import-EnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Environment file not found: $Path"
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

function Require-Env {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Missing required environment variable: $Name"
    }
    return $value
}

function Test-WindowsPlatform {
    return $PSVersionTable.PSEdition -eq "Desktop" -or [bool](Get-Variable -Name IsWindows -ErrorAction SilentlyContinue).Value
}

function Get-DefaultDataRoot {
    if (-not [string]::IsNullOrWhiteSpace($env:AUTOOUTLOOK_DATA_DIR)) {
        return $env:AUTOOUTLOOK_DATA_DIR
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramData)) {
        return (Join-Path $env:ProgramData "AutoOutlook")
    }
    return (Join-Path $script:RepoDir ".autooutlook")
}

function Join-RepoPath {
    param([string[]]$Parts)
    return [System.IO.Path]::Combine([string[]](@($script:RepoDir) + $Parts))
}

function Get-VenvPythonExecutable {
    if (Test-WindowsPlatform) {
        return (Join-Path $script:VenvDir "Scripts\python.exe")
    }
    return (Join-Path $script:VenvDir "bin/python")
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

    $venvPython = Get-VenvPythonExecutable
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

    throw "No Python executable found. Install Python 3.11+ or set AUTOOUTLOOK_PYTHON in the env file."
}

function Ensure-PythonEnvironment {
    $basePython = Get-PythonExecutable
    $venvPython = Get-VenvPythonExecutable
    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-Item -ItemType Directory -Force -Path $script:VenvDir | Out-Null
        Invoke-NativeCommand $basePython @("-m", "venv", $script:VenvDir)
    }

    $requirementsPath = Join-RepoPath @("backend", "requirements.txt")
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
    $packageLockPath = Join-RepoPath @("package-lock.json")
    $stateHashPath = Join-Path $script:StateDir "package-lock.sha256"
    $nodeModulesPath = Join-Path $script:RepoDir "node_modules"
    $currentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $packageLockPath).Hash
    $previousHash = if (Test-Path -LiteralPath $stateHashPath) { Get-Content -LiteralPath $stateHashPath -Raw } else { "" }

    if ((-not (Test-Path -LiteralPath $nodeModulesPath)) -or $currentHash -ne $previousHash.Trim()) {
        Invoke-NativeCommand "npm" @("ci")
        $currentHash | Set-Content -LiteralPath $stateHashPath -Encoding ASCII
    }
}

function Test-ProductionHasCycle {
    param(
        [string]$CycleTimeIso,
        [switch]$IgnoreForce
    )

    if ($Force -and -not $IgnoreForce) {
        Write-Host "Manual force requested."
        return $false
    }

    try {
        $payload = Invoke-RestMethod -Uri $env:AUTOOUTLOOK_PRODUCTION_INDEX_URL -TimeoutSec 20
        $ready = @($payload.readyForecastHours)
        if ($payload.cycleTimeISO -eq $CycleTimeIso -and $payload.status -eq "complete" -and $ready.Count -ge 49) {
            Write-Host "Production already has this complete cycle."
            return $true
        }
        Write-Host "Production cycle is '$($payload.cycleTimeISO)', expected '$CycleTimeIso'."
        return $false
    }
    catch {
        Write-Host "Production index check failed: $($_.Exception.Message)"
        return $false
    }
}

function Test-EnvFlag {
    param(
        [string]$Name,
        [bool]$Default = $false
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
}

function Remove-GeneratedDirectory {
    param(
        [string]$Path,
        [string]$ExpectedPath,
        [string]$Label
    )

    $target = [System.IO.Path]::GetFullPath($Path)
    $expected = [System.IO.Path]::GetFullPath($ExpectedPath)
    if (-not $target.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean unexpected $Label path: $target"
    }

    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host "[cleanup] $Label not present: $target"
        return
    }

    $measure = Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum
    $megabytes = [math]::Round(($measure.Sum / 1MB), 1)
    Remove-Item -LiteralPath $target -Recurse -Force
    Write-Host "[cleanup] removed $Label files=$($measure.Count) sizeMB=$megabytes path=$target"
}

function Remove-LocalDeployOutputs {
    $artifactsRoot = Join-RepoPath @("backend", "artifacts")
    Remove-GeneratedDirectory `
        -Path (Join-Path $artifactsRoot "latest_incremental") `
        -ExpectedPath (Join-RepoPath @("backend", "artifacts", "latest_incremental")) `
        -Label "incremental artifacts"
    Remove-GeneratedDirectory `
        -Path (Join-Path $artifactsRoot "latest_incremental_complete") `
        -ExpectedPath (Join-RepoPath @("backend", "artifacts", "latest_incremental_complete")) `
        -Label "complete incremental artifacts"
    Remove-GeneratedDirectory `
        -Path (Join-RepoPath @("dist")) `
        -ExpectedPath (Join-RepoPath @("dist")) `
        -Label "Cloudflare deploy bundle"
}

function Remove-LocalHrrrCache {
    $expectedCacheDir = if ($env:AUTOOUTLOOK_HRRR_CACHE_DIR) {
        $env:AUTOOUTLOOK_HRRR_CACHE_DIR
    }
    else {
        [System.IO.Path]::Combine((Get-DefaultDataRoot), "cache", "hrrr_selected")
    }
    Remove-GeneratedDirectory `
        -Path $hrrrCacheDir `
        -ExpectedPath $expectedCacheDir `
        -Label "selected HRRR cache"
    New-Item -ItemType Directory -Force -Path $hrrrCacheDir | Out-Null
}

$envFile = [Environment]::GetEnvironmentVariable("AUTOOUTLOOK_ENV_FILE", "Process")
if (-not $SkipEnvFile) {
    if ([string]::IsNullOrWhiteSpace($envFile)) {
        if ([string]::IsNullOrWhiteSpace($env:ProgramData)) {
            throw "AUTOOUTLOOK_ENV_FILE is required when ProgramData is unavailable. Use -SkipEnvFile in CI."
        }
        $envFile = Join-Path $env:ProgramData "AutoOutlook\refresh.env"
    }
    Import-EnvFile -Path $envFile
}
else {
    Write-Host "Skipping env file import; using process environment."
}

Require-Env "CLOUDFLARE_ACCOUNT_ID" | Out-Null
Require-Env "CLOUDFLARE_API_TOKEN" | Out-Null

$script:RepoDir = if ($env:AUTOOUTLOOK_REPO_DIR) { $env:AUTOOUTLOOK_REPO_DIR } else { (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$defaultDataRoot = Get-DefaultDataRoot
$script:VenvDir = if ($env:AUTOOUTLOOK_VENV_DIR) { $env:AUTOOUTLOOK_VENV_DIR } else { Join-Path $defaultDataRoot ".venv" }
$script:StateDir = if ($env:AUTOOUTLOOK_STATE_DIR) { $env:AUTOOUTLOOK_STATE_DIR } else { Join-Path $defaultDataRoot "state" }
$logDir = if ($env:AUTOOUTLOOK_LOG_DIR) { $env:AUTOOUTLOOK_LOG_DIR } else { Join-Path $defaultDataRoot "logs" }
$hrrrCacheDir = if ($env:AUTOOUTLOOK_HRRR_CACHE_DIR) { $env:AUTOOUTLOOK_HRRR_CACHE_DIR } else { [System.IO.Path]::Combine($defaultDataRoot, "cache", "hrrr_selected") }

$env:AUTOOUTLOOK_HOUR_WORKERS = if ($env:AUTOOUTLOOK_HOUR_WORKERS) { $env:AUTOOUTLOOK_HOUR_WORKERS } else { "3" }
$env:AUTOOUTLOOK_RANGE_WORKERS = if ($env:AUTOOUTLOOK_RANGE_WORKERS) { $env:AUTOOUTLOOK_RANGE_WORKERS } else { "4" }
$env:AUTOOUTLOOK_RANGE_COALESCE_GAP_BYTES = if ($env:AUTOOUTLOOK_RANGE_COALESCE_GAP_BYTES) { $env:AUTOOUTLOOK_RANGE_COALESCE_GAP_BYTES } else { "2097152" }
$env:AUTOOUTLOOK_GRID_STRIDE = if ($env:AUTOOUTLOOK_GRID_STRIDE) { $env:AUTOOUTLOOK_GRID_STRIDE } else { "2" }
$env:AUTOOUTLOOK_TILE_STRIDE = if ($env:AUTOOUTLOOK_TILE_STRIDE) { $env:AUTOOUTLOOK_TILE_STRIDE } else { "1" }
$env:AUTOOUTLOOK_PRODUCTION_INDEX_URL = if ($env:AUTOOUTLOOK_PRODUCTION_INDEX_URL) { $env:AUTOOUTLOOK_PRODUCTION_INDEX_URL } else { "https://autooutlook.tech/api/outlook/incremental" }
$env:CLOUDFLARE_PAGES_PROJECT = if ($env:CLOUDFLARE_PAGES_PROJECT) { $env:CLOUDFLARE_PAGES_PROJECT } else { "autooutlook-pages" }
$env:CLOUDFLARE_PAGES_BRANCH = if ($env:CLOUDFLARE_PAGES_BRANCH) { $env:CLOUDFLARE_PAGES_BRANCH } else { "master" }

New-Item -ItemType Directory -Force -Path $script:StateDir, $logDir, $hrrrCacheDir | Out-Null

$isWindowsPlatform = Test-WindowsPlatform
$mutexName = if ($isWindowsPlatform) { "Global\AutoOutlookStaticRefresh" } else { "AutoOutlookStaticRefresh" }
$mutex = [Threading.Mutex]::new($false, $mutexName)
if (-not $mutex.WaitOne(0)) {
    Write-Host "Another AutoOutlook refresh is already running; exiting."
    exit 0
}

$transcriptPath = Join-Path $logDir ("refresh-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$transcriptStarted = $false

try {
    Start-Transcript -Path $transcriptPath -Append | Out-Null
    $transcriptStarted = $true

    Set-Location -LiteralPath $script:RepoDir

    $python = Invoke-Step "Prepare Python dependencies" { Ensure-PythonEnvironment }
    Invoke-Step "Prepare frontend dependencies" { Ensure-NodeDependencies }
    Invoke-Step "Bootstrap runtime hazard models" {
        Invoke-NativeCommand $python @("-m", "backend.ml.bootstrap_models")
    }

    $cyclePath = Join-Path $script:StateDir "cycle.json"
    Invoke-Step "Detect latest complete HRRR cycle" {
        $cycleOutput = & $python "scripts/detect-hrrr-cycle.py" "--require-forecast-hour" "48"
        if ($LASTEXITCODE -ne 0) {
            throw "$python scripts/detect-hrrr-cycle.py exited with code $LASTEXITCODE"
        }
        $cycleOutput | Set-Content -LiteralPath $cyclePath -Encoding UTF8
    }

    $cycle = Get-Content -LiteralPath $cyclePath -Raw | ConvertFrom-Json
    if (Test-ProductionHasCycle -CycleTimeIso $cycle.cycleTimeISO) {
        exit 0
    }

    Invoke-Step "Generate incremental artifacts" {
        Invoke-NativeCommand $python @(
            "-m", "backend.ml.outlook_pipeline",
            "--incremental",
            "--all-hours",
            "--cycle-policy", "complete-requested",
            "--output-dir", "backend/artifacts/latest_incremental",
            "--cache-dir", $hrrrCacheDir,
            "--hour-workers", $env:AUTOOUTLOOK_HOUR_WORKERS,
            "--range-workers", $env:AUTOOUTLOOK_RANGE_WORKERS,
            "--grid-stride", $env:AUTOOUTLOOK_GRID_STRIDE,
            "--tile-stride", $env:AUTOOUTLOOK_TILE_STRIDE
        )
    }

    Invoke-Step "Build frontend" { Invoke-NativeCommand "npm" @("run", "build") }
    Invoke-Step "Export static API" { Invoke-NativeCommand $python @("scripts/export-static-api.py") }
    Invoke-Step "Deploy to Cloudflare Pages" {
        Invoke-NativeCommand "npx" @(
            "--yes",
            "wrangler@latest",
            "pages",
            "deploy",
            "dist",
            "--project-name=$env:CLOUDFLARE_PAGES_PROJECT",
            "--branch=$env:CLOUDFLARE_PAGES_BRANCH",
            "--commit-dirty=true"
        )
    }

    $productionConfirmed = Test-ProductionHasCycle -CycleTimeIso $cycle.cycleTimeISO -IgnoreForce
    if (-not $productionConfirmed) {
        Write-Host "[cleanup] skipped because production did not confirm the deployed cycle yet."
    }
    elseif (Test-EnvFlag "AUTOOUTLOOK_CLEANUP_AFTER_DEPLOY" $true) {
        Invoke-Step "Clean local deploy outputs" { Remove-LocalDeployOutputs }
        if (Test-EnvFlag "AUTOOUTLOOK_CLEANUP_CACHE_AFTER_DEPLOY" $true) {
            Invoke-Step "Clean local HRRR cache" { Remove-LocalHrrrCache }
        }
    }

    $maxAgeDays = if ($env:AUTOOUTLOOK_CACHE_MAX_AGE_DAYS) { [int]$env:AUTOOUTLOOK_CACHE_MAX_AGE_DAYS } else { 2 }
    Get-ChildItem -LiteralPath $hrrrCacheDir -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$maxAgeDays) } |
        Remove-Item -Force
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
