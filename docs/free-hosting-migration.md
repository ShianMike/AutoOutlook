# Free Hosting Migration

This migration removes Google Cloud from the public serving path and from the scheduled artifact job.

## Target Architecture

- Cloudflare Pages serves the Vite build and the `autooutlook.tech` custom domain.
- A Cloudflare Pages Function handles `/api/*` and maps those routes to static files generated under `dist/_api`.
- A scheduled runner runs the HRRR/XGBoost artifact job. GitHub Actions can do this, but an Oracle Always Free VM with `systemd` is the preferred no-GitHub-billing path.
- The runner deploys only when a newer complete F00-F48 HRRR cycle is available, keeping Cloudflare Pages deploys below the Free plan's 500/month limit.
- No public request triggers HRRR downloads, model inference, polygon generation, or preview generation.

## Required Accounts

- GitHub repository with Actions enabled, or an Oracle Always Free VM configured with `docs/oracle-always-free-runner.md`.
- Cloudflare Free account with a Pages project.
- Cloudflare API token with Pages deploy permission.

## Cloudflare Setup

Create a Pages project named `autooutlook-pages`, then add these GitHub repository secrets:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

Optional repository variables:

```text
CLOUDFLARE_PAGES_PROJECT=autooutlook-pages
AUTOOUTLOOK_PRODUCTION_INDEX_URL=https://autooutlook.tech/api/outlook/incremental
AUTOOUTLOOK_HOUR_WORKERS=2
AUTOOUTLOOK_RANGE_WORKERS=2
AUTOOUTLOOK_GRID_STRIDE=3
AUTOOUTLOOK_TILE_STRIDE=1
```

During staging, set `AUTOOUTLOOK_PRODUCTION_INDEX_URL` to the Pages preview URL so the hourly job can skip unchanged cycles before `autooutlook.tech` is moved.

## Local Verification

Use existing generated artifacts:

```powershell
npm run build
python scripts/export-static-api.py
```

Expected outputs:

- `dist/index.html`
- `dist/_api/forecast.json`
- `dist/_api/outlook/incremental/index.json`
- `dist/_api/outlook/incremental/hour/f00/probability-tile.json`
- `dist/_api/outlook/incremental/hour/f48/probability-tile.json`

## Cutover

1. Run the `Refresh static AutoOutlook artifacts` workflow manually with `force_deploy=true`.
2. Verify the Pages URL:
   - `/`
   - `/api/health`
   - `/api/forecast`
   - `/api/outlook/incremental`
   - `/api/outlook/incremental/hour/0/probability-tile`
   - `/gif.worker.js`
3. Add `autooutlook.tech` as a Cloudflare Pages custom domain.
4. Move DNS for `autooutlook.tech` to Cloudflare if it is not already there.
5. Recheck the same endpoints on `https://autooutlook.tech`.

## Limits To Watch

- Cloudflare Pages Free: 500 deploys/month, 20,000 files/site, 25 MiB per uploaded file.
- Workers Free, which backs Pages Functions: 100,000 function requests/day and 10 ms CPU/request.
- GitHub scheduled workflows can be delayed or dropped at high load, so this is free and practical but not strict real-time cron.
