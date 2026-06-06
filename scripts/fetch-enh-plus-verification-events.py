"""Fetch local inputs for the hardcoded historical risk verification archive.

This command is intentionally local-only. It generates event-day HRRR 00Z
f17-f28 artifacts first, then downloads the archived SPC Day 1 outlook
inputs and daily storm reports. GitHub Actions must not run this command.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ml.historical_event_verification import (  # noqa: E402
    DEFAULT_ENH_PLUS_EVENT_DATES,
    event_slug,
    event_window_for_date,
    fetch_spc_daily_storm_reports,
    max_spc_category,
    parse_event_date,
)
from backend.ml.merged_outlook import fetch_archived_spc_day1_category  # noqa: E402


ARTIFACT_ROOT = PROJECT_ROOT / "backend" / "artifacts" / "historical_enh_plus"
GENERATOR = PROJECT_ROOT / "scripts" / "generate-custom-hrrr-artifacts.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-date",
        action="append",
        help="Event date to fetch, YYYY-MM-DD. Defaults to the configured archive catalog.",
    )
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--hour-workers", type=int, default=2)
    parser.add_argument("--range-workers", type=int, default=6)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="Keep selected HRRR decode cache. Default avoids the large historical cache.",
    )
    parser.add_argument(
        "--omit-hgt500",
        action="store_true",
        help="Skip the optional 500 mb height overlay.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    event_dates = (
        [parse_event_date(value) for value in args.event_date]
        if args.event_date
        else list(DEFAULT_ENH_PLUS_EVENT_DATES)
    )
    failures: list[str] = []
    for index, event_date in enumerate(event_dates, start=1):
        print(f"\n[{index}/{len(event_dates)}] {event_date.isoformat()}", flush=True)
        try:
            fetch_event(
                event_date,
                args.artifact_root,
                hour_workers=args.hour_workers,
                range_workers=args.range_workers,
                force=args.force,
                keep_cache=args.keep_cache,
                omit_hgt500=args.omit_hgt500,
            )
        except Exception as exc:
            failures.append(f"{event_date.isoformat()}: {exc}")
            print(f"[event failed] {event_date.isoformat()}: {exc}", file=sys.stderr, flush=True)

    if failures:
        raise SystemExit("Historical event acquisition failed:\n" + "\n".join(failures))


def fetch_event(
    event_date: Any,
    artifact_root: Path,
    *,
    hour_workers: int,
    range_workers: int,
    force: bool,
    keep_cache: bool,
    omit_hgt500: bool,
) -> Path:
    window = event_window_for_date(event_date)
    output_dir = artifact_root / event_slug(event_date)
    output_dir.mkdir(parents=True, exist_ok=True)

    if force or not event_artifacts_complete(output_dir, window.forecast_hours):
        command = [
            sys.executable,
            str(GENERATOR),
            "--cycle",
            event_date.strftime("%Y%m%d00"),
            "--output-dir",
            str(output_dir),
            "--forecast-hours",
            *[str(hour) for hour in window.forecast_hours],
            "--grid-stride",
            "2",
            "--tile-stride",
            "1",
            "--hour-workers",
            str(max(1, hour_workers)),
            "--range-workers",
            str(max(1, range_workers)),
            "--no-spc-verify",
        ]
        if force:
            command.append("--force")
        if not keep_cache:
            command.append("--no-cache")
        if omit_hgt500:
            command.append("--omit-hgt500")
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    if not event_artifacts_complete(output_dir, window.forecast_hours):
        raise RuntimeError("HRRR generation did not complete all f17-f28 hours")

    spc_payload = fetch_archived_spc_day1_category(event_date, output_dir=output_dir)
    spc_label, _ = max_spc_category(spc_payload["categoryGeojson"])
    reports = fetch_spc_daily_storm_reports(event_date)
    write_json(output_dir / "spc_storm_reports.json", {"reports": reports})
    print(
        f"[event ready] {event_date.isoformat()} spc={spc_label} "
        f"hours={len(window.forecast_hours)} reports={len(reports)}",
        flush=True,
    )
    return output_dir


def event_artifacts_complete(output_dir: Path, forecast_hours: tuple[int, ...]) -> bool:
    index_path = output_dir / "index.json"
    if not index_path.exists():
        return False
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    ready = {int(value) for value in index.get("readyForecastHours", [])}
    return (
        index.get("status") == "complete"
        and set(forecast_hours).issubset(ready)
        and all(
            (output_dir / "hours" / f"f{hour:02d}" / "probability_tile.json").exists()
            for hour in forecast_hours
        )
    )


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
