"""Generate local AutoOutlook artifacts for a fixed HRRR cycle.

This is the local/historical companion to the scheduled refresh path. It keeps
the same incremental artifact builder, selected-field S3 byte-range fetcher,
cache, and worker controls, but supplies a fixed cycle instead of detecting the
latest live cycle.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.hrrr_selected import (  # noqa: E402
    DEFAULT_CACHE_TTL_HOURS,
    DEFAULT_RANGE_WORKERS,
    DEFAULT_SELECTED_CACHE_DIR,
    HrrrCycle,
    HrrrHourRef,
    OPTIONAL_HRRR_TERMS,
    REQUIRED_FIELD_KEYS,
    REQUIRED_HRRR_TERMS,
    SELECTED_HRRR_TERMS,
    SelectedHrrrHour,
    fetch_selected_hrrr_hour_with_metadata,
    latest_available_hrrr_cycle_with_metadata,
)
from backend.ml.outlook_pipeline import (  # noqa: E402
    DEFAULT_HOUR_WORKERS,
    DEFAULT_INCREMENTAL_OUTPUT_DIR,
    run_incremental_pipeline,
)

HGT500_TERM = ":HGT:500 mb:"
HGT500_FIELD = "hgt500"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", default="latest", help="Fixed HRRR cycle time, ISO or YYYYMMDDHH, or 'latest' to detect dynamically.")
    parser.add_argument("--start-valid", default="latest", help="First valid hour, ISO or YYYYMMDDHH, or 'latest' to match detected cycle.")
    parser.add_argument("--end-valid", default="latest", help="Last valid hour, ISO or YYYYMMDDHH, or 'latest' to end 24 hours after start.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_INCREMENTAL_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_SELECTED_CACHE_DIR)
    parser.add_argument("--cache-ttl-hours", type=float, default=DEFAULT_CACHE_TTL_HOURS)
    parser.add_argument("--hour-workers", type=int, default=DEFAULT_HOUR_WORKERS)
    parser.add_argument("--range-workers", type=int, default=DEFAULT_RANGE_WORKERS)
    parser.add_argument("--grid-stride", type=int, default=2)
    parser.add_argument("--tile-stride", type=int, default=1)
    parser.add_argument("--forecast-hours", type=int, nargs="+", help="Explicit forecast hours. Overrides start/end valid times.")
    parser.add_argument("--force", action="store_true", help="Regenerate ready hours instead of reusing existing artifacts.")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--include-hgt500", dest="include_hgt500", action="store_true", default=True, help="Fetch HGT 500 mb.")
    parser.add_argument("--omit-hgt500", dest="include_hgt500", action="store_false", help="Skip HGT 500 mb if height contours are not needed.")
    parser.add_argument("--stop-on-hour-failure", action="store_true")
    return parser.parse_args()


def parse_time(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("z"):
        raw = raw[:-1] + "Z"
    if raw.isdigit() and len(raw) == 10:
        return datetime.strptime(raw, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    if raw.isdigit() and len(raw) == 8:
        return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid UTC time {value!r}; use ISO or YYYYMMDDHH") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def cycle_from_time(cycle_time: datetime) -> HrrrCycle:
    return HrrrCycle(cycle_time.strftime("%Y%m%d"), cycle_time.hour)


def hours_from_valid_window(cycle_time: datetime, start_valid: datetime, end_valid: datetime) -> list[int]:
    if end_valid < start_valid:
        raise ValueError("--end-valid must be at or after --start-valid")
    start_hour = int((start_valid - cycle_time).total_seconds() // 3600)
    end_hour = int((end_valid - cycle_time).total_seconds() // 3600)
    hours = list(range(start_hour, end_hour + 1))
    invalid = [hour for hour in hours if hour < 0 or hour > 48]
    if invalid:
        raise ValueError(f"Requested valid window maps outside HRRR f00..f48: {invalid}")
    return hours


def without_hgt500(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(item for item in values if item != HGT500_TERM)


def required_fields_without_hgt500() -> tuple[str, ...]:
    return tuple(item for item in REQUIRED_FIELD_KEYS if item != HGT500_FIELD)


def build_fetcher(include_hgt500: bool, range_workers: int, cache_dir: Path, cache_ttl_hours: float, no_cache: bool, grid_stride: int):
    selected_terms = tuple(SELECTED_HRRR_TERMS) if include_hgt500 else without_hgt500(SELECTED_HRRR_TERMS)
    required_terms = tuple(REQUIRED_HRRR_TERMS) if include_hgt500 else without_hgt500(REQUIRED_HRRR_TERMS)
    optional_terms = tuple(OPTIONAL_HRRR_TERMS) if include_hgt500 else without_hgt500(OPTIONAL_HRRR_TERMS)
    required_fields = tuple(REQUIRED_FIELD_KEYS) if include_hgt500 else required_fields_without_hgt500()

    def fetch_hour(ref: HrrrHourRef, session: requests.Session) -> SelectedHrrrHour:
        return fetch_selected_hrrr_hour_with_metadata(
            ref,
            session=session,
            max_workers=range_workers,
            cache_dir=cache_dir,
            cache_ttl_hours=cache_ttl_hours,
            no_cache=no_cache,
            grid_stride=grid_stride,
            selected_terms=selected_terms,
            required_terms=required_terms,
            optional_terms=optional_terms,
            required_field_keys=required_fields,
        )

    return fetch_hour


def update_custom_metadata(output_dir: Path, include_hgt500: bool) -> None:
    if include_hgt500:
        return
    replacements = {
        "selectedHrrrTerms": list(without_hgt500(SELECTED_HRRR_TERMS)),
        "requiredHrrrTerms": list(without_hgt500(REQUIRED_HRRR_TERMS)),
        "optionalHrrrTerms": list(without_hgt500(OPTIONAL_HRRR_TERMS)),
        "requiredFieldKeys": list(required_fields_without_hgt500()),
        "omittedHrrrTerms": [HGT500_TERM],
        "omittedFieldKeys": [HGT500_FIELD],
        "customFetchNote": "HGT 500 mb was omitted by request for this local historical run, so height contours are not included in these artifacts.",
    }
    for name in ("index.json", "metadata.json"):
        path = output_dir / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(replacements)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.cycle.lower() == "latest":
        print("Detecting latest complete HRRR cycle...")
        with requests.Session() as session:
            detection = latest_available_hrrr_cycle_with_metadata(
                session=session,
                require_forecast_hour=24,
            )
        cycle_time = detection.selected.cycle_time
        print(f"Detected latest cycle: {detection.selected.label} ({cycle_time.isoformat()})")
    else:
        cycle_time = parse_time(args.cycle)

    if args.start_valid.lower() == "latest":
        start_valid = cycle_time
    else:
        start_valid = parse_time(args.start_valid)

    if args.end_valid.lower() == "latest":
        end_valid = cycle_time + timedelta(hours=24)
    else:
        end_valid = parse_time(args.end_valid)

    cycle = cycle_from_time(cycle_time)
    forecast_hours = args.forecast_hours or hours_from_valid_window(cycle_time, start_valid, end_valid)

    def fixed_cycle(_session: requests.Session, _now: datetime | None):
        return cycle

    metadata = run_incremental_pipeline(
        output_dir=args.output_dir,
        forecast_hours=forecast_hours,
        process_forecast_hours=forecast_hours,
        hour_workers=args.hour_workers,
        range_workers=args.range_workers,
        grid_stride=args.grid_stride,
        tile_stride=args.tile_stride,
        cache_dir=args.cache_dir,
        cache_ttl_hours=args.cache_ttl_hours,
        no_cache=args.no_cache,
        force=args.force,
        cycle_policy="complete-requested",
        require_complete_hour=max(forecast_hours),
        detect_cycle_fn=fixed_cycle,
        fetch_hour_fn=build_fetcher(
            args.include_hgt500,
            args.range_workers,
            args.cache_dir,
            args.cache_ttl_hours,
            args.no_cache,
            args.grid_stride,
        ),
        continue_on_hour_failure=not args.stop_on_hour_failure,
        publish_gcs_bucket=None,
        verify_spc=True,
    )
    update_custom_metadata(args.output_dir, args.include_hgt500)

    print(json.dumps({
        "outputDir": str(args.output_dir),
        "cycle": metadata.get("cycle"),
        "cycleTimeISO": metadata.get("cycleTimeISO"),
        "status": metadata.get("status"),
        "readyForecastHours": metadata.get("readyForecastHours"),
        "failedForecastHours": metadata.get("failedForecastHours"),
        "pendingForecastHours": metadata.get("pendingForecastHours"),
        "latencyMs": metadata.get("latencyMs"),
        "includeHgt500": bool(args.include_hgt500),
    }, indent=2))


if __name__ == "__main__":
    main()
