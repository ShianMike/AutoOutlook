#!/bin/bash
set -e
sudo apt-get update -q > /tmp/apt.log 2>&1
sudo apt-get install -y python3-venv python3-pip unzip build-essential python3-dev >> /tmp/apt.log 2>&1
unzip -q -o /home/ubuntu/backend_aws.zip -d /home/ubuntu/
python3 -m venv /home/ubuntu/.venv
/home/ubuntu/.venv/bin/pip install -q --upgrade pip
/home/ubuntu/.venv/bin/pip install -q pandas pyarrow xgboost scikit-learn requests cfgrib eccodes metpy scipy numpy joblib siphon
sed -i 's/max_workers=12/max_workers=4/' /home/ubuntu/backend/ml/gather_archive.py
mkdir -p /home/ubuntu/backend/ml_data
echo SETUP_OK
