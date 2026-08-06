"""Export generated AutoOutlook artifacts as a static API tree.

This is used by the static Cloudflare Pages hosting path:

1. Generate artifacts with ``backend.ml.outlook_pipeline``.
2. Build the Vite frontend into ``dist``.
3. Export JSON/GeoJSON files into ``dist/_api``.
4. Let Cloudflare Pages Functions map ``/api/*`` routes to those static files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "backend" / "artifacts" / "latest_incremental_complete"
DEFAULT_LEGACY_ARTIFACT_DIR = PROJECT_ROOT / "backend" / "artifacts" / "latest"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist" / "_api"
FULL_INCREMENTAL_FORECAST_HOURS = set(range(49))
D2_ARCHIVE_FILENAMES = (
    "verification.json",
    "risk-polygons.geojson",
    "hazard-probability-shapes.geojson",
    "probability-tile.json",
    "spc-day2-category.geojson",
    "risk-polygons-pure.geojson",
    "hazard-probability-shapes-pure.geojson",
    "probability-tile-pure.json",
    "storm-reports.json",
)
D2_REQUIRED_FILENAMES = {
    "verification.json",
    "risk-polygons.geojson",
    "probability-tile.json",
    "spc-day2-category.geojson",
}
MAX_REMOTE_D2_FILE_BYTES = 64 * 1024 * 1024


def default_production_base_url() -> str:
    carry_forward_override = os.environ.get("AUTOOUTLOOK_D2_CARRY_FORWARD_BASE_URL", "").strip()
    if carry_forward_override:
        return carry_forward_override.rstrip("/")
    explicit = os.environ.get("AUTOOUTLOOK_PRODUCTION_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    index_url = os.environ.get("AUTOOUTLOOK_PRODUCTION_INDEX_URL", "").strip()
    parts = urlsplit(index_url)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--legacy-artifact-dir", type=Path, default=DEFAULT_LEGACY_ARTIFACT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--production-base-url",
        default=default_production_base_url(),
        help="Current Cloudflare production origin used to carry a still-valid D2 export across ephemeral CI runs.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def copy_if_exists(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def copy_json_if_exists(source: Path, target: Path) -> bool:
    payload = read_json(source)
    if payload is None:
        return False
    write_json(target, payload)
    return True


def copy_first_existing(sources: list[Path], target: Path) -> Path | None:
    for source in sources:
        if copy_if_exists(source, target):
            return source
    return None


def coerce_hours(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    hours: list[int] = []
    for item in value:
        try:
            hour = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 48:
            hours.append(hour)
    return sorted(set(hours))


def probability_max(metadata: dict[str, Any]) -> dict[str, float]:
    stats = metadata.get("probabilityStats") if isinstance(metadata.get("probabilityStats"), dict) else {}
    values = (
        stats.get("categoryConsistencyProbabilityMax")
        or stats.get("cappedProbabilityMax")
        or stats.get("environmentalCappedProbabilityMax")
        or stats.get("rawProbabilityMax")
        or {}
    )
    if not isinstance(values, dict):
        values = {}
    return {hazard: float(values.get(hazard, 0) or 0) for hazard in ("tornado", "hail", "wind")}


def import_artifact_server_helpers(artifact_dir: Path):
    os.environ["AUTOOUTLOOK_ARTIFACT_DIR"] = str(artifact_dir)
    os.environ["AUTOOUTLOOK_INCREMENTAL_ARTIFACT_DIR"] = str(artifact_dir)
    os.environ["AUTOOUTLOOK_INCREMENTAL_COMPLETE_ARTIFACT_DIR"] = str(artifact_dir)
    os.environ["AUTOOUTLOOK_FORECAST_SOURCE"] = "artifact"
    os.environ["AUTOOUTLOOK_ENABLE_LIVE_BUILD"] = "false"
    import importlib
    if "backend.server" in sys.modules:
        server = importlib.reload(sys.modules["backend.server"])
    else:
        from backend import server  # noqa: PLC0415

    return server


def build_incremental_summary(index: dict[str, Any], artifact_dir: Path, helpers) -> dict[str, Any]:
    hours: list[dict[str, Any]] = []
    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        metadata = read_json(artifact_dir / "hours" / f"f{forecast_hour:02d}" / "metadata.json")
        if not isinstance(metadata, dict):
            continue
        category_counts = metadata.get("categoryCounts") or {}
        if not isinstance(category_counts, dict):
            category_counts = {}
        raw_probability_max = probability_max(metadata)
        category = helpers._max_category_from_counts(category_counts)
        display_probability_max = helpers._cap_probabilities_for_category(raw_probability_max, category)
        main_hazard = helpers._main_hazard_from_probabilities(raw_probability_max)
        total_cells = sum(int(value) for value in category_counts.values() if isinstance(value, (int, float)))
        active_cells = total_cells - int(category_counts.get("NONE", 0) or 0)
        hours.append({
            "forecastHour": int(metadata.get("forecastHour", forecast_hour)),
            "validTimeISO": metadata.get("validTimeISO"),
            "category": category,
            "mainHazard": main_hazard,
            "peakHazardProbability": helpers._max_hazard_probability(display_probability_max),
            "significantSevere": helpers._has_significant_probability(display_probability_max, category, main_hazard),
            "coverage": active_cells / total_cells if total_cells > 0 else 0,
            "categoryCounts": category_counts,
            "probabilityMax": display_probability_max,
        })
    return {
        "cycle": index.get("cycle"),
        "cycleTimeISO": index.get("cycleTimeISO"),
        "generatedAtISO": index.get("generatedAtISO"),
        "hours": sorted(hours, key=lambda item: item["forecastHour"]),
    }


def merged_risk_polygons(index: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        payload = read_json(artifact_dir / "hours" / f"f{forecast_hour:02d}" / "risk_polygons.geojson")
        if isinstance(payload, dict) and isinstance(payload.get("features"), list):
            features.extend(payload["features"])
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "incremental_artifacts",
            "cycle": index.get("cycle"),
            "cycleTimeISO": index.get("cycleTimeISO"),
            "generatedAtISO": index.get("generatedAtISO"),
        },
    }


def lightweight_probability_tiles(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle": index.get("cycle"),
        "featureSchemaHash": index.get("featureSchemaHash"),
        "riskLabels": index.get("riskLabels"),
        "gridStride": index.get("gridStride"),
        "tileStride": index.get("tileStride"),
        "environmentalCapsApplied": True,
        "categoryConsistencyCapsApplied": True,
        "hours": [],
        "staticExportNote": "Per-hour probability tiles are served from /api/outlook/incremental/hour/:hour/probability-tile.",
    }


def validate_full_index(index: dict[str, Any]) -> None:
    ready = set(coerce_hours(index.get("readyForecastHours")))
    model = index.get("cycleDetection", {}).get("cyclePolicy", {}).get("model", "HRRR").upper()
    expected = set(range(49))

    if index.get("status") != "complete" or not expected.issubset(ready):
        missing = sorted(expected - ready)
        raise ValueError(
            f"Refusing static export because artifacts are not a complete {model} set. "
            f"status={index.get('status')!r} missing={missing[:10]}"
        )


def _in_progress_merge_date(dates: list[str]) -> str | None:
    """Return the merged-D1 date whose 12Z–12Z convective window currently
    contains "now" (UTC). SPC storm reports accumulate over this window, so the
    default (no-date) storm-reports fallback should target this day rather than
    the newest cycle date — which, before 12Z, is a convective day that has not
    started yet and therefore has an empty/non-updating SPC report file.
    Returns ``None`` when no listed date is in progress (e.g. all are complete).
    """
    now = datetime.now(timezone.utc)
    for date_str in dates:
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            continue
        start = datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        if start <= now < end:
            return date_str
    return None


def _write_lightweight_tile(tile_path: Path, dst_path: Path, label: str) -> None:
    """Copy a merged probability tile with the heavy grids stripped.

    The merged maps render categories/hazards from the vector risk + hazard
    shape collections, so the client only needs the lightweight tile (vector
    shapes + metadata); dropping ``categoryOrdinal`` / ``categoryLabel`` /
    ``probabilities`` keeps the static payload small.
    """
    if not tile_path.exists():
        return
    try:
        tile = json.loads(tile_path.read_text(encoding="utf-8"))
        if isinstance(tile, dict):
            tile["categoryOrdinal"] = []
            tile["categoryLabel"] = []
            tile["probabilities"] = {}
            write_json(dst_path, tile)
    except Exception as e:
        print(f"Warning: failed to make lightweight {label} tile: {e}")


def export_merged_d1_archives(output_dir: Path, artifact_root: Path, helpers) -> None:
    # 1. Get available merge dates list
    dates = helpers._available_merge_dates_list(model="hrrr")
    if not dates:
        print("No available merge dates found for static export.")
        return
        
    print(f"Exporting merged D1 archives for dates: {dates}")
    
    # 2. Create target directory
    merged_d1_out_dir = output_dir / "outlook" / "merged-d1"
    merged_d1_out_dir.mkdir(parents=True, exist_ok=True)
    
    # 3. Write available dates list
    write_json(merged_d1_out_dir / "available-dates.json", {"dates": dates})
    
    latest_date_dir = None
    date_dirs: dict[str, Path] = {}
    
    for date_str in dates:
        merged_dir = helpers._generate_or_get_merged_d1_dir(date_str, model="hrrr")
        if merged_dir is None or not merged_dir.exists():
            print(f"Warning: could not resolve merged D1 dir for date {date_str}")
            continue
            
        # Target output folder for this date
        date_out_dir = merged_d1_out_dir / date_str
        date_out_dir.mkdir(parents=True, exist_ok=True)
        date_dirs[date_str] = date_out_dir
        
        # Copy small files
        copy_if_exists(merged_dir / "merged_verification_summary.json", date_out_dir / "verification.json")
        copy_if_exists(merged_dir / "merged_risk_polygons.geojson", date_out_dir / "risk-polygons.geojson")
        copy_if_exists(merged_dir / "merged_hazard_probability_shapes.geojson", date_out_dir / "hazard-probability-shapes.geojson")
        # Pure ("Our Model" / no SPC blend) variants, served when backing=pure.
        copy_if_exists(merged_dir / "merged_risk_polygons_pure.geojson", date_out_dir / "risk-polygons-pure.geojson")
        copy_if_exists(merged_dir / "merged_hazard_probability_shapes_pure.geojson", date_out_dir / "hazard-probability-shapes-pure.geojson")

        # Process and write lightweight probability tile (remove heavy lat/lon and probability grids)
        _write_lightweight_tile(merged_dir / "merged_probability_tile.json", date_out_dir / "probability-tile.json", "D1")
        _write_lightweight_tile(merged_dir / "merged_probability_tile_pure.json", date_out_dir / "probability-tile-pure.json", "D1 pure")
                
        # Fetch and write storm reports
        try:
            reports = helpers.fetch_spc_daily_storm_reports(date_str)
            write_json(date_out_dir / "storm-reports.json", {"reports": reports})
        except Exception as e:
            print(f"Warning: failed to fetch storm reports for {date_str}: {e}")
            write_json(date_out_dir / "storm-reports.json", {"reports": []})
                
        if latest_date_dir is None:
            latest_date_dir = date_out_dir

    # 4. Copy latest date's files directly as default fallbacks
    if latest_date_dir is not None:
        copy_if_exists(latest_date_dir / "verification.json", merged_d1_out_dir / "verification.json")
        copy_if_exists(latest_date_dir / "risk-polygons.geojson", merged_d1_out_dir / "risk-polygons.geojson")
        copy_if_exists(latest_date_dir / "hazard-probability-shapes.geojson", merged_d1_out_dir / "hazard-probability-shapes.geojson")
        copy_if_exists(latest_date_dir / "probability-tile.json", merged_d1_out_dir / "probability-tile.json")
        copy_if_exists(latest_date_dir / "risk-polygons-pure.geojson", merged_d1_out_dir / "risk-polygons-pure.geojson")
        copy_if_exists(latest_date_dir / "hazard-probability-shapes-pure.geojson", merged_d1_out_dir / "hazard-probability-shapes-pure.geojson")
        copy_if_exists(latest_date_dir / "probability-tile-pure.json", merged_d1_out_dir / "probability-tile-pure.json")
        print(f"Default fallback merged D1 set to latest date: {dates[0]}")

        # Storm reports accumulate over the 12Z–12Z convective day, so the
        # default (no-date) reports should track the in-progress day rather than
        # the newest cycle date (whose window may not have started yet and thus
        # has empty/non-updating SPC reports). Fall back to the latest date when
        # no listed day is currently in progress.
        report_default_date = _in_progress_merge_date(dates)
        report_dir = date_dirs.get(report_default_date or "", latest_date_dir)
        copy_if_exists(report_dir / "storm-reports.json", merged_d1_out_dir / "storm-reports.json")
        print(
            "Default storm reports set to in-progress day: "
            f"{report_default_date or dates[0]}"
        )


def _future_d2_dates(payload: object, now: datetime | None = None) -> list[str]:
    values = payload.get("dates") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return []
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    future: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            continue
        # A forecast date represents the 12Z-to-12Z convective day. Keep the
        # previous 12Z cycle's D2 product through 00Z and 06Z refreshes on the
        # forecast date; it does not expire at midnight.
        expires_at = datetime(parsed.year, parsed.month, parsed.day, 12, tzinfo=timezone.utc)
        if reference_time < expires_at:
            future.add(parsed.isoformat())
    return sorted(future, reverse=True)


def _write_d2_default_files(merged_d2_out_dir: Path, latest_date_dir: Path) -> None:
    for filename in D2_ARCHIVE_FILENAMES:
        copy_if_exists(latest_date_dir / filename, merged_d2_out_dir / filename)


def carry_forward_merged_d2_archive(
    source_dir: Path,
    target_dir: Path,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Copy still-future D2 static files into a fresh regional export tree."""
    dates = _future_d2_dates(read_json(source_dir / "available-dates.json"), now)
    target_dir.mkdir(parents=True, exist_ok=True)
    for date_str in dates:
        source_date_dir = source_dir / date_str
        missing = sorted(name for name in D2_REQUIRED_FILENAMES if not (source_date_dir / name).is_file())
        if missing:
            raise RuntimeError(f"Cloudflare D2 carry-forward for {date_str} is incomplete: {', '.join(missing)}")
        target_date_dir = target_dir / date_str
        for filename in D2_ARCHIVE_FILENAMES:
            copy_if_exists(source_date_dir / filename, target_date_dir / filename)

    write_json(target_dir / "available-dates.json", {"dates": dates})
    if dates:
        _write_d2_default_files(target_dir, target_dir / dates[0])
    return dates


