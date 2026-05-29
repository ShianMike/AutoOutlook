# Oracle Always Free Runner

This moves the hourly AutoOutlook artifact refresh from GitHub Actions to an Oracle Cloud Infrastructure Always Free VM. Cloudflare Pages still hosts the static site; the VM only generates artifacts and uploads a new Pages deployment when the production index does not already have the latest complete HRRR cycle.

## Free VM Shape

Use an OCI Ampere A1 VM in the tenancy home region:

- Image: Ubuntu 24.04 Minimal aarch64, or the latest Ubuntu aarch64 image.
- Shape: `VM.Standard.A1.Flex`.
- Size: up to 4 OCPUs and 24 GB RAM is inside the Always Free monthly Ampere A1 allocation when a single VM runs all month. If capacity is tight, start with 2 OCPUs and 12 GB RAM.
- Boot volume: 50 GB.
- Network: public subnet with SSH allowed from your IP. No inbound web ports are required for this runner.

## Cloudflare Token

Create a Cloudflare API token that can deploy the `autooutlook-pages` Pages project. The runner needs these values:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

## Install On The VM

SSH into the new VM, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/ShianMike/AutoOutlook/master/scripts/oci/install-autooutlook-runner.sh -o install-autooutlook-runner.sh
sudo bash install-autooutlook-runner.sh
```

The installer creates:

- `/opt/autooutlook/app` for the managed checkout.
- `/etc/autooutlook-refresh.env` for credentials and runtime settings.
- `autooutlook-refresh.service` for one refresh run.
- `autooutlook-refresh.timer` for the hourly schedule.

Edit the environment file:

```bash
sudo nano /etc/autooutlook-refresh.env
```

Set at least:

```text
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
```

Then enable the timer:

```bash
sudo systemctl enable --now autooutlook-refresh.timer
```

Run the first refresh manually:

```bash
sudo systemctl start autooutlook-refresh.service
```

## Verification

Check the job status:

```bash
systemctl status autooutlook-refresh.service --no-pager
systemctl list-timers autooutlook-refresh.timer --no-pager
journalctl -u autooutlook-refresh.service -n 200 --no-pager
```

Check the live site after a successful deploy:

```bash
curl -fsS https://autooutlook.tech/api/health
curl -fsS https://autooutlook.tech/api/outlook/incremental | jq '.cycleTimeISO, .status, (.readyForecastHours | length)'
curl -fsS https://autooutlook.tech/api/outlook/incremental/hour/0/probability-tile >/dev/null
curl -fsS https://autooutlook.tech/gif.worker.js >/dev/null
```

## Operations

Force a refresh even when production already has the detected cycle:

```bash
sudo -u autooutlook bash -lc 'cd /opt/autooutlook/app && set -a && . /etc/autooutlook-refresh.env && set +a && scripts/oci/refresh-autooutlook.sh --force'
```

Stop scheduled refreshes:

```bash
sudo systemctl disable --now autooutlook-refresh.timer
```

Update runner code from GitHub:

```bash
sudo systemctl start autooutlook-refresh.service
```

The refresh script syncs `/opt/autooutlook/app` to `origin/master` before each run when `AUTOOUTLOOK_GIT_SYNC=true`.
