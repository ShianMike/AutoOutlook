"""Repair ENH+ archive days frozen on the wrong SPC issuance (and thus a wrong
SPC-blended outlook).

Early-morning captures could freeze a day's archive on the prior day's overnight
SPC issuance. Because the archived risk polygons are *SPC-blended*, fixing this
properly requires re-blending, not just swapping the overlay -- and the source
HRRR grids have rotated out of the live cycle dirs. This local-only tool
regenerates the affected days end to end:

  1. Re-fetch the day's 00Z HRRR F12-F36 cycle + the correct SPC Day-1 outlook +
     storm reports (via scripts/fetch-enh-plus-verification-events.py).
  2. Re-blend the merged Day-1 product with the correct SPC.
  3. Rewrite the day in backend/artifacts/enh_plus_archive (correct generated
     outlook, SPC overlay, and headline category).

Like the historical-event fetcher, this is expensive (fetches ~25 HRRR hours per
day) and must NOT run in GitHub Actions. After running locally, push the repaired
archive to GCS so production picks it up on the next deploy:

  gcloud storage rsync --recursive --delete-unmatched-destination-objects \\
    backend/artifacts/enh_plus_archive gs://$GCP_ARTIFACT_BUCKET/enh_plus_archive

With no --date arguments, every archived day whose stored SPC window does not
cover its convective day is repaired automatically.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ml.enh_plus_archive import (  # noqa: E402
    _convective_window_expire,
    _read_json,
    _spc_geojson_expire,
    archive_available_dates,
    remove_archived_date,
    update_archive_for_date,
)
from backend.ml.historical_event_verification import (  # noqa: E402
    event_slug,
    event_window_for_date,
    parse_event_date,
)
from backend.ml.merged_outlook import merge_cycles_for_spc_window  # noqa: E402

ARCHIVE_DIR = PROJECT_ROOT / "backend" / "artifacts" / "enh_plus_archive"
SOURCE_ROOT = PROJECT_ROOT / "backend" / "artifacts" / "historical_enh_plus"
FETCH_SCRIPT = PROJECT_ROOT / "scripts" / "fetch-enh-plus-verification-events.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--date",
        action="append",
        help="Archive date to repair (YYYY-MM-DD). Repeatable. Default: all days with a wrong-window SPC overlay.",
    )
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Reuse an existing source dir instead of re-fetching HRRR (faster; assumes the cycle is already local).",
    )
    parser.add_argument("--hour-workers", type=int, default=2)
    parser.add_argument("--range-workers", type=int, default=6)
    return parser.parse_args()


def _stored_spc_is_wrong(archive_dir: Path, event_date: date) -> bool:
    spc = _read_json(archive_dir / event_date.isoformat() / "spc-day1-category.geojson")
    return _spc_geojson_expire(spc) != _convective_window_expire(event_date)


def _detect_broken_dates(archive_dir: Path) -> list[date]:
    broken: list[date] = []
    for date_str in archive_available_dates(archive_dir):
        try:
            event_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if _stored_spc_is_wrong(archive_dir, event_date):
            broken.append(event_date)
    return broken


def _fetch_source(event_date: date, source_root: Path, hour_workers: int, range_workers: int) -> Path:
    command = [
        sys.executable,
        str(FETCH_SCRIPT),
        "--event-date",
        event_date.isoformat(),
        "--artifact-root",
        str(source_root),
        "--hour-workers",
        str(hour_workers),
        "--range-workers",
        str(range_workers),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    return source_root / event_slug(event_date)


def repair_day(
    event_date: date,
    *,
    archive_dir: Path,
    source_root: Path,
    skip_fetch: bool,
    hour_workers: int,
    range_workers: int,
) -> dict[str, Any]:
    source_dir = source_root / event_slug(event_date)
    if not skip_fetch or not source_dir.is_dir():
        source_dir = _fetch_source(event_date, source_root, hour_workers, range_workers)

    spc_geojson = _read_json(source_dir / "spc_day1_cat.geojson")
    if not isinstance(spc_geojson, dict):
        raise RuntimeError(f"Missing/invalid SPC categorical in source dir: {source_dir}")

    reports_payload = _read_json(source_dir / "spc_storm_reports.json") or {}
    reports = reports_payload.get("reports") if isinstance(reports_payload, dict) else []
    if not isinstance(reports, list):
        reports = []

    window = event_window_for_date(event_date)

    with tempfile.TemporaryDirectory(prefix="autooutlook-archive-repair-") as tmp:
        merged_dir = Path(tmp)
        summary = merge_cycles_for_spc_window(
            [source_dir],
            output_dir=merged_dir,
            target_date=event_date,
            window_valid=window.start_time,
            window_expire=window.end_time,
        )
        if isinstance(summary, dict) and "error" in summary:
            raise RuntimeError(f"Merge failed for {event_date}: {summary['error']}")
        # The merge writes spc_day1_cat.geojson from the source dir's correct,
        # convective-day issuance, so update_archive_for_date's SPC-window gate
        # passes and the re-blended risk polygons replace the frozen ones.
        entry = update_archive_for_date(
            archive_dir, merged_dir, event_date, report_fetch_fn=lambda _d: reports
        )
        if entry is None:
            # Re-blended day is below ENH+ at midday: drop any stale (contaminated)
            # entry so the archive reflects the strict midday-categorical rule.
            removed = remove_archived_date(archive_dir, event_date)
            return {"date": event_date.isoformat(), "removed": removed}
        return entry


def main() -> None:
    args = parse_args()
    archive_dir = Path(args.archive_dir)

    if args.date:
        dates = [parse_event_date(value) for value in args.date]
    else:
        dates = _detect_broken_dates(archive_dir)
        if not dates:
            print("No archived days with a wrong-window SPC overlay were found. Nothing to repair.")
            return
        print("Detected wrong-window SPC archive days: " + ", ".join(d.isoformat() for d in dates))

    failures: list[str] = []
    for index, event_date in enumerate(dates, start=1):
        print(f"\n[{index}/{len(dates)}] repairing {event_date.isoformat()}", flush=True)
        try:
            result = repair_day(
                event_date,
                archive_dir=archive_dir,
                source_root=Path(args.source_root),
                skip_fetch=args.skip_fetch,
                hour_workers=args.hour_workers,
                range_workers=args.range_workers,
            )
            if "removed" in result:
                if result["removed"]:
                    print(f"[removed] {event_date.isoformat()} is below ENH+ at midday; removed from archive.")
                else:
                    print(f"[skipped] {event_date.isoformat()} is below ENH+ and was not present.")
            else:
                print(
                    f"[repaired] {event_date.isoformat()} -> max={result['maxCategory']} "
                    f"auto={result['autoMaxCategory']} spc={result['spcMaxCategory']} "
                    f"reports={result['reportCounts']['total']}"
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{event_date.isoformat()}: {exc}")
            print(f"[failed] {event_date.isoformat()}: {exc}", file=sys.stderr, flush=True)

    if failures:
        raise SystemExit("Archive repair failed:\n" + "\n".join(failures))
    print(
        "\nDone. Push the repaired archive to GCS so production updates on the next deploy:\n"
        "  gcloud storage rsync --recursive --delete-unmatched-destination-objects \\\n"
        "    backend/artifacts/enh_plus_archive gs://$GCP_ARTIFACT_BUCKET/enh_plus_archive"
    )


if __name__ == "__main__":
    main()
