"""Add SPC report intensity labels to an existing archive training parquet."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

from backend.ml.gather_archive import load_spc_reports
from backend.ml.merge_archive_training_data import ARCHIVE_TRAINING_DIR, DEFAULT_OUTPUT as DEFAULT_INPUT
from backend.ml.reports import INTENSITY_LABEL_KEYS, ensure_utc, intensity_labels_for_sample

DEFAULT_OUTPUT = ARCHIVE_TRAINING_DIR / "autooutlook_hrrr_2020_202602_00z_f00_f48_intensity.parquet"
DEFAULT_SUMMARY = ARCHIVE_TRAINING_DIR / "autooutlook_hrrr_2020_202602_00z_f00_f48_intensity_summary.json"


def _require_deps() -> Any:
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Intensity label enrichment requires pandas and pyarrow. "
            "Run `pip install -r backend/requirements.txt` first. "
            f"Original error: {exc}"
        ) from exc
    return pd


def _parse_valid_time(value: Any) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return ensure_utc(parsed)


def _hour_key(value: datetime) -> datetime:
    value = ensure_utc(value)
    return value.replace(minute=0, second=0, microsecond=0)


def _reports_by_hour(reports: Iterable[Mapping[str, Any]]) -> dict[datetime, list[Mapping[str, Any]]]:
    grouped: dict[datetime, list[Mapping[str, Any]]] = defaultdict(list)
    for report in reports:
        report_time = report.get("time")
        if isinstance(report_time, datetime):
            grouped[_hour_key(report_time)].append(report)
    return dict(grouped)


def _run_dates_from_frame(frame: Any) -> list[str]:
    return sorted(str(value) for value in frame["runDate"].dropna().astype(str).unique())


def _years_months_from_run_dates(run_dates: Iterable[str]) -> tuple[list[int], list[int]]:
    years: set[int] = set()
    months: set[int] = set()
    for run_date in run_dates:
        token = str(run_date)
        if len(token) < 6:
            continue
        years.add(int(token[:4]))
        months.add(int(token[4:6]))
    return sorted(years), sorted(months)


def enrich_frame_with_intensity_labels(
    frame: Any,
    reports: Iterable[Mapping[str, Any]],
    radius_km: float = 40.0,
    window_hours: float = 1.0,
) -> Any:
    missing = [column for column in ("validTimeISO", "sampleLat", "sampleLon") if column not in frame.columns]
    if missing:
        raise ValueError(f"Training frame missing columns required for intensity labels: {missing}")

    grouped_reports = _reports_by_hour(reports)
    output = frame.copy()
    output = output.reset_index(drop=True)
    labels_by_key = {key: [0] * len(output) for key in INTENSITY_LABEL_KEYS}

    for valid_time_text, group in output.groupby("validTimeISO", sort=False):
        valid_time = _parse_valid_time(valid_time_text)
        candidates = grouped_reports.get(_hour_key(valid_time), [])
        for row in group.itertuples(index=True):
            labels = intensity_labels_for_sample(
                candidates,
                valid_time,
                float(getattr(row, "sampleLat")),
                float(getattr(row, "sampleLon")),
                radius_km=radius_km,
                window_hours=window_hours,
            )
            for key in INTENSITY_LABEL_KEYS:
                labels_by_key[key][int(row.Index)] = int(labels[key])

    for key, values in labels_by_key.items():
        output[f"label_{key}"] = values
        output[f"label_{key}"] = output[f"label_{key}"].astype(int)

    return output


def _summary(frame: Any, output: Path, summary_path: Path, reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "createdAtISO": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "output": str(output),
        "summary": str(summary_path),
        "rows": int(len(frame)),
        "reportsLoaded": int(len(reports)),
        "intensityLabels": {
            f"label_{key}": int(frame[f"label_{key}"].sum())
            for key in INTENSITY_LABEL_KEYS
            if f"label_{key}" in frame.columns
        },
    }


def add_intensity_labels(
    input_path: Path,
    output_path: Path,
    summary_path: Path,
    radius_km: float = 40.0,
    window_hours: float = 1.0,
    limit_rows: int | None = None,
) -> dict[str, Any]:
    pd = _require_deps()
    frame = pd.read_parquet(input_path)
    if limit_rows is not None:
        frame = frame.head(limit_rows).copy()

    run_dates = _run_dates_from_frame(frame)
    years, months = _years_months_from_run_dates(run_dates)
    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-intensity-labeler/1.0"
    reports = load_spc_reports(session, years, months, run_dates=run_dates)

    enriched = enrich_frame_with_intensity_labels(frame, reports, radius_km=radius_km, window_hours=window_hours)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(output_path, index=False)

    summary = _summary(enriched, output_path, summary_path, reports)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--radius-km", type=float, default=40.0)
    parser.add_argument("--window-hours", type=float, default=1.0)
    parser.add_argument("--limit-rows", type=int, default=None, help="Optional smoke-test row limit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = add_intensity_labels(
        args.input,
        args.output,
        args.summary,
        radius_km=args.radius_km,
        window_hours=args.window_hours,
        limit_rows=args.limit_rows,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
