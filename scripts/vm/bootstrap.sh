#!/usr/bin/env bash
# Bootstrap a fresh Debian/Ubuntu Compute Engine VM to run the ENH+ archive
# fetch + regeneration. Idempotent: safe to re-run.
#
# Usage:
#   bash bootstrap.sh [REPO_URL] [BRANCH] [CHECKOUT_DIR]
#
# Defaults clone the public GitHub repo on the branch that carries the new
# June 2026 catalog dates.
set -euo pipefail

REPO_URL="${1:-https://github.com/ShianMike/AutoOutlook.git}"
BRANCH="${2:-enh-plus-june-2026}"
DIR="${3:-$HOME/AutoOutlook}"

echo "[bootstrap] repo=$REPO_URL branch=$BRANCH dir=$DIR"

# --- system packages -------------------------------------------------------
sudo apt-get update -qq
sudo apt-get install -y -qq \
  git python3-venv python3-pip build-essential python3-dev

# --- clone / update --------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  echo "[bootstrap] existing checkout, fetching"
  git -C "$DIR" fetch --all --prune
  git -C "$DIR" checkout "$BRANCH"
  git -C "$DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$DIR"
fi

# --- python venv + deps ----------------------------------------------------
cd "$DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt

echo "[bootstrap] python: $(python3 --version)"
echo "[bootstrap] model:  $(python3 -c 'import json;print(json.load(open("backend/models/metadata.json"))["version"])')"
echo "[bootstrap] vcpus:  $(nproc)"
echo "BOOTSTRAP_OK"
