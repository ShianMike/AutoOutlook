[CmdletBinding()]
param(
    [string]$RepoDir = "",
    [string]$TaskName = "AutoOutlook Static Refresh",
    [string]$EnvFile = (Join-Path $env:ProgramData "AutoOutlook\refresh.env"),
    [string[]]$UtcRunTimes = @("03:00", "09:00", "15:00", "21:00"),
    [switch]$RegisterWithoutCredentials
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoDir)) {
    $RepoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Test-ConfiguredValue {
    param([string]$Value)
    return -not [string]::IsNullOrWhiteSpace($Value) -and -not $Value.Trim().EndsWith("=")
}

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
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
        $values[$key] = $value
    }
    return $values
}

function Convert-UtcRunTimeToLocalTriggerTime {
    param([string]$UtcRunTime)

    if ($UtcRunTime -notmatch '^([01]?\d|2[0-3]):([0-5]\d)$') {
        throw "Invalid UTC run time '$UtcRunTime'. Use HH:mm, for example 01:30."
    }

    $hour = [int]$matches[1]
    $minute = [int]$matches[2]
    $utcDate = [DateTime]::SpecifyKind([DateTime]::UtcNow.Date.AddHours($hour).AddMinutes($minute), [DateTimeKind]::Utc)
    $localDate = [TimeZoneInfo]::ConvertTimeFromUtc($utcDate, [TimeZoneInfo]::Local)
    return [DateTime]::Today.Add($localDate.TimeOfDay)
}

$programDataDir = Join-Path $env:ProgramData "AutoOutlook"
$logDir = Join-Path $programDataDir "logs"
$stateDir = Join-Path $programDataDir "state"
$cacheDir = Join-Path $programDataDir "cache\hrrr_selected"
$venvDir = Join-Path $programDataDir ".venv"

New-Item -ItemType Directory -Force -Path $programDataDir, $logDir, $stateDir, $cacheDir | Out-Null

if (-not (Test-Path -LiteralPath $EnvFile)) {
    @"
# AutoOutlook scheduled refresh settings.
# Keep this file private; it contains the Cloudflare deploy token.

CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=

CLOUDFLARE_PAGES_PROJECT=autooutlook-pages
CLOUDFLARE_PAGES_BRANCH=master
AUTOOUTLOOK_PRODUCTION_INDEX_URL=https://autooutlook.tech/api/outlook/incremental

AUTOOUTLOOK_REPO_DIR=$RepoDir
AUTOOUTLOOK_VENV_DIR=$venvDir
AUTOOUTLOOK_STATE_DIR=$stateDir
AUTOOUTLOOK_HRRR_CACHE_DIR=$cacheDir
AUTOOUTLOOK_LOG_DIR=$logDir

AUTOOUTLOOK_HOUR_WORKERS=3
AUTOOUTLOOK_RANGE_WORKERS=4
AUTOOUTLOOK_RANGE_COALESCE_GAP_BYTES=2097152
AUTOOUTLOOK_GRID_STRIDE=2
AUTOOUTLOOK_TILE_STRIDE=1
AUTOOUTLOOK_CLEANUP_AFTER_DEPLOY=true
AUTOOUTLOOK_CLEANUP_CACHE_AFTER_DEPLOY=true
AUTOOUTLOOK_CACHE_MAX_AGE_DAYS=2

# Optional: set to a Python 3.11+ executable if PATH's python is not suitable.
AUTOOUTLOOK_PYTHON=
"@ | Set-Content -LiteralPath $EnvFile -Encoding UTF8
}

$envValues = Read-EnvFile -Path $EnvFile
$hasCredentials = (Test-ConfiguredValue $envValues["CLOUDFLARE_ACCOUNT_ID"]) -and (Test-ConfiguredValue $envValues["CLOUDFLARE_API_TOKEN"])

if (-not $hasCredentials -and -not $RegisterWithoutCredentials) {
    Write-Host "Created or verified $EnvFile"
    Write-Host "Add CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN, then rerun this installer to register the task."
    exit 0
}

$refreshScript = Join-Path $RepoDir "scripts\windows\refresh-autooutlook.ps1"
if (-not (Test-Path -LiteralPath $refreshScript)) {
    throw "Refresh script not found: $refreshScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$refreshScript`""

$localRunTimes = $UtcRunTimes |
    ForEach-Object { Convert-UtcRunTimeToLocalTriggerTime -UtcRunTime $_ } |
    Sort-Object TimeOfDay -Unique

$triggers = $localRunTimes | ForEach-Object {
    New-ScheduledTaskTrigger -Daily -At $_
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Runs AutoOutlook refresh at 03:00Z, 09:00Z, 15:00Z, and 21:00Z." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName'."
Write-Host "UTC run times: $($UtcRunTimes -join ', ')"
Write-Host "Local run times: $(($localRunTimes | ForEach-Object { $_.ToString('HH:mm') }) -join ', ')"
Write-Host "Manual test:"
Write-Host "  powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$refreshScript`""
