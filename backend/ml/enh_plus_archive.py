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
from datetime import date, datetime, timedelta, timezone
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

    # Never persist a prior-day SPC overlay. If the merged dir was built early
    # (before SPC issued the Day-1 outlook for this convective day) its SPC
    # categorical covers the wrong window (12Z D-1 -> 12Z D instead of
    # 12Z D -> 12Z D+1). Skip until a later refresh rebuilds it with the correct
    # issuance (see backend.server _generate_or_get_merged_dir freshness check).
    spc_expire = _spc_geojson_expire(spc_geojson)
    if spc_expire is not None and spc_expire != _convective_window_expire(event_date):
        return None

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


def refresh_reports_for_archived_date(
    archive_dir: Path,
    event_date: date,
    *,
    report_fetch_fn: ReportFetchFn | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    """Re-fetch storm reports for an ENH+ day already present in the archive.

    Unlike :func:`update_archive_for_date`, this needs no live merged D1
    artifacts -- it only rewrites ``storm-reports.json`` for a day that is
    already archived, and refreshes that day's report counts in the index. This
    keeps past ENH+ days accruing reports after they fall out of the rolling
    merged-D1 availability window, where :func:`update_archive_for_date` would
    no longer reach them. Returns the updated index entry, or ``None`` if the
    date is not archived. On a fetch error the previously archived reports are
    preserved (via :func:`_refresh_reports`).
    """
    archive_dir = Path(archive_dir)
    date_dir = archive_dir / event_date.isoformat()
    if not date_dir.is_dir():
        return None

    index = _read_json(archive_dir / "index.json")
    entry: dict[str, Any] | None = None
    if isinstance(index, dict) and isinstance(index.get("dates"), list):
        for item in index["dates"]:
            if isinstance(item, Mapping) and item.get("date") == event_date.isoformat():
                entry = dict(item)
                break
    if entry is None:
        return None

    window = event_window_for_date(event_date)
    reports = _refresh_reports(date_dir, event_date, window, report_fetch_fn, session)
    counts = report_counts(reports)
    _write_json(
        date_dir / "storm-reports.json",
        {"reports": reports, "counts": counts, "updatedAtISO": _now_iso()},
    )

    entry["reportCounts"] = counts
    entry["windowStartISO"] = window.start_iso
    entry["windowEndISO"] = window.end_iso
    entry["updatedAtISO"] = _now_iso()
    _upsert_index_entry(archive_dir, entry)
    return entry


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


def remove_archived_date(archive_dir: Path, event_date: date) -> bool:
    """Delete a day's archive dir and drop it from the index. Returns True if anything was removed."""
    archive_dir = Path(archive_dir)
    removed = False
    date_dir = archive_dir / event_date.isoformat()
    if date_dir.is_dir():
        shutil.rmtree(date_dir, ignore_errors=True)
        removed = True
    index_path = archive_dir / "index.json"
    index = _read_json(index_path)
    if isinstance(index, dict) and isinstance(index.get("dates"), list):
        kept = [
            item
            for item in index["dates"]
            if not (isinstance(item, Mapping) and item.get("date") == event_date.isoformat())
        ]
        if len(kept) != len(index["dates"]):
            _write_json(index_path, {"dates": kept, "generatedAtISO": _now_iso()})
            removed = True
    return removed


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


def _parse_iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _convective_window_expire(event_date: date) -> datetime:
    """End of the 12Z-12Z convective day anchored to ``event_date`` (== 12Z D+1)."""
    return datetime(
        event_date.year, event_date.month, event_date.day, 12, 0, 0, tzinfo=timezone.utc
    ) + timedelta(days=1)


def _spc_geojson_expire(spc_geojson: Mapping[str, Any] | None) -> datetime | None:
    """Latest ``EXPIRE_ISO`` across an SPC categorical geojson, or ``None``."""
    if not isinstance(spc_geojson, Mapping):
        return None
    latest: datetime | None = None
    for feature in spc_geojson.get("features", []):
        if not isinstance(feature, Mapping):
            continue
        props = feature.get("properties", {})
        expire = _parse_iso_utc(props.get("EXPIRE_ISO")) if isinstance(props, Mapping) else None
        if expire is not None and (latest is None or expire > latest):
            latest = expire
    return latest


def _spc_geojson_window(spc_geojson: Mapping[str, Any] | None) -> tuple[datetime | None, datetime | None]:
    """``(VALID_ISO, EXPIRE_ISO)`` from the first feature exposing both."""
    if not isinstance(spc_geojson, Mapping):
        return None, None
    for feature in spc_geojson.get("features", []):
        if not isinstance(feature, Mapping):
            continue
        props = feature.get("properties", {})
        if not isinstance(props, Mapping):
            continue
        valid = _parse_iso_utc(props.get("VALID_ISO"))
        expire = _parse_iso_utc(props.get("EXPIRE_ISO"))
        if valid is not None and expire is not None:
            return valid, expire
    return None, None


def repair_archived_spc_window(
    archive_dir: Path,
    event_date: date,
    *,
    session: requests.Session | None = None,
    spc_fetch_fn: Callable[[date], Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Repair an archived day whose stored SPC categorical covers the wrong day.

    Early-morning captures (before the freshness/gate fixes) could freeze the
    prior day's overnight SPC issuance onto a day's archive (its window expires
    at 12Z D rather than 12Z D+1). The source HRRR cycle may since have been
    purged, so re-running the full merge is not always possible; instead this
    re-fetches only the correct SPC Day-1 categorical for the convective day from
    SPC's permanent archive and rewrites the SPC overlay plus the index category.
    The (00Z Day-1) AutoOutlook grid is intentionally left unchanged.

    Cheap no-op (no network) when the stored SPC window is already correct.
    Returns the updated index entry, or ``None`` if nothing changed.
    """
    archive_dir = Path(archive_dir)
    date_dir = archive_dir / event_date.isoformat()
    if not date_dir.is_dir():
        return None

    expected_expire = _convective_window_expire(event_date)
    spc_path = date_dir / "spc-day1-category.geojson"
    if _spc_geojson_expire(_read_json(spc_path)) == expected_expire:
        return None  # already correctly windowed

    try:
        if spc_fetch_fn is not None:
            result: Any = spc_fetch_fn(event_date)
        else:
            from backend.ml.merged_outlook import fetch_archived_spc_category

            result = fetch_archived_spc_category(event_date, session=session, day=1)
    except Exception:
        return None

    spc_geojson = result.get("categoryGeojson") if isinstance(result, Mapping) else result
    if not isinstance(spc_geojson, Mapping) or _spc_geojson_expire(spc_geojson) != expected_expire:
        return None  # could not obtain a correctly-windowed issuance; leave as-is

    spc_label, spc_ord = max_spc_category(spc_geojson)
    summary = _read_json(date_dir / "verification.json")
    predicted = summary.get("predictedCategories") if isinstance(summary, Mapping) else None
    auto_label, auto_ord = predicted_max_category(predicted)

    # Strict midday-categorical inclusion: a day belongs in the ENH+ archive only
    # if the AutoOutlook or the corrected midday SPC categorical reaches ENH+. A
    # wrong-window overlay can have inflated a sub-ENH day into the archive (e.g.
    # the prior day's MDT, or an evening upgrade); once the correct issuance is
    # known and it is below ENH+, drop the day. Guarded on a known AutoOutlook
    # prediction so incomplete metadata never deletes a day.
    if predicted is not None and max(auto_ord, spc_ord) < ENH_PLUS_MIN_ORDINAL:
        remove_archived_date(archive_dir, event_date)
        return {
            "date": event_date.isoformat(),
            "removed": True,
            "maxCategory": SPC_RISK_LABELS[max(auto_ord, spc_ord)],
        }

    _write_json(spc_path, spc_geojson)

    # Patch the summary's stale SPC window/category fields for consistency. The
    # comparison metrics (officialCategories/agreement) are left as-is: recomputing
    # them needs the original HRRR grid and the source cycle may be gone.
    if isinstance(summary, dict):
        valid_dt, expire_dt = _spc_geojson_window(spc_geojson)
        if valid_dt is not None:
            summary["spcValidTimeISO"] = valid_dt.isoformat().replace("+00:00", "Z")
        if expire_dt is not None:
            summary["spcExpireTimeISO"] = expire_dt.isoformat().replace("+00:00", "Z")
        summary["spcMaxCategory"] = spc_label
        summary["spcWindowRepairedAtISO"] = _now_iso()
        _write_json(date_dir / "verification.json", summary)

    index = _read_json(archive_dir / "index.json")
    entry: dict[str, Any] | None = None
    if isinstance(index, dict) and isinstance(index.get("dates"), list):
        for item in index["dates"]:
            if isinstance(item, Mapping) and item.get("date") == event_date.isoformat():
                entry = dict(item)
                break
    if entry is None:
        return None

    entry["spcMaxCategory"] = spc_label
    entry["autoMaxCategory"] = auto_label
    entry["maxCategory"] = SPC_RISK_LABELS[max(auto_ord, spc_ord)]
    entry["updatedAtISO"] = _now_iso()
    _upsert_index_entry(archive_dir, entry)
    return entry
