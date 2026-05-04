#!/bin/bash
export PATH="/home/shian/.venv/bin:$PATH"
export VIRTUAL_ENV="/home/shian/.venv"
cd /home/shian
exec /home/shian/.venv/bin/python3 -u -m backend.ml.gather_archive \
  --years $1 --months 3 4 5 6 \
  --cycles 0 6 12 18 \
  --forecast-hours 6 12 18 24 \
  --points-per-hour 15 \
  --negative-points-per-hour 5 \
  --max-samples 30000 \
  --output backend/ml_data/archive_$1.parquet \
  --dedupe-feature-label-rows
