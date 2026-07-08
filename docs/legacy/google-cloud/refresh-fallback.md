# Archived Google Cloud Refresh With Cloudflare Pages

This is legacy fallback documentation. The scheduled no-cost production path is
documented in `../../free-direct-refresh.md` and runs through
`.github/workflows/free-direct-refresh.yml`.

The former Google Cloud publisher workflow is archived at
`docs/legacy/google-cloud/free-hosting-refresh.yml`. It is intentionally outside
`.github/workflows`, so GitHub Actions will not list or run it. Copy it back
only if the Google Cloud fallback is intentionally restored.

## Architecture

- Cloud Scheduler can start the Cloud Run Job `autooutlook-artifact-refresh` when the Google Cloud fallback is intentionally enabled.
- The job writes temporary files under `/tmp` and publishes completed artifacts to `gs://autooutlook-artifacts-project-e75d6e93-197d-4d41-ad6`.
- The bucket uses stable object paths, so storage does not grow by retaining one archive per workflow run.
- `docs/legacy/google-cloud/free-hosting-refresh.yml` is the archived publisher workflow. When restored to `.github/workflows/free-hosting-refresh.yml`, it authenticates with Google Workload Identity Federation and deploys Cloudflare Pages only when production does not already have the latest completed GCS snapshot.
- The GitHub workflow does not call `actions/upload-artifact`, `actions/download-artifact`, or `actions/cache`.
- The Cloud Run service `autooutlook` reads the same bucket and provides a fallback API at `https://autooutlook-672125056378.us-east1.run.app`.

The manual publisher hydrates only live deploy prefixes (`latest_incremental*`,
`latest_incremental_hrrr_*`, and `enh_plus_archive`). It must not rsync the whole
bucket, because manual archive-regeneration prefixes such as `regen/` and
`regen-sources/` can be much larger than the live site bundle.

## Google Cloud Resources

```text
Project: project-e75d6e93-197d-4d41-ad6
Region: us-east1
Artifact bucket: autooutlook-artifacts-project-e75d6e93-197d-4d41-ad6
Artifact Registry repository: autooutlook
Cloud Run service: autooutlook
Cloud Run job: autooutlook-artifact-refresh
Cloud Scheduler job: autooutlook-artifact-refresh-cycle
Runtime service account: autooutlook-runtime@project-e75d6e93-197d-4d41-ad6.iam.gserviceaccount.com
GitHub deployment service account: autooutlook-github-deploy@project-e75d6e93-197d-4d41-ad6.iam.gserviceaccount.com
```

## GitHub Configuration

Cloudflare credentials remain repository secrets:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

Google Cloud integration uses repository variables:

```text
GCP_WORKLOAD_IDENTITY_PROVIDER=projects/672125056378/locations/global/workloadIdentityPools/github-actions/providers/autooutlook
GCP_SERVICE_ACCOUNT=autooutlook-github-deploy@project-e75d6e93-197d-4d41-ad6.iam.gserviceaccount.com
GCP_ARTIFACT_BUCKET=autooutlook-artifacts-project-e75d6e93-197d-4d41-ad6
AUTOOUTLOOK_PRODUCTION_INDEX_URL=https://autooutlook.tech/api/outlook/incremental
```

The workload identity provider is restricted to `ShianMike/AutoOutlook`.
The GitHub deployment service account has `roles/storage.objectAdmin` and
`roles/storage.legacyBucketReader` on the artifact bucket so it can synchronize
objects and read bucket metadata without project-wide storage access.

## Manual Operations

Run the generator and wait for completion:

```powershell
gcloud run jobs replace infra/gcp/autooutlook-artifact-refresh.yaml `
  --region us-east1 `
  --project project-e75d6e93-197d-4d41-ad6

gcloud run jobs execute autooutlook-artifact-refresh `
  --region us-east1 `
  --project project-e75d6e93-197d-4d41-ad6 `
  --wait
```

Force the Cloudflare publisher after the GCS snapshot is complete:

```powershell
copy docs/legacy/google-cloud/free-hosting-refresh.yml .github/workflows/free-hosting-refresh.yml
gh workflow run free-hosting-refresh.yml -f force_deploy=true
```

Inspect recent generator executions:

```powershell
gcloud run jobs executions list `
  --job autooutlook-artifact-refresh `
  --region us-east1 `
  --project project-e75d6e93-197d-4d41-ad6
```

## Verification

The publisher refuses deployment unless the GCS index:

- has `status=complete`;
- contains all 49 forecast hours;
- contains SPC verification generated after the prediction artifacts.

After deployment, it polls `https://autooutlook.tech/api/outlook/incremental` until the expected GCS cycle is visible.