def _fetch_remote_json(url: str, timeout_seconds: int = 20) -> dict[str, Any] | list[Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json, application/geo+json",
            "User-Agent": "AutoOutlook-D2-Carry-Forward/1.0",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(MAX_REMOTE_D2_FILE_BYTES + 1)
    if len(raw) > MAX_REMOTE_D2_FILE_BYTES:
        raise RuntimeError(f"Cloudflare D2 artifact exceeds {MAX_REMOTE_D2_FILE_BYTES} bytes: {url}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, (dict, list)):
        raise RuntimeError(f"Cloudflare D2 artifact is not JSON: {url}")
    return payload


def hydrate_merged_d2_from_cloudflare(
    production_base_url: str,
    target_dir: Path,
    *,
    now: datetime | None = None,
    fetch_json=None,
) -> list[str]:
    """Hydrate a future D2 export from the currently served Cloudflare deployment."""
    fetch = fetch_json or _fetch_remote_json
    base_url = production_base_url.rstrip("/")
    if not base_url:
        write_json(target_dir / "available-dates.json", {"dates": []})
        return []
    cache_buster = int((now or datetime.now(timezone.utc)).timestamp())
    remote_root = f"{base_url}/_api/outlook/merged-d2"
    available = fetch(f"{remote_root}/available-dates.json?carry_forward={cache_buster}")
    dates = _future_d2_dates(available, now)
    target_dir.mkdir(parents=True, exist_ok=True)

    for date_str in dates:
        payloads: dict[str, dict[str, Any] | list[Any]] = {}
        for filename in D2_ARCHIVE_FILENAMES:
            url = f"{remote_root}/{date_str}/{filename}?carry_forward={cache_buster}"
            try:
                payloads[filename] = fetch(url)
            except Exception as exc:
                if filename in D2_REQUIRED_FILENAMES:
                    raise RuntimeError(
                        f"Cloudflare D2 carry-forward for {date_str} is missing required {filename}: {exc}"
                    ) from exc
                print(f"Warning: optional Cloudflare D2 artifact unavailable for {date_str}: {filename} ({exc})")
        payloads.setdefault("storm-reports.json", {"reports": []})
        target_date_dir = target_dir / date_str
        for filename, payload in payloads.items():
            write_json(target_date_dir / filename, payload)

    write_json(target_dir / "available-dates.json", {"dates": dates})
    if dates:
        _write_d2_default_files(target_dir, target_dir / dates[0])
    return dates


def export_merged_d2_archives(
    output_dir: Path,
    artifact_root: Path,
    helpers,
    *,
    carry_forward_dir: Path | None = None,
    production_base_url: str = "",
    now: datetime | None = None,
) -> None:
    # 1. Get available merged D2 dates list (12Z cycles reaching f48).
    dates = helpers._available_merge_d2_dates_list(model="hrrr")

    # 2. Create the target dir. Ephemeral CI runners do not retain the 12Z cache
    #    used by later 18Z/00Z/06Z exports, so carry the still-future static D2
    #    tree forward from Cloudflare (or the root export for the regional copy).
    merged_d2_out_dir = output_dir / "outlook" / "merged-d2"
    merged_d2_out_dir.mkdir(parents=True, exist_ok=True)

    if not dates:
        if carry_forward_dir is not None:
            dates = carry_forward_merged_d2_archive(carry_forward_dir, merged_d2_out_dir, now=now)
        elif production_base_url:
            dates = hydrate_merged_d2_from_cloudflare(production_base_url, merged_d2_out_dir, now=now)
        else:
            write_json(merged_d2_out_dir / "available-dates.json", {"dates": []})
        if dates:
            print(f"Carried forward Cloudflare D2 archives for dates: {dates}")
        else:
            print("No available merged D2 dates found for static export.")
        return

    write_json(merged_d2_out_dir / "available-dates.json", {"dates": dates})

    print(f"Exporting merged D2 archives for dates: {dates}")

    latest_date_dir = None

    for date_str in dates:
        merged_dir = helpers._generate_or_get_merged_d2_dir(date_str, model="hrrr")
        if merged_dir is None or not merged_dir.exists():
            print(f"Warning: could not resolve merged D2 dir for date {date_str}")
            continue

        date_out_dir = merged_d2_out_dir / date_str
        date_out_dir.mkdir(parents=True, exist_ok=True)

        # Copy small files (note: D2 backs against the SPC Day 2 categorical).
        copy_if_exists(merged_dir / "merged_verification_summary.json", date_out_dir / "verification.json")
        copy_if_exists(merged_dir / "merged_risk_polygons.geojson", date_out_dir / "risk-polygons.geojson")
        copy_if_exists(merged_dir / "merged_hazard_probability_shapes.geojson", date_out_dir / "hazard-probability-shapes.geojson")
        copy_if_exists(merged_dir / "spc_day2_cat.geojson", date_out_dir / "spc-day2-category.geojson")
        # Pure ("Our Model" / no SPC blend) variants, served when backing=pure.
        copy_if_exists(merged_dir / "merged_risk_polygons_pure.geojson", date_out_dir / "risk-polygons-pure.geojson")
        copy_if_exists(merged_dir / "merged_hazard_probability_shapes_pure.geojson", date_out_dir / "hazard-probability-shapes-pure.geojson")

        # Process and write lightweight probability tile (drop heavy grids; keep
        # the vector risk/hazard/cig shapes the merged maps actually render).
        _write_lightweight_tile(merged_dir / "merged_probability_tile.json", date_out_dir / "probability-tile.json", "D2")
        _write_lightweight_tile(merged_dir / "merged_probability_tile_pure.json", date_out_dir / "probability-tile-pure.json", "D2 pure")

        # D2 spans a future convective day, so there are no verifying storm
        # reports yet; write an empty list so the client gets a clean response.
        write_json(date_out_dir / "storm-reports.json", {"reports": []})

        if latest_date_dir is None:
            latest_date_dir = date_out_dir

    # 4. Copy latest date's files directly as default fallbacks
    if latest_date_dir is not None:
        copy_if_exists(latest_date_dir / "verification.json", merged_d2_out_dir / "verification.json")
        copy_if_exists(latest_date_dir / "risk-polygons.geojson", merged_d2_out_dir / "risk-polygons.geojson")
        copy_if_exists(latest_date_dir / "hazard-probability-shapes.geojson", merged_d2_out_dir / "hazard-probability-shapes.geojson")
        copy_if_exists(latest_date_dir / "probability-tile.json", merged_d2_out_dir / "probability-tile.json")
        copy_if_exists(latest_date_dir / "spc-day2-category.geojson", merged_d2_out_dir / "spc-day2-category.geojson")
        copy_if_exists(latest_date_dir / "risk-polygons-pure.geojson", merged_d2_out_dir / "risk-polygons-pure.geojson")
        copy_if_exists(latest_date_dir / "hazard-probability-shapes-pure.geojson", merged_d2_out_dir / "hazard-probability-shapes-pure.geojson")
        copy_if_exists(latest_date_dir / "probability-tile-pure.json", merged_d2_out_dir / "probability-tile-pure.json")
        copy_if_exists(latest_date_dir / "storm-reports.json", merged_d2_out_dir / "storm-reports.json")
        print(f"Default fallback merged D2 set to latest date: {dates[0]}")


def _refresh_recent_archived_reports(archive_dir, merged_dates, refresh_fn) -> None:
    """Re-fetch storm reports for archived ENH+ days still within report accrual.

    Covers days that already left the rolling merged-D1 window (so step 1 no
    longer touches them) but whose 12Z-12Z convective day closed recently enough
    that SPC may still be adding reports. Days already handled by the merged-day
    loop are skipped to avoid a duplicate fetch.
    """
    from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone

    from backend.ml.enh_plus_archive import archive_available_dates
    from backend.ml.historical_event_verification import event_window_for_date

    # Preliminary storm reports keep trickling in for a few days after the event.
    accrual_grace = _timedelta(days=3)
    now = _datetime.now(_timezone.utc)
    for date_str in archive_available_dates(archive_dir):
        if date_str in merged_dates:
            continue
        try:
            event_date = _date.fromisoformat(date_str)
            window = event_window_for_date(event_date)
            if window.end_time < now - accrual_grace:
                continue  # Convective day fully elapsed; reports are final.
            entry = refresh_fn(archive_dir, event_date)
            if entry is not None:
                print(
                    f"ENH+ archive (report refresh): {date_str} -> "
                    f"{entry['reportCounts']['total']} reports"
                )
        except Exception as exc:
            print(f"Warning: ENH+ archive report refresh failed for {date_str}: {exc}")


def export_spc_backed_hours(index: dict[str, Any], artifact_dir: Path, output_dir: Path, helpers) -> None:
    """Pre-compute the 50/50 SPC-blended hourly tiles for the static scrubber.

    Mirrors the dynamic ``/incremental/hour/<h>/spc-backed-tile`` endpoint in
    blend mode so the hourly "SPC Blend" toggle works on the static deployment.
    The SPC envelope comes from the merged-D1 cache, so this must run after
    :func:`export_merged_d1_archives`. Every ready hour is written (even when no
    SPC window covers it) so the tile always carries an ``spcBacking`` note and
    the client can render the "no SPC" state instead of 404-ing.
    """
    from datetime import datetime, timedelta, timezone

    try:
        from backend.ml.merged_outlook import spc_backed_hour_tile
    except Exception as exc:
        print(f"Warning: cannot import spc_backed_hour_tile; skipping hourly SPC backing: {exc}")
        return

    cycle_iso = index.get("cycleTimeISO")
    base_date = None
    if isinstance(cycle_iso, str):
        try:
            base_date = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        except Exception:
            base_date = None
    if base_date is None:
        base_date = datetime.now(timezone.utc).date()
    window_dates = [base_date, base_date + timedelta(days=1)]

    try:
        spc_geojsons = helpers._spc_geojsons_for_dates(window_dates, "hrrr")
    except Exception as exc:
        print(f"Warning: failed to load SPC geojsons for hourly backing: {exc}")
        spc_geojsons = []

    written = 0
    backed = 0
    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        tile = read_json(artifact_dir / "hours" / f"f{forecast_hour:02d}" / "probability_tile.json")
        if not isinstance(tile, dict):
            continue
        try:
            result = spc_backed_hour_tile(tile, spc_geojsons, mode="blend")
        except Exception as exc:
            print(f"Warning: SPC-backed hour F{forecast_hour:02d} failed: {exc}")
            continue
        target = output_dir / "outlook" / "incremental" / "hour" / f"f{forecast_hour:02d}" / "spc-backed-tile.json"
        write_json(target, result["tile"])
        written += 1
        if result.get("applied"):
            backed += 1
    print(f"Exported {written} SPC-backed hourly tiles ({backed} with an SPC envelope).")


def export_enh_plus_archive(output_dir: Path, artifact_root: Path, helpers) -> None:
    """Accumulate ENH+ (Enhanced risk or higher) days into a persistent archive.

    For each currently-available merged D1 day, if the AutoOutlook or SPC
    categorical reaches ENH+, the day's artifacts and (re-fetched) storm reports
    are added/refreshed in ``backend/artifacts/enh_plus_archive`` -- a directory
    persisted across deploys via an Actions cache -- and the accumulated archive
    is exported under ``outlook/enh-plus-archive/``.
    """
    from datetime import date as _date

    from backend.ml.enh_plus_archive import (
        archive_available_dates,
        refresh_reports_for_archived_date,
        repair_archived_spc_window,
        update_archive_for_date,
    )

    archive_dir = artifact_root / "enh_plus_archive"

    # 1. Refresh/accumulate from the current merged D1 days.
    merged_dates: list[str] = []
    for date_str in helpers._available_merge_dates_list(model="hrrr"):
        merged_dates.append(date_str)
        try:
            merged_dir = helpers._generate_or_get_merged_d1_dir(date_str, model="hrrr")
            if merged_dir is None or not merged_dir.exists():
                continue
            entry = update_archive_for_date(archive_dir, merged_dir, _date.fromisoformat(date_str))
            if entry is not None:
                print(f"ENH+ archive: {date_str} -> {entry['maxCategory']} ({entry['reportCounts']['total']} reports)")
        except Exception as exc:
            print(f"Warning: ENH+ archive update failed for {date_str}: {exc}")

    # 1b. Keep already-archived ENH+ days refreshing after they leave the rolling
    # 2-day merged-D1 window. SPC storm reports keep accruing for a few days after
    # the 12Z-12Z convective day, but step 1 only ever revisits the newest cycles,
    # so without this a day freezes the moment newer cycles push it out of view.
    # Storm reports need only the date, so no merged artifacts are required here.
    _refresh_recent_archived_reports(archive_dir, set(merged_dates), refresh_reports_for_archived_date)

    # 1c. Repair archived days whose SPC categorical was frozen on the wrong
    # convective day by an early-morning capture (no-op when already correct).
    for date_str in archive_available_dates(archive_dir):
        try:
            entry = repair_archived_spc_window(archive_dir, _date.fromisoformat(date_str))
            if entry is not None and entry.get("removed"):
                print(f"ENH+ archive: removed {date_str} (midday categorical {entry['maxCategory']} below ENH+)")
            elif entry is not None:
                print(f"ENH+ archive: repaired SPC window for {date_str} -> {entry['maxCategory']}")
        except Exception as exc:
            print(f"Warning: ENH+ archive SPC repair failed for {date_str}: {exc}")

    # 2. Export the accumulated archive tree.
    dates = archive_available_dates(archive_dir)
    out_dir = output_dir / "outlook" / "enh-plus-archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = read_json(archive_dir / "index.json") or {"dates": []}
    write_json(out_dir / "available-dates.json", index)

    if not dates:
        print("ENH+ archive: no ENH+ days accumulated yet.")
        return

    geojson_names = (
        "verification.json",
        "risk-polygons.geojson",
        "hazard-probability-shapes.geojson",
        "spc-day1-category.geojson",
        "spc-hazard-shapes.geojson",
        "risk-polygons-pure.geojson",
        "hazard-probability-shapes-pure.geojson",
        "storm-reports.json",
    )
    for date_str in dates:
        src = archive_dir / date_str
        dst = out_dir / date_str
        for name in geojson_names:
            copy_if_exists(src / name, dst / name)
        # Lightweight probability tile (drop heavy grids; keep the vector shapes).
        tile_path = src / "probability-tile.json"
        if tile_path.exists():
            try:
                tile = json.loads(tile_path.read_text(encoding="utf-8"))
                if isinstance(tile, dict):
                    tile["categoryOrdinal"] = []
                    tile["categoryLabel"] = []
                    tile["probabilities"] = {}
                    write_json(dst / "probability-tile.json", tile)
            except Exception as exc:
                print(f"Warning: failed to make lightweight ENH+ tile for {date_str}: {exc}")

    print(f"ENH+ archive: exported {len(dates)} day(s) -> {out_dir}")


def export_static_api(
    artifact_dir: Path,
    legacy_artifact_dir: Path,
    output_dir: Path,
    *,
    d2_carry_forward_dir: Path | None = None,
    production_base_url: str = "",
) -> None:
    artifact_dir = artifact_dir.resolve()
    legacy_artifact_dir = legacy_artifact_dir.resolve()
    output_dir = output_dir.resolve()
    index = read_json(artifact_dir / "index.json")
    if not isinstance(index, dict):
        raise SystemExit(f"Missing incremental index: {artifact_dir / 'index.json'}")
    validate_full_index(index)

    helpers = import_artifact_server_helpers(artifact_dir)
    forecast = helpers._artifact_forecast_bundle()
    if not isinstance(forecast, dict):
        raise SystemExit("Could not build /api/forecast from generated artifacts.")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "health.json", {
        "status": "ok",
        "service": "autooutlook-static-api",
        "generatedAtISO": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    })
    write_json(output_dir / "forecast.json", forecast)
    write_json(output_dir / "outlook" / "latest.json", index)
    write_json(output_dir / "outlook" / "incremental" / "index.json", index)
    write_json(output_dir / "outlook" / "incremental" / "summary.json", build_incremental_summary(index, artifact_dir, helpers))
    write_json(output_dir / "outlook" / "risk-polygons.geojson", merged_risk_polygons(index, artifact_dir))
    write_json(output_dir / "outlook" / "aggregate-risk-polygons.geojson", merged_risk_polygons(index, artifact_dir))
    write_json(output_dir / "outlook" / "probability-tiles.json", lightweight_probability_tiles(index))
    write_json(output_dir / "outlook" / "trends.json", helpers._outlook_trends_payload(
        current_dir=artifact_dir,
        forecast_hour=12,
        model="hrrr",
        legacy_dir=legacy_artifact_dir,
    ))

    copy_first_existing(
        [artifact_dir / "verification_summary.json", legacy_artifact_dir / "verification_summary.json"],
        output_dir / "outlook" / "verification.json",
    )
    copy_first_existing(
        [artifact_dir / "spc_day1_cat.geojson", legacy_artifact_dir / "spc_day1_cat.geojson"],
        output_dir / "outlook" / "spc-day1-category.geojson",
    )
    copy_if_exists(legacy_artifact_dir / "preview.png", output_dir / "outlook" / "preview.png")

    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        source_hour_dir = artifact_dir / "hours" / f"f{forecast_hour:02d}"
        target_hour_dir = output_dir / "outlook" / "incremental" / "hour" / f"f{forecast_hour:02d}"
        copy_json_if_exists(source_hour_dir / "risk_polygons.geojson", target_hour_dir / "risk-polygons.geojson")
        copy_json_if_exists(source_hour_dir / "probability_tile.json", target_hour_dir / "probability-tile.json")
        copy_json_if_exists(source_hour_dir / "metadata.json", target_hour_dir / "metadata.json")

    # Export merged D1 outlook archives
    export_merged_d1_archives(output_dir, artifact_dir.parent, helpers)

    # Pre-compute SPC-blended hourly tiles (needs the merged-D1 SPC cache above)
    export_spc_backed_hours(index, artifact_dir, output_dir, helpers)

    # Export merged D2 outlook archives (12Z cycle F24-F48)
    export_merged_d2_archives(
        output_dir,
        artifact_dir.parent,
        helpers,
        carry_forward_dir=d2_carry_forward_dir,
        production_base_url=production_base_url,
    )

    # Accumulate + export the auto ENH+ risk archive (persisted across deploys)
    export_enh_plus_archive(output_dir, artifact_dir.parent, helpers)

    print(json.dumps({
        "outputDir": str(output_dir),
        "cycle": index.get("cycle"),
        "cycleTimeISO": index.get("cycleTimeISO"),
        "readyForecastHours": len(coerce_hours(index.get("readyForecastHours"))),
    }, indent=2))


def main() -> None:
    args = parse_args()

    # If the user passed the default artifact directory, export the primary HRRR artifact tree.
    is_default_run = (args.artifact_dir == DEFAULT_ARTIFACT_DIR)

    if is_default_run:
        # 1. Export legacy/conus to output_dir (first, so it clears the root but keeps later subfolders)
        if DEFAULT_ARTIFACT_DIR.exists():
            try:
                print("Exporting CONUS artifacts (legacy root)...")
                export_static_api(
                    DEFAULT_ARTIFACT_DIR,
                    args.legacy_artifact_dir,
                    args.output_dir,
                    production_base_url=args.production_base_url,
                )

                print("Exporting CONUS artifacts (regional)...")
                export_static_api(
                    DEFAULT_ARTIFACT_DIR,
                    args.legacy_artifact_dir,
                    args.output_dir / "conus",
                    d2_carry_forward_dir=args.output_dir / "outlook" / "merged-d2",
                )
            except ValueError as exc:
                print(f"Warning: Skipping CONUS export: {exc}")
        else:
            print(f"CONUS artifacts dir not found: {DEFAULT_ARTIFACT_DIR}")

        pass
    else:
        try:
            export_static_api(
                args.artifact_dir,
                args.legacy_artifact_dir,
                args.output_dir,
                production_base_url=args.production_base_url,
            )
        except ValueError as exc:
            raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
