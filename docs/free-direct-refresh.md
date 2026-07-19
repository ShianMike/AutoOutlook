# Free Direct Refresh With Cloudflare Pages

This is the self-contained production refresh path. GitHub Actions supplies the
temporary scheduled runner, while Cloudflare's official Wrangler Action uploads
the finished F00-F48 `dist` bundle to Cloudflare Pages for storage and serving.

## Why this is the free default

- `ShianMike/AutoOutlook` is a public repository, so standard GitHub-hosted
  Actions minutes are free for this workflow.
- The workflow does not use `actions/upload-artifact`, `actions/cache`, package
  registries, persistent GitHub storage, or paid cloud runtime services.
- Cloudflare Pages serves the deployed static site/API bundle.
- Generated HRRR files, Python wheels, Node modules, and deploy output live only
  on the temporary GitHub runner and are discarded after the run.
- Deployment uses `cloudflare/wrangler-action@v3`, the current Cloudflare Pages
  continuous-integration path, with Wrangler pinned to major version 4.

## Workflow

The active workflow is:

```text
.github/workflows/free-direct-refresh.yml
```

It runs at `03:00Z`, `09:00Z`, `15:00Z`, and `21:00Z`, with backup starts at
`:15` after each primary start. The refresh script first detects the latest
complete HRRR cycle and exits without generating when production already serves
that cycle. For a 12Z cycle, "complete" also requires the expected Day 2 date
and its risk, probability, and SPC layers to be live.

Day 2 is anchored to the 12Z F24-F48 window. Later-cycle runners are ephemeral,
so their static export carries the still-future Day 2 bundle forward from the
current Cloudflare Pages deployment. The carry-forward rejects incomplete
bundles and automatically drops the date once it is no longer a future
convective day.

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

## Archived Fallback Material

The old provider-specific workflow has been moved out of `.github/workflows`
and archived at `docs/legacy/google-cloud/free-hosting-refresh.yml`, so it no
longer appears as an active GitHub Actions workflow.

The companion notes and configs live under `docs/legacy/google-cloud/` and
`infra/gcp/`. Keep the archived scheduler disabled while the direct workflow is
healthy.

The retired GitHub Container Registry publisher and cleanup workflow live under
`docs/legacy/github-packages/`. They are not active production workflows.
