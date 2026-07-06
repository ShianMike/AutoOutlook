# Free Direct Refresh With Cloudflare Pages

This is the no-Google-Cloud production refresh path. The scheduled GitHub
Actions job generates the finished F00-F48 artifact bundle and deploys `dist`
directly to Cloudflare Pages.

## Why this is the free default

- `ShianMike/AutoOutlook` is a public repository, so standard GitHub-hosted
  Actions minutes are free for this workflow.
- The workflow does not use `actions/upload-artifact`, `actions/cache`, GitHub
  Packages, GCS, Cloud Run, Cloud Scheduler, or Cloud Build.
- Cloudflare Pages serves the deployed static site/API bundle.
- Generated HRRR files, Python wheels, Node modules, and deploy output live only
  on the temporary GitHub runner and are discarded after the run.

## Workflow

The active workflow is:

```text
.github/workflows/free-direct-refresh.yml
```

It runs at `03:00Z`, `09:00Z`, `15:00Z`, and `21:00Z`, with backup starts at
`:15` after each primary start. The refresh script first detects the latest
complete HRRR cycle and exits without generating when production already serves
that cycle.

Required GitHub repository secrets:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

Optional GitHub repository variables:

```text
CLOUDFLARE_PAGES_PROJECT=autooutlook-pages
CLOUDFLARE_PAGES_BRANCH=master
AUTOOUTLOOK_PRODUCTION_INDEX_URL=https://autooutlook.tech/api/outlook/incremental
```

## Manual Run

```powershell
gh workflow run free-direct-refresh.yml -f force_deploy=true
```

Then watch it:

```powershell
gh run list --workflow free-direct-refresh.yml --limit 1
gh run watch <RUN_ID> --exit-status
```

Verify production after the deploy:

```powershell
curl.exe -fsS https://autooutlook.tech/api/health
curl.exe -fsS https://autooutlook.tech/api/outlook/incremental
```

## Google Cloud Fallback

`.github/workflows/free-hosting-refresh.yml` is manual-only now. Do not
re-enable its schedule while the direct workflow is active, because a stale GCS
snapshot could redeploy older artifacts over the newer Cloudflare Pages output.

After the direct workflow is confirmed healthy, the Google Cloud scheduler can
be disabled and the GCS artifact bucket can be cleaned up separately.
