# ENH+ archive regeneration on a Compute Engine VM

The historical ENH+ verification archive is rebuilt by fetching event-day HRRR
00Z f12–f36 and running the hazard models per forecast hour. The GRIB decode is
a GIL-bound pure-Python step (`backend/grib2.py`), so it does **not** parallelize
across threads — the only way to use many cores is to run **one OS process per
event**. A multi-vCPU VM does this in a couple of hours instead of ~a day.

`backend/artifacts/` is gitignored, so a fresh VM has no cached events: it fetches
everything in the catalog. The committed model files travel with the clone, so the
regenerated archive is consistent with the active model. The 50/50 HRRR↔SPC blend
is the default (`DEFAULT_SPC_SUPPORT_WEIGHT = 0.50`), so no flags are needed.

## 1. Create the VM (Spot, high-clock, enough RAM for 1 process/core)

```bash
gcloud compute instances create enh-plus-builder \
  --zone=us-central1-a \
  --machine-type=c2d-standard-32 \
  --provisioning-model=SPOT \
  --instance-termination-action=DELETE \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=60GB --boot-disk-type=pd-ssd
```

`c2d-standard-32` = 32 vCPU / 128 GB. Each event uses ~1 GB, so all catalog
events run concurrently. Use `c2d-standard-16` to halve cost (events run in two
waves). Spot keeps it cheap for a one-off batch.

## 2. Bootstrap

```bash
gcloud compute ssh enh-plus-builder --zone=us-central1-a
# on the VM:
curl -fsSL https://raw.githubusercontent.com/ShianMike/AutoOutlook/enh-plus-june-2026/scripts/vm/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh
```

(If the repo is private, instead `git clone` with a token, or `gcloud compute scp`
a slim zip of `backend/` + `scripts/` and skip the clone.)

## 3. Run (parallel fetch + regenerate)

```bash
cd ~/AutoOutlook
nohup bash scripts/vm/run-enh-plus.sh > ~/enh_plus_run.log 2>&1 &
# monitor:
tail -f ~/enh_plus_run.log
ls ~/enh_plus_logs            # one log per event
```

Fetch only the new June dates (faster, but produces a June-only TS unless you
also have the other events present):

```bash
bash scripts/vm/run-enh-plus.sh 7 2026-06-06 2026-06-07 2026-06-08 2026-06-09 2026-06-10 2026-06-11 2026-06-12
```

## 4. Retrieve the result

Only `src/data/historicalEnhPlusVerification.ts` matters (artifacts stay on the VM).

```bash
# from your workstation:
gcloud compute scp enh-plus-builder:~/AutoOutlook/src/data/historicalEnhPlusVerification.ts \
  ./src/data/historicalEnhPlusVerification.ts --zone=us-central1-a
```

Then locally: `npm run build` and open `#docs-enh-verification` to verify.

## 5. Tear down

```bash
gcloud compute instances delete enh-plus-builder --zone=us-central1-a
```
