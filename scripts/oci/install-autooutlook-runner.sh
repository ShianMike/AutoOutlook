#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${AUTOOUTLOOK_APP_DIR:-/opt/autooutlook/app}"
BRANCH="${AUTOOUTLOOK_BRANCH:-master}"
REPO_URL="${AUTOOUTLOOK_REPO_URL:-https://github.com/ShianMike/AutoOutlook.git}"
RUN_USER="${AUTOOUTLOOK_RUN_USER:-autooutlook}"
ENV_FILE="${AUTOOUTLOOK_ENV_FILE:-/etc/autooutlook-refresh.env}"
SERVICE_FILE="/etc/systemd/system/autooutlook-refresh.service"
TIMER_FILE="/etc/systemd/system/autooutlook-refresh.timer"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

case "${APP_DIR}" in
  ""|"/"|"/opt"|"/opt/"|"/home"|"/usr"|"/var")
    echo "Refusing unsafe AUTOOUTLOOK_APP_DIR: ${APP_DIR}" >&2
    exit 1
    ;;
esac

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer currently targets Ubuntu/Debian images on OCI." >&2
  exit 1
fi

apt-get update
apt-get install -y \
  bash \
  build-essential \
  ca-certificates \
  curl \
  git \
  jq \
  libgeos-dev \
  libhdf5-dev \
  libnetcdf-dev \
  pkg-config \
  python3 \
  python3-pip \
  python3-venv \
  util-linux

if ! command -v node >/dev/null 2>&1 || ! node --version | grep -Eq '^v2[4-9]\.'; then
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -y nodejs
fi

npm install --global wrangler

if ! id "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/autooutlook --shell /bin/bash "${RUN_USER}"
fi

install -d -o "${RUN_USER}" -g "${RUN_USER}" "$(dirname "${APP_DIR}")"

if [[ -d "${APP_DIR}/.git" ]]; then
  sudo -H -u "${RUN_USER}" git -C "${APP_DIR}" fetch --prune origin "${BRANCH}"
  sudo -H -u "${RUN_USER}" git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
else
  if [[ -e "${APP_DIR}" ]]; then
    echo "${APP_DIR} exists but is not a Git checkout. Move it aside before installing." >&2
    exit 1
  fi
  sudo -H -u "${RUN_USER}" git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi

sudo -H -u "${RUN_USER}" bash -lc "
  set -euo pipefail
  cd '${APP_DIR}'
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip wheel setuptools
  python -m pip install -r backend/requirements.txt
  npm ci
  python -m backend.ml.bootstrap_models
"

if [[ ! -f "${ENV_FILE}" ]]; then
  umask 077
  cat > "${ENV_FILE}" <<EOF
# Required Cloudflare credentials. Keep this file mode 600.
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=

# Cloudflare Pages target.
CLOUDFLARE_PAGES_PROJECT=autooutlook-pages
CLOUDFLARE_PAGES_BRANCH=master

# Production freshness check. The runner skips generation when this already has
# the latest complete F00-F48 cycle.
AUTOOUTLOOK_PRODUCTION_INDEX_URL=https://autooutlook.tech/api/outlook/incremental

# Runtime sizing for the free OCI Ampere VM.
AUTOOUTLOOK_HOUR_WORKERS=2
AUTOOUTLOOK_RANGE_WORKERS=2
AUTOOUTLOOK_GRID_STRIDE=3
AUTOOUTLOOK_TILE_STRIDE=1
AUTOOUTLOOK_CACHE_MAX_AGE_DAYS=2

# Managed checkout settings.
AUTOOUTLOOK_REPO_DIR=${APP_DIR}
AUTOOUTLOOK_BRANCH=${BRANCH}
AUTOOUTLOOK_GIT_SYNC=true
EOF
  chmod 600 "${ENV_FILE}"
fi

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Refresh AutoOutlook static artifacts
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/scripts/oci/refresh-autooutlook.sh
Nice=5
IOSchedulingClass=best-effort
TimeoutStartSec=5h
EOF

cat > "${TIMER_FILE}" <<'EOF'
[Unit]
Description=Run AutoOutlook refresh hourly

[Timer]
OnCalendar=*:30:00
Persistent=true
RandomizedDelaySec=5m

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload

if grep -Eq '^CLOUDFLARE_ACCOUNT_ID=.+$' "${ENV_FILE}" && grep -Eq '^CLOUDFLARE_API_TOKEN=.+$' "${ENV_FILE}"; then
  systemctl enable --now autooutlook-refresh.timer
else
  echo "Edit ${ENV_FILE} with Cloudflare credentials, then run:"
  echo "  sudo systemctl enable --now autooutlook-refresh.timer"
fi

echo "Installed AutoOutlook runner in ${APP_DIR}."
echo "Manual test command:"
echo "  sudo systemctl start autooutlook-refresh.service"
