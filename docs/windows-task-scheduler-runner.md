# Windows Task Scheduler Runner

This is now a fallback runner. The scheduled GitHub Action uses the same cycle-aligned `01:30Z`, `07:30Z`, `13:30Z`, and `19:30Z` primary schedule, with backup triggers 15 minutes later in case GitHub drops a scheduled event.

This is the temporary free runner for AutoOutlook on the local Windows machine. It replaces the GitHub Actions scheduler with a Windows scheduled task that:

1. Detects the latest complete HRRR cycle.
2. Skips work when `https://autooutlook.tech/api/outlook/incremental` already has that complete cycle.
3. Generates F00-F48 artifacts.
4. Builds and exports the Cloudflare Pages static API.
5. Deploys `dist` to Cloudflare Pages with Wrangler.

## Install

Run from the real Git checkout:

```powershell
cd C:\Users\shian\OneDrive\Desktop\AutoOutlook-git
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install-autooutlook-task.ps1
```

The installer creates:

```text
C:\ProgramData\AutoOutlook\refresh.env
C:\ProgramData\AutoOutlook\logs\
C:\ProgramData\AutoOutlook\state\
C:\ProgramData\AutoOutlook\cache\hrrr_selected\
```

The default local fetch profile is tuned for the Windows runner:

```text
AUTOOUTLOOK_HOUR_WORKERS=3
AUTOOUTLOOK_RANGE_WORKERS=4
AUTOOUTLOOK_RANGE_COALESCE_GAP_BYTES=2097152
```

That runs three forecast hours at a time, uses four parallel S3 byte-range downloads within each hour, and merges selected HRRR records separated by no more than 2 MiB so the runner makes fewer HTTP requests per hour.

After a successful Cloudflare Pages deploy, the runner removes the local generated outputs by default:

```text
backend/artifacts/latest_incremental/
backend/artifacts/latest_incremental_complete/
dist/
C:\ProgramData\AutoOutlook\cache\hrrr_selected\
```

This keeps the Windows checkout from accumulating the large per-cycle artifact files and clears the HRRR selected-field cache once the deployed cycle is confirmed on production. Set `AUTOOUTLOOK_CLEANUP_AFTER_DEPLOY=false` in `C:\ProgramData\AutoOutlook\refresh.env` if you need to keep a completed local deploy bundle for debugging, or `AUTOOUTLOOK_CLEANUP_CACHE_AFTER_DEPLOY=false` if you want to keep the HRRR cache for a same-cycle retry.

Edit the env file and add the Cloudflare values:

```powershell
notepad C:\ProgramData\AutoOutlook\refresh.env
```

Required values:

```text
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
```

Then rerun the installer to register the cycle-aligned task:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install-autooutlook-task.ps1
```

By default, the Windows task runs at `01:30Z`, `07:30Z`, `13:30Z`, and `19:30Z`, which is one hour and thirty minutes after the `00Z`, `06Z`, `12Z`, and `18Z` HRRR cycles. On a UTC+8 Windows timezone, that appears in Task Scheduler as `09:30`, `15:30`, `21:30`, and `03:30` local time. The GitHub Actions workflow also has backup triggers at `01:45Z`, `07:45Z`, `13:45Z`, and `19:45Z`; the workflow freshness gate skips the backup when the primary run already deployed the cycle.

To override the UTC run times:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install-autooutlook-task.ps1 -UtcRunTimes 01:30,07:30,13:30,19:30
```

## Manual Test

Run one refresh manually:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\refresh-autooutlook.ps1
```

Force generation even when production already has the detected cycle:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\refresh-autooutlook.ps1 -Force
```

## Task Operations

Inspect the task:

```powershell
Get-ScheduledTask -TaskName "AutoOutlook Static Refresh"
Get-ScheduledTaskInfo -TaskName "AutoOutlook Static Refresh"
```

Run the task now:

```powershell
Start-ScheduledTask -TaskName "AutoOutlook Static Refresh"
```

Stop scheduling:

```powershell
Disable-ScheduledTask -TaskName "AutoOutlook Static Refresh"
```

Remove the task completely:

```powershell
Unregister-ScheduledTask -TaskName "AutoOutlook Static Refresh" -Confirm:$false
```

Read recent logs:

```powershell
Get-ChildItem C:\ProgramData\AutoOutlook\logs\refresh-*.log |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  Get-Content -Tail 200
```

## Notes

- The scheduled task uses the current Windows user and runs only when the machine is on.
- The script creates its own Python virtual environment under `C:\ProgramData\AutoOutlook\.venv`; it does not use the repo `.venv`.
- If the default `python` command is not suitable, set `AUTOOUTLOOK_PYTHON` in `refresh.env` to a Python 3.11+ executable.
