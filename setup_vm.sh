#!/bin/bash
YEAR=$1
OUT="archive_${YEAR}.parquet"
LOG="gather_${YEAR}.log"

set -e

# Fix apt lock if needed
sudo killall apt-get apt 2>/dev/null || true
sudo rm -f /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock
sudo dpkg --configure -a -q 2>/dev/null || true
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip unzip

# Unzip project
cd ~
unzip -qo backend_colab.zip -d .

# Create venv and install deps
python3 -m venv ~/.venv
source ~/.venv/bin/activate
pip install -q --upgrade pip
pip install -q pandas pyarrow xgboost scikit-learn requests cfgrib eccodes metpy scipy numpy joblib siphon

# Patch max_workers to 4
python3 - <<'PY'
from pathlib import Path
p = Path('/home/shian/backend/ml/gather_archive.py')
s = p.read_text()
for old in ['max_workers=12', 'max_workers=8', 'max_workers=2']:
    s = s.replace(f'ThreadPoolExecutor({old})', 'ThreadPoolExecutor(max_workers=4)')
p.write_text(s)
print('patched max_workers=4')
PY

# Create output dir
mkdir -p ~/backend/ml_data

# Launch with nohup
nohup bash -c "source ~/.venv/bin/activate && cd ~ && python3 -u -m backend.ml.gather_archive --years $YEAR --months 3 4 5 6 --cycles 0 6 12 18 --forecast-hours 6 12 18 24 --points-per-hour 15 --negative-points-per-hour 5 --max-samples 30000 --output backend/ml_data/$OUT --dedupe-feature-label-rows" > ~/$LOG 2>&1 &
echo "Launched year $YEAR with PID $!"
