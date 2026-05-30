"""Decode HRRR geopotential height pressure levels from selected S3 byte ranges."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.hrrr_selected import (  # noqa: E402
    DEFAULT_CACHE_TTL_HOURS,
    DEFAULT_RANGE_WORKERS,
    DEFAULT_SELECTED_CACHE_DIR,
    HrrrCycle,
    HrrrHourRef,
    fetch_selected_hrrr_hour_with_metadata,
)

DEFAULT_HGT_LEVELS_MB = (500, 700, 850, 1000)
DEFAULT_CACHE_DIR = DEFAULT_SELECTED_CACHE_DIR.parent / "hrrr_hgt_decode"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", default="2020-05-17T12:00:00Z", help="HRRR cycle time, ISO or YYYYMMDDHH.")
    parser.add_argument("--forecast-hour", type=int, default=0, help="Forecast hour to decode.")
    parser.add_argument("--levels", type=int, nargs="+", default=list(DEFAULT_HGT_LEVELS_MB), help="HGT pressure levels in mb.")
    parser.add_argument("--grid-stride", type=int, default=3, help="Decode every Nth grid point.")
    parser.add_argument("--range-workers", type=int, default=DEFAULT_RANGE_WORKERS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--cache-ttl-hours", type=float, default=DEFAULT_CACHE_TTL_HOURS)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--output-json", type=Path, help="Optional path to write the decoded summary JSON.")
    return parser.parse_args()


def parse_cycle_time(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("z"):
        raw = raw[:-1] + "Z"
    if raw.isdigit() and len(raw) == 10:
        return datetime.strptime(raw, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def field_key(level_mb: int) -> str:
    return f"hgt{int(level_mb)}"


def hgt_term(level_mb: int) -> str:
    return f":HGT:{int(level_mb)} mb:"


def field_stats(values: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return {"finiteCount": 0}
    p01, p50, p99 = np.nanpercentile(finite, [1.0, 50.0, 99.0])
    return {
        "shape": list(arr.shape),
        "finiteCount": int(finite.size),
        "minM": float(np.nanmin(finite)),
        "p01M": float(p01),
        "p50M": float(p50),
        "p99M": float(p99),
        "maxM": float(np.nanmax(finite)),
    }


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    levels = tuple(dict.fromkeys(int(level) for level in args.levels))
    if not levels:
        raise ValueError("At least one pressure level is required")
    invalid = [level for level in levels if level <= 0]
    if invalid:
        raise ValueError(f"Pressure levels must be positive mb values: {invalid}")

    cycle_time = parse_cycle_time(args.cycle)
    ref = HrrrHourRef(
        run_date=cycle_time.strftime("%Y%m%d"),
        run_cycle=cycle_time.hour,
        forecast_hour=args.forecast_hour,
    )
    selected_terms = tuple(hgt_term(level) for level in levels)
    required_fields = tuple(field_key(level) for level in levels)

    fetched = fetch_selected_hrrr_hour_with_metadata(
        ref,
        max_workers=args.range_workers,
        cache_dir=args.cache_dir,
        cache_ttl_hours=args.cache_ttl_hours,
        no_cache=args.no_cache,
        grid_stride=args.grid_stride,
        selected_terms=selected_terms,
        required_terms=selected_terms,
        optional_terms=(),
        required_field_keys=required_fields,
    )

    return {
        "cycle": HrrrCycle(ref.run_date, ref.run_cycle).label,
        "cycleTimeISO": cycle_time.isoformat().replace("+00:00", "Z"),
        "forecastHour": ref.forecast_hour,
        "validTimeISO": ref.valid_time.isoformat().replace("+00:00", "Z"),
        "levelsMb": list(levels),
        "decodedFieldNames": fetched.metadata.get("decodedFieldNames", sorted(fetched.fields)),
        "gridShape": fetched.metadata.get("gridShape"),
        "gridStride": fetched.metadata.get("gridStride"),
        "cacheHit": fetched.metadata.get("cacheHit"),
        "fetchLatencyMs": fetched.metadata.get("fetchLatencyMs"),
        "fields": {key: field_stats(fetched.fields[key]) for key in required_fields},
    }


def main() -> None:
    args = parse_args()
    summary = build_summary(args)
    text = json.dumps(summary, indent=2)
    print(text)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
