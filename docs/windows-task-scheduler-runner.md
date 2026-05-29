# Windows Task Scheduler Runner

This is the temporary free runner for AutoOutlook on the local Windows machine. It replaces the hourly GitHub Actions scheduler with a Windows scheduled task that:

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

Edit the env file and add the Cloudflare values:

```powershell
notepad C:\ProgramData\AutoOutlook\refresh.env
```

Required values:

```text
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
```

Then rerun the installer to register the hourly task:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install-autooutlook-task.ps1
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
