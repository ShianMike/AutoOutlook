#!/usr/bin/env bash
# Fetch the ENH+ verification archive in parallel (one OS process per event so
# the GIL-bound pure-Python GRIB decode runs on a separate core), then
# regenerate the static TypeScript data module.
#
# Run from the repo root with the venv created by bootstrap.sh.
#
# Usage:
#   bash scripts/vm/run-enh-plus.sh [CONCURRENCY] [EVENT_DATE ...]
#
#   CONCURRENCY   max events fetched at once (default: nproc). Each event needs
#                 ~1 GB RAM, so keep CONCURRENCY <= free_GB.
#   EVENT_DATE..  optional explicit dates (YYYY-MM-DD). Default: the full
#                 DEFAULT_ENH_PLUS_EVENT_DATES catalog (so the regenerated TS
#                 is internally consistent across all events).
set -euo pipefail

cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source .venv/bin/activate

CONCURRENCY="${1:-$(nproc)}"
shift || true

if [ "$#" -gt 0 ]; then
  DATES=("$@")
else
  # Pull the catalog straight from the source of truth.
  mapfile -t DATES < <(python3 -c "from backend.ml.historical_event_verification import DEFAULT_ENH_PLUS_EVENT_DATES as d; print('\n'.join(x.isoformat() for x in d))")
fi

LOG_DIR="$HOME/enh_plus_logs"
mkdir -p "$LOG_DIR"

echo "[run] events=${#DATES[@]} concurrency=$CONCURRENCY logs=$LOG_DIR"
printf '[run] %s\n' "${DATES[@]}"

# --- phase 1: parallel per-event fetch -------------------------------------
for d in "${DATES[@]}"; do
  # Throttle to CONCURRENCY background jobs.
  while [ "$(jobs -rp | wc -l)" -ge "$CONCURRENCY" ]; do
    wait -n
  done
  (
    echo "[start] $d"
    if python3 scripts/fetch-enh-plus-verification-events.py \
        --event-date "$d" --hour-workers 2 --range-workers 6 \
        > "$LOG_DIR/$d.log" 2>&1; then
      echo "[done]  $d"
    else
      echo "[FAIL]  $d (see $LOG_DIR/$d.log)"
    fi
  ) &
done
wait
echo "[run] all fetch jobs finished"

# --- phase 2: regenerate the static archive --------------------------------
echo "[run] regenerating TypeScript data module (50/50 SPC blend is the default)"
python3 scripts/generate-enh-plus-verification-data.py

echo "[run] output: src/data/historicalEnhPlusVerification.ts"
echo "RUN_OK"
