"""Detect the latest complete extended HRRR cycle for scheduled refresh jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.hrrr_selected import latest_available_hrrr_cycle_with_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-forecast-hour", type=int, default=48)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with requests.Session() as session:
        detection = latest_available_hrrr_cycle_with_metadata(
            session=session,
            require_forecast_hour=args.require_forecast_hour,
        )
    payload = dict(detection.metadata)
    payload["cycle"] = detection.selected.label
    payload["cycleTimeISO"] = detection.selected.cycle_time.isoformat().replace("+00:00", "Z")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
