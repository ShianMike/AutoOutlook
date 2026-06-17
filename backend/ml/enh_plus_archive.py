"""Auto-accumulating archive of ENH+ (Enhanced risk or higher) outlook days.

Unlike the curated historical ENH+ catalog (hardcoded dates baked into a
committed TS module), this archive is built automatically from the live merged
Day 1 products on every refresh run. Any convective day whose merged AutoOutlook
category *or* the official SPC categorical reaches ENH+ is copied into a
persistent archive together with its SPC storm reports. The storm reports are
re-fetched on every run so they fill in as the convective day plays out.

The archive directory is persisted across deploys (via an Actions cache keyed
stably, not per-run), so past ENH+ days remain available and keep updating until
their convective day has fully elapsed.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from backend.ml.gridded_outlook import SPC_RISK_LABELS
from backend.ml.historical_event_verification import (
    ENH_PLUS_MIN_ORDINAL,
    event_window_for_date,
    fetch_spc_daily_storm_reports,
    filter_spc_reports_for_event_window,
    max_spc_category,
    report_counts,
    risk_ordinal,
)

# Category-count thresholds mirror backend.server._max_category_from_counts so
# the archive's notion of the AutoOutlook category matches what the rest of the
# app shows.
_CATEGORY_MIN_CELLS: dict[str, int] = {
    "NONE": 0,
    "TSTM": 1,
    "MRGL": 100,
    "SLGT": 350,
    "ENH": 1200,
    "MDT": 2500,
    "HIGH": 4500,
}

# Merged artifact filename -> archive filename (served names mirror the static API).
_ARCHIVE_FILES: tuple[tuple[str, str], ...] = (
    ("merged_verification_summary.json", "verification.json"),
    ("merged_risk_polygons.geojson", "risk-polygons.geojson"),
    ("merged_hazard_probability_shapes.geojson", "hazard-probability-shapes.geojson"),
    ("merged_probability_tile.json", "probability-tile.json"),
    ("spc_day1_cat.geojson", "spc-day1-category.geojson"),
)

ReportFetchFn = Callable[[date], list[dict[str, Any]]]


def predicted_max_category(category_counts: Mapping[str, Any] | None) -> tuple[str, int]:
    """Highest AutoOutlook category whose cell count meets its minimum threshold."""
    counts = category_counts or {}
    best = "NONE"
    for label in SPC_RISK_LABELS:
        count = int(counts.get(label, 0) or 0)
        if count >= _CATEGORY_MIN_CELLS.get(label, 0):
            best = label
    return best, SPC_RISK_LABELS.index(best)


def day_max_ordinal(
    summary: Mapping[str, Any] | None,
    spc_geojson: Mapping[str, Any] | None,
) -> tuple[str, int, str, int]:
    """Return ``(auto_label, auto_ord, spc_label, spc_ord)`` for a merged day."""
    auto_label, auto_ord = predicted_max_category(
        (summary or {}).get("predictedCategories") if isinstance(summary, Mapping) else None
    )
    if isinstance(spc_geojson, Mapping):
        spc_label, spc_ord = max_spc_category(spc_geojson)
    elif isinstance(summary, Mapping) and summary.get("spcMaxCategory"):
        spc_label = str(summary.get("spcMaxCategory"))
        spc_ord = risk_ordinal(spc_label)
    else:
        spc_label, spc_ord = "NONE", 0
    return auto_label, auto_ord, spc_label, spc_ord


def day_is_enh_plus(
    summary: Mapping[str, Any] | None,
    spc_geojson: Mapping[str, Any] | None = None,
) -> bool:
    """True when either the AutoOutlook or the SPC category reaches ENH+."""
    _, auto_ord, _, spc_ord = day_max_ordinal(summary, spc_geojson)
    return max(auto_ord, spc_ord) >= ENH_PLUS_MIN_ORDINAL


def update_archive_for_date(
    archive_dir: Path,
    merged_dir: Path,
    event_date: date,
    *,
    report_fetch_fn: ReportFetchFn | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    """Add/refresh ``event_date`` in the ENH+ archive from its merged D1 artifacts.

    Returns the archive index entry, or ``None`` if the day is not ENH+ (in which
    case the archive is left unchanged). Storm reports are always re-fetched so
    they update as the convective day progresses; on a fetch error any previously
    archived reports are preserved.
    """
    archive_dir = Path(archive_dir)
    merged_dir = Path(merged_dir)

    summary = _read_json(merged_dir / "merged_verification_summary.json")
    if not isinstance(summary, dict):
        return None
    spc_geojson = _read_json(merged_dir / "spc_day1_cat.geojson")

    auto_label, auto_ord, spc_label, spc_ord = day_max_ordinal(summary, spc_geojson)
    if max(auto_ord, spc_ord) < ENH_PLUS_MIN_ORDINAL:
        return None

    date_dir = archive_dir / event_date.isoformat()
    date_dir.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in _ARCHIVE_FILES:
        src = merged_dir / src_name
        if src.is_file():
            shutil.copyfile(src, date_dir / dst_name)

    window = event_window_for_date(event_date)
    reports = _refresh_reports(date_dir, event_date, window, report_fetch_fn, session)
    counts = report_counts(reports)
    _write_json(
        date_dir / "storm-reports.json",
        {"reports": reports, "counts": counts, "updatedAtISO": _now_iso()},
    )

    entry = {
        "date": event_date.isoformat(),
        "maxCategory": SPC_RISK_LABELS[max(auto_ord, spc_ord)],
        "autoMaxCategory": auto_label,
        "spcMaxCategory": spc_label,
        "reportCounts": counts,
        "windowStartISO": window.start_iso,
        "windowEndISO": window.end_iso,
        "updatedAtISO": _now_iso(),
    }
    _upsert_index_entry(archive_dir, entry)
    return entry


def archive_available_dates(archive_dir: Path) -> list[str]:
    """Archived ENH+ dates, newest first."""
    index = _read_json(Path(archive_dir) / "index.json")
    if not isinstance(index, dict):
        return []
    dates = index.get("dates")
    if not isinstance(dates, list):
        return []
    return [str(item.get("date")) for item in dates if isinstance(item, Mapping) and item.get("date")]


def _refresh_reports(
    date_dir: Path,
    event_date: date,
    window: Any,
    report_fetch_fn: ReportFetchFn | None,
    session: requests.Session | None,
) -> list[dict[str, Any]]:
    try:
        if report_fetch_fn is not None:
            raw = report_fetch_fn(event_date)
        else:
            raw = fetch_spc_daily_storm_reports(event_date, session)
        return filter_spc_reports_for_event_window(raw, window)
    except Exception:
        existing = _read_json(date_dir / "storm-reports.json")
        if isinstance(existing, dict) and isinstance(existing.get("reports"), list):
            return existing["reports"]
        return []


def _upsert_index_entry(archive_dir: Path, entry: Mapping[str, Any]) -> None:
    index_path = Path(archive_dir) / "index.json"
    index = _read_json(index_path)
    dates = index.get("dates") if isinstance(index, dict) and isinstance(index.get("dates"), list) else []
    kept = [item for item in dates if isinstance(item, Mapping) and item.get("date") != entry["date"]]
    kept.append(dict(entry))
    kept.sort(key=lambda item: str(item.get("date")), reverse=True)
    _write_json(index_path, {"dates": kept, "generatedAtISO": _now_iso()})


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
