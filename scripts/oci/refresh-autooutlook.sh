#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${AUTOOUTLOOK_REPO_DIR:-/opt/autooutlook/app}"
BRANCH="${AUTOOUTLOOK_BRANCH:-master}"
LOCK_FILE="${AUTOOUTLOOK_LOCK_FILE:-/tmp/autooutlook-refresh.lock}"
STATE_DIR="${AUTOOUTLOOK_STATE_DIR:-${REPO_DIR}/.autooutlook-state}"
FORCE_DEPLOY="${AUTOOUTLOOK_FORCE_DEPLOY:-false}"
export STATE_DIR

if [[ "${1:-}" == "--force" ]]; then
  FORCE_DEPLOY="true"
fi

required_env=(
  CLOUDFLARE_ACCOUNT_ID
  CLOUDFLARE_API_TOKEN
)

for name in "${required_env[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 2
  fi
done

export AUTOOUTLOOK_HOUR_WORKERS="${AUTOOUTLOOK_HOUR_WORKERS:-2}"
export AUTOOUTLOOK_RANGE_WORKERS="${AUTOOUTLOOK_RANGE_WORKERS:-2}"
export AUTOOUTLOOK_GRID_STRIDE="${AUTOOUTLOOK_GRID_STRIDE:-2}"
export AUTOOUTLOOK_TILE_STRIDE="${AUTOOUTLOOK_TILE_STRIDE:-1}"
export AUTOOUTLOOK_PRODUCTION_INDEX_URL="${AUTOOUTLOOK_PRODUCTION_INDEX_URL:-https://autooutlook.tech/api/outlook/incremental}"
export CLOUDFLARE_PAGES_PROJECT="${CLOUDFLARE_PAGES_PROJECT:-autooutlook-pages}"
export CLOUDFLARE_PAGES_BRANCH="${CLOUDFLARE_PAGES_BRANCH:-master}"
export AUTOOUTLOOK_CACHE_MAX_AGE_DAYS="${AUTOOUTLOOK_CACHE_MAX_AGE_DAYS:-2}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another AutoOutlook refresh is already running; exiting."
  exit 0
fi

cd "${REPO_DIR}"
mkdir -p "${STATE_DIR}" backend/artifacts backend/cache/hrrr_selected

if [[ "${AUTOOUTLOOK_GIT_SYNC:-true}" == "true" ]]; then
  git fetch --prune origin "${BRANCH}"
  git reset --hard "origin/${BRANCH}"
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate

current_requirements_hash="$(sha256sum backend/requirements.txt | awk '{print $1}')"
previous_requirements_hash="$(cat "${STATE_DIR}/requirements.sha256" 2>/dev/null || true)"
if [[ "${current_requirements_hash}" != "${previous_requirements_hash}" ]]; then
  python -m pip install --upgrade pip wheel setuptools
  python -m pip install -r backend/requirements.txt
  printf '%s\n' "${current_requirements_hash}" > "${STATE_DIR}/requirements.sha256"
fi

current_package_hash="$(sha256sum package-lock.json | awk '{print $1}')"
previous_package_hash="$(cat "${STATE_DIR}/package-lock.sha256" 2>/dev/null || true)"
if [[ ! -d node_modules || "${current_package_hash}" != "${previous_package_hash}" ]]; then
  npm ci
  printf '%s\n' "${current_package_hash}" > "${STATE_DIR}/package-lock.sha256"
fi

python -m backend.ml.bootstrap_models
python scripts/detect-hrrr-cycle.py --require-forecast-hour 48 > "${STATE_DIR}/cycle.json"

should_generate="$(
  FORCE_DEPLOY="${FORCE_DEPLOY}" python - <<'PY'
import json
import os
import urllib.request

cycle_path = os.path.join(os.environ["STATE_DIR"], "cycle.json") if "STATE_DIR" in os.environ else None
if cycle_path is None:
    cycle_path = ".autooutlook-state/cycle.json"

with open(cycle_path, encoding="utf-8") as handle:
    cycle = json.load(handle)

expected = cycle.get("cycleTimeISO", "")
force = os.environ.get("FORCE_DEPLOY", "false").lower() == "true"
url = os.environ["AUTOOUTLOOK_PRODUCTION_INDEX_URL"]

if force:
    print("true")
    print("manual force requested", file=os.sys.stderr)
    raise SystemExit

try:
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.load(response)
    ready = payload.get("readyForecastHours") or []
    complete = (
        payload.get("cycleTimeISO") == expected
        and payload.get("status") == "complete"
        and len(ready) >= 49
    )
    if complete:
        print("false")
        print("production already has this complete cycle", file=os.sys.stderr)
    else:
        print("true")
        print(
            f"production cycle is {payload.get('cycleTimeISO')!r}, expected {expected!r}",
            file=os.sys.stderr,
        )
except Exception as exc:
    print("true")
    print(f"production index check failed: {exc}", file=os.sys.stderr)
PY
)"

if [[ "${should_generate}" != "true" ]]; then
  exit 0
fi

python -m backend.ml.outlook_pipeline \
  --incremental \
  --all-hours \
  --cycle-policy complete-requested \
  --output-dir backend/artifacts/latest_incremental \
  --cache-dir backend/cache/hrrr_selected \
  --hour-workers "${AUTOOUTLOOK_HOUR_WORKERS}" \
  --range-workers "${AUTOOUTLOOK_RANGE_WORKERS}" \
  --grid-stride "${AUTOOUTLOOK_GRID_STRIDE}" \
  --tile-stride "${AUTOOUTLOOK_TILE_STRIDE}"

npm run build
python scripts/export-static-api.py

wrangler pages deploy dist \
  --project-name="${CLOUDFLARE_PAGES_PROJECT}" \
  --branch="${CLOUDFLARE_PAGES_BRANCH}"

find backend/cache/hrrr_selected -type f -mtime "+${AUTOOUTLOOK_CACHE_MAX_AGE_DAYS}" -delete
