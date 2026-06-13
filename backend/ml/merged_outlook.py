"""Merge multiple HRRR cycle artifacts into a combined D1 outlook for SPC comparison."""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import requests

from backend.ml.spc_verification import (
    _lat_lon_grid,
    compare_prediction_to_spc,
    fetch_current_spc_day1_category,
    official_category_grid,
)
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    _category_probability_cap_grid,
    _clean_projected_geometry,
    _drop_small_projected_parts,
    _projected_component_count,
    _smooth_display_projected_geometry,
    apply_category_probability_ceiling,
    constrain_hazard_probability_shapes_to_risk_support,
    risk_polygons_from_grid,
    hazard_probability_shapes_from_grids,
)


SPC_ANCHORED_OUTLOOK_FLAG = "AUTOOUTLOOK_SPC_ANCHORED_OUTLOOK"
SPC_SUPPORT_WEIGHT_FLAG = "AUTOOUTLOOK_SPC_SUPPORT_WEIGHT"
DEFAULT_SPC_SUPPORT_WEIGHT = 0.50
_MAX_CATEGORY_ORDINAL = len(SPC_RISK_LABELS) - 1


def _spc_implied_hazard_probability(hazard: str, spc_category_grid: np.ndarray) -> np.ndarray:
    """Per-cell probability implied by the SPC category for a single hazard.

    SPC's Day 1 categorical outlook is hazard-agnostic (one category per cell),
    so there is no true per-hazard SPC probability. We approximate the SPC
    "target" for each hazard as the midpoint of that hazard's probability band
    for the SPC category: ``0.5 * (band_floor + band_ceiling)``. Cells where SPC
    draws no risk (category NONE) imply a zero target.
    """
    grid = np.asarray(spc_category_grid, dtype=np.int16)
    ceiling = _category_probability_cap_grid(hazard, grid)
    lower_category = np.clip(grid - 1, 0, _MAX_CATEGORY_ORDINAL).astype(np.int16)
    floor = _category_probability_cap_grid(hazard, lower_category)
    implied = 0.5 * (floor + ceiling)
    return np.where(grid <= 0, 0.0, implied)


def _spc_anchored_outlook_enabled() -> bool:
    """Whether the merged outlook is fully anchored to the SPC Day 1 outlook.

    Full anchoring is equivalent to an SPC support weight of 1.0. It is
    disabled by default; the default behavior is a partial SPC backing (see
    :func:`_spc_support_weight`). Set ``AUTOOUTLOOK_SPC_ANCHORED_OUTLOOK`` truthy
    to force full anchoring.
    """
    value = os.environ.get(SPC_ANCHORED_OUTLOOK_FLAG)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _spc_support_weight() -> float:
    """How strongly the official SPC Day 1 outlook backs the merged outlook.

    Returns a weight in ``[0, 1]`` used to blend the HRRR/XGBoost categorical
    grid toward SPC: ``final = (1 - w) * hrrr + w * spc``. ``0`` is pure HRRR,
    ``1`` is full SPC anchoring, and the default ``0.50`` means the outlook is a
    50/50 blend of HRRR/XGBoost and SPC.

    Resolution order:
    1. ``AUTOOUTLOOK_SPC_SUPPORT_WEIGHT`` (explicit float, clamped to [0, 1]).
    2. ``AUTOOUTLOOK_SPC_ANCHORED_OUTLOOK`` truthy -> 1.0 (full anchoring).
    3. Default 0.50.
    """
    raw = os.environ.get(SPC_SUPPORT_WEIGHT_FLAG)
    if raw is not None and raw.strip() != "":
        try:
            return min(1.0, max(0.0, float(raw)))
        except ValueError:
            pass
    if _spc_anchored_outlook_enabled():
        return 1.0
    return DEFAULT_SPC_SUPPORT_WEIGHT


def blend_merged_outlook_with_spc(
    lats: np.ndarray,
    lons: np.ndarray,
    hrrr_category_grid: np.ndarray,
    hrrr_probabilities: Mapping[str, np.ndarray],
    spc_geojson: Mapping[str, Any],
    weight: float = DEFAULT_SPC_SUPPORT_WEIGHT,
    mode: str = "blend",
) -> dict[str, Any]:
    """Back the HRRR/XGBoost outlook with the official SPC categorical outlook.

    Two modes:

    ``"blend"`` (default, used by the merged D1/D2 products): the categorical grid
    is blended per cell toward SPC by ``weight``::

        final_ordinal = round((1 - weight) * hrrr_ordinal + weight * spc_ordinal)

    so ``weight=0`` leaves the HRRR outlook untouched, ``weight=1`` conforms it
    fully to SPC, and ``weight=0.5`` produces a 50/50 blend. HRRR hazard
    probabilities are blended toward the SPC-category-implied level by the same
    weight.

    ``"ceiling"`` (used by the hourly scrubber): SPC is treated as a per-day
    envelope rather than a symmetric target. The HRRR category is capped at the
    SPC category and zeroed outside SPC's footprint (``min(hrrr, spc)``), so quiet
    hours stay quiet and no risk is drawn where SPC drew none, but the model is
    never inflated. ``weight`` is ignored in this mode.

    In both modes the hazard probabilities (and implied CIG / hazard overlays)
    are clipped to stay consistent with the resulting categories.
    """
    normalized_mode = str(mode).strip().lower()
    weight = min(1.0, max(0.0, float(weight)))
    hrrr_grid = np.asarray(hrrr_category_grid, dtype=np.int16)

    if normalized_mode == "blend" and weight <= 0.0:
        return {
            "category_grid": hrrr_grid,
            "probabilities": dict(hrrr_probabilities),
            "report": {
                "spcSupportApplied": False,
                "spcSupportWeight": 0.0,
                "spcSupportMode": normalized_mode,
            },
        }

    lat_grid, lon_grid = _lat_lon_grid(lats, lons, hrrr_grid.shape)
    spc_grid = np.asarray(
        official_category_grid(lat_grid, lon_grid, spc_geojson),
        dtype=np.int16,
    )

    if normalized_mode == "ceiling":
        # SPC as a per-day envelope: cap to SPC and zero outside its footprint.
        blended_grid = np.minimum(hrrr_grid, spc_grid).astype(np.int16)
        # Keep the HRRR hazard gradient but clip it to the capped categories.
        blended_probabilities = {
            hazard: np.clip(np.asarray(values, dtype=float), 0.0, 1.0)
            for hazard, values in hrrr_probabilities.items()
        }
        hazards_blended = False
    else:
        blended_grid = np.rint(
            (1.0 - weight) * hrrr_grid.astype(float) + weight * spc_grid.astype(float)
        )
        blended_grid = np.clip(blended_grid, 0, _MAX_CATEGORY_ORDINAL).astype(np.int16)
        # Blend each HRRR hazard probability toward the SPC-category-implied level
        # for that hazard by the same weight.
        blended_probabilities = {}
        for hazard, values in hrrr_probabilities.items():
            arr = np.clip(np.asarray(values, dtype=float), 0.0, 1.0)
            spc_target = _spc_implied_hazard_probability(hazard, spc_grid)
            blended_probabilities[hazard] = np.clip(
                (1.0 - weight) * arr + weight * spc_target, 0.0, 1.0
            )
        hazards_blended = True

    ceiling = apply_category_probability_ceiling(blended_probabilities, blended_grid)

    report = {
        "spcSupportApplied": True,
        "spcSupportMode": normalized_mode,
        "spcSupportWeight": weight if normalized_mode == "blend" else None,
        "fullyAnchored": normalized_mode == "blend" and weight >= 1.0,
        "hazardProbabilitiesBlended": hazards_blended,
        "spcCategoryCells": int(np.sum(spc_grid > 0)),
        "hrrrCategoryCells": int(np.sum(hrrr_grid > 0)),
        "blendedCategoryCells": int(np.sum(blended_grid > 0)),
        "raisedTowardSpcCells": int(np.sum(blended_grid > hrrr_grid)),
        "loweredTowardSpcCells": int(np.sum(blended_grid < hrrr_grid)),
        "categoryConsistency": ceiling.report,
    }
    return {
        "category_grid": blended_grid,
        "probabilities": ceiling.probabilities,
        "report": report,
    }



SpcFetchFn = Callable[[requests.Session, Path | None], dict[str, Any]]
MERGED_D1_AVAILABLE_DAY_COUNT = 2


def spc_d1_window(
    spc_geojson: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Extract the D1 valid window from SPC GeoJSON, or default to today 12Z–tomorrow 12Z."""
    if spc_geojson:
        for feature in spc_geojson.get("features", []):
            props = feature.get("properties", {})
            valid = _parse_iso(props.get("VALID_ISO"))
            expire = _parse_iso(props.get("EXPIRE_ISO"))
            if valid is not None and expire is not None:
                return valid, expire
    now = now or datetime.now(timezone.utc)
    today_12z = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now.hour < 12:
        today_12z -= timedelta(days=1)
    return today_12z, today_12z + timedelta(days=1)


def resolve_merge_cycle_dirs(
    artifact_root: Path,
    now: datetime | None = None,
) -> list[Path]:
    """Find completed incremental cycle directories sorted by cycle time (newest first).

    Scans `artifact_root` for directories containing an `index.json` with
    ``status`` equal to ``"complete"`` and a parseable ``cycleTimeISO``.
    """
    now = now or datetime.now(timezone.utc)
    candidates: list[tuple[datetime, Path]] = []
    if not artifact_root.exists():
        return []
    for child in artifact_root.iterdir():
        if not child.is_dir():
            continue
        index = _read_index(child)
        if not isinstance(index, dict):
            continue
        status = index.get("status")
        if status not in ("complete", "partial"):
            continue
        ready = index.get("readyForecastHours")
        if not isinstance(ready, list) or len(ready) < 4:
            continue
        cycle_time = _parse_iso(index.get("cycleTimeISO"))
        if cycle_time is None:
            continue
        candidates.append((cycle_time, child))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates]


def resolve_cycle_dirs_for_window(
    artifact_root: Path,
    d1_valid: datetime,
    d1_expire: datetime,
    model: str = "hrrr",
) -> list[Path]:
    """Find completed cycle directories that contribute forecast hours inside the target window.

    Filters by the requested model (case-insensitive).
    """
    candidates: list[tuple[datetime, Path]] = []
    if not artifact_root.exists():
        return []
    for child in artifact_root.iterdir():
        if not child.is_dir():
            continue
        index = _read_index(child)
        if not isinstance(index, dict):
            continue
        status = index.get("status")
        if status not in ("complete", "partial"):
            continue
        ready = index.get("readyForecastHours")
        if not isinstance(ready, list) or not ready:
            continue
        cycle_time = _parse_iso(index.get("cycleTimeISO"))
        if cycle_time is None:
            continue

        # Check if the cycle matches the requested model
        cycle_policy = index.get("cyclePolicy") or {}
        cycle_model = cycle_policy.get("model") or index.get("model", {}).get("name") or "HRRR"
        if str(cycle_model).lower() != model.lower():
            continue

        # Check if any ready forecast hour falls into the target window
        has_overlap = False
        for forecast_hour in ready:
            valid_time = cycle_time + timedelta(hours=int(forecast_hour))
            if d1_valid <= valid_time < d1_expire:
                has_overlap = True
                break
        if has_overlap:
            candidates.append((cycle_time, child))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates]


def available_merged_d1_dates(
    artifact_root: Path,
    model: str = "hrrr",
    *,
    day_count: int = MERGED_D1_AVAILABLE_DAY_COUNT,
) -> list[str]:
    """Return visible merged-D1 dates that map to same-day forecast cycles."""
    if day_count <= 0:
        return []
    candidates = _merged_d1_cycle_candidates(artifact_root, model)
    dates: list[str] = []
    for cycle_date in sorted({cycle_time.date() for cycle_time, _path, _index in candidates}, reverse=True):
        if _preferred_merged_d1_cycle_for_date(candidates, cycle_date) is None:
            continue
        dates.append(cycle_date.isoformat())
        if len(dates) >= day_count:
            break
    return dates


def resolve_cycle_dirs_for_merged_d1_date(
    artifact_root: Path,
    target_date: date,
    model: str = "hrrr",
    *,
    day_count: int = MERGED_D1_AVAILABLE_DAY_COUNT,
) -> list[Path]:
    """Resolve the same-day run used by the public merged-D1 date selector."""
    allowed = set(available_merged_d1_dates(artifact_root, model, day_count=day_count))
    if target_date.isoformat() not in allowed:
        return []
    selected = _preferred_merged_d1_cycle_for_date(
        _merged_d1_cycle_candidates(artifact_root, model),
        target_date,
    )
    if selected is None:
        return []
    return [selected[1]]


def fetch_archived_spc_day1_category(
    target_date: Any,
    session: requests.Session | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Fetch historical SPC Day 1 categorical GeoJSON from NOAA archives."""
    return fetch_archived_spc_category(target_date, session, output_dir, day=1)


# Archive issue times that commonly appear in the geojson zip filenames, per day.
_SPC_ARCHIVE_RUN_TIMES: dict[int, list[str]] = {
    1: ["1200", "1300", "1630", "2000", "0100"],
    2: ["0600", "1730", "0700"],
}


def fetch_archived_spc_category(
    target_date: Any,
    session: requests.Session | None = None,
    output_dir: Path | None = None,
    day: int = 1,
) -> dict[str, Any]:
    """Fetch historical SPC Day ``day`` (1 or 2) categorical GeoJSON from NOAA archives."""
    from datetime import date
    if day not in (1, 2):
        raise ValueError(f"Unsupported SPC outlook day: {day}")
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-SPC-verifier/1.0")

    product = f"day{day}otlk"
    year = target_date.year
    date_str = target_date.strftime("%Y%m%d")
    run_times = _SPC_ARCHIVE_RUN_TIMES[day]
    zip_url = None
    selected_run_time = None
    category_geojson = None
    selected_ordinal = -1
    last_error = None

    try:
        for run_time in run_times:
            url = f"https://www.spc.noaa.gov/products/outlook/archive/{year}/{product}_{date_str}_{run_time}-geojson.zip"
            try:
                res = session.get(url, timeout=15)
                if res.status_code != 200:
                    continue
                import io
                import zipfile
                with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
                    cat_name = next(
                        name for name in zf.namelist()
                        if name.endswith("_cat.nolyr.geojson") or name.endswith(f"{product}_cat.nolyr.geojson")
                    )
                    candidate_geojson = json.loads(zf.read(cat_name).decode("utf-8"))
                candidate_ordinal = _spc_geojson_max_category_ordinal(candidate_geojson)
                if candidate_ordinal > selected_ordinal:
                    zip_url = url
                    selected_run_time = run_time
                    category_geojson = candidate_geojson
                    selected_ordinal = candidate_ordinal
            except Exception as exc:
                last_error = exc

        if category_geojson is None:
            raise ValueError(
                f"Could not find SPC Day {day} archive zip for date {date_str}. "
                f"Tried run times {run_times}. Last error: {last_error}"
            )

        html_url = f"https://www.spc.noaa.gov/products/outlook/archive/{year}/{product}_{date_str}.html"
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"spc_day{day}_cat.geojson").write_text(json.dumps(category_geojson), encoding="utf-8")
            (output_dir / "spc_source.json").write_text(json.dumps({
                "day1Url": html_url,
                "spcDay": day,
                "geojsonZipUrl": zip_url,
                "selectedIssueTimeUTC": selected_run_time,
                "fetchedAtISO": _now_iso(),
            }, indent=2), encoding="utf-8")

        return {
            "day1Url": html_url,
            "spcDay": day,
            "geojsonZipUrl": zip_url,
            "selectedIssueTimeUTC": selected_run_time,
            "fetchedAtISO": _now_iso(),
            "categoryGeojson": category_geojson,
        }
    finally:
        if own_session:
            session.close()


def _spc_geojson_max_category_ordinal(category_geojson: Mapping[str, Any]) -> int:
    category_order = {
        "NONE": 0,
        "TSTM": 1,
        "MRGL": 2,
        "SLGT": 3,
        "ENH": 4,
        "MDT": 5,
        "MOD": 5,
        "HIGH": 6,
    }
    return max(
        (
            category_order.get(
                str(feature.get("properties", {}).get("LABEL") or "").upper(),
                0,
            )
            for feature in category_geojson.get("features", [])
            if isinstance(feature, Mapping)
        ),
        default=0,
    )


def merge_cycles_for_spc_window(
    cycle_dirs: list[Path],
    spc_fetch_fn: SpcFetchFn | None = None,
    session: requests.Session | None = None,
    output_dir: Path | None = None,
    target_date: Any | None = None,
    window_valid: datetime | None = None,
    window_expire: datetime | None = None,
) -> dict[str, Any]:
    """Merge per-hour category grids from multiple cycles and compare to SPC D1.

    Parameters
    ----------
    cycle_dirs:
        List of incremental artifact directories (one per HRRR cycle).
    spc_fetch_fn:
        Optional override for SPC Day 1 fetch.  Defaults to
        ``fetch_current_spc_day1_category``.
    session:
        Optional ``requests.Session`` for network calls.
    output_dir:
        If provided, write ``merged_verification_summary.json`` and
        ``merged_d1_index.json`` here.
    target_date:
        Optional date object or YYYY-MM-DD string to target.
    """
    started = time.perf_counter()
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-merged-outlook/1.0")

    try:
        from datetime import date
        if isinstance(target_date, str):
            target_date = date.fromisoformat(target_date)

        # 1. Determine SPC Day 1 window bounds
        if (window_valid is None) != (window_expire is None):
            raise ValueError("window_valid and window_expire must be provided together")
        window_expire_inclusive = window_valid is not None and window_expire is not None
        if window_valid is not None and window_expire is not None:
            d1_valid = window_valid.astimezone(timezone.utc)
            d1_expire = window_expire.astimezone(timezone.utc)
        elif target_date is not None:
            d1_valid = datetime(target_date.year, target_date.month, target_date.day, 12, 0, 0, tzinfo=timezone.utc)
            d1_expire = d1_valid + timedelta(days=1)
        else:
            d1_valid, d1_expire = None, None

        # 2. Try to find/fetch SPC Day 1 geojson
        spc = None
        spc_geojson = None

        # Try to find a cached spc_day1_cat.geojson in the cycle_dirs
        for cycle_dir in cycle_dirs:
            cached_geojson_path = cycle_dir / "spc_day1_cat.geojson"
            if cached_geojson_path.exists():
                try:
                    geo = json.loads(cached_geojson_path.read_text(encoding="utf-8"))
                    if d1_valid is not None:
                        cv, ce = spc_d1_window(geo)
                        date_matches = target_date is not None and cv.date() == target_date
                        if cv == d1_valid or (window_valid is not None and date_matches):
                            day1_url = (
                                f"https://www.spc.noaa.gov/products/outlook/archive/{target_date.year}/"
                                f"day1otlk_{target_date.strftime('%Y%m%d')}.html"
                                if target_date is not None
                                else None
                            )
                            spc_geojson = geo
                            spc = {
                                "categoryGeojson": geo,
                                "day1Url": day1_url,
                                "fetchedAtISO": _now_iso(),
                            }
                            break
                    else:
                        spc_geojson = geo
                        spc = {
                            "categoryGeojson": geo,
                            "fetchedAtISO": _now_iso(),
                        }
                        break
                except Exception:
                    continue

        # If not found in cache, fetch it
        if spc_geojson is None:
            if target_date is not None:
                now_utc = datetime.now(timezone.utc).date()
                if abs((target_date - now_utc).days) <= 1:
                    try:
                        spc = fetch_archived_spc_day1_category(target_date, session, output_dir)
                    except Exception:
                        spc_fetch = spc_fetch_fn or fetch_current_spc_day1_category
                        spc = spc_fetch(session, output_dir)
                else:
                    spc = fetch_archived_spc_day1_category(target_date, session, output_dir)
            else:
                spc_fetch = spc_fetch_fn or fetch_current_spc_day1_category
                spc = spc_fetch(session, output_dir)

            spc_geojson = spc.get("categoryGeojson")

        if not isinstance(spc_geojson, Mapping):
            raise ValueError("Could not find or fetch a valid SPC Day 1 category GeoJSON")

        if d1_valid is None:
            d1_valid, d1_expire = spc_d1_window(spc_geojson)

        tile_lats: np.ndarray | None = None
        tile_lons: np.ndarray | None = None
        category_grids: list[np.ndarray] = []
        hazard_grids_by_name: dict[str, list[np.ndarray]] = {
            "tornado": [],
            "hail": [],
            "wind": [],
            "thunder": [],
        }
        cig_feature_collections: list[Mapping[str, Any]] = []
        contributing_hours: list[dict[str, Any]] = []
        merged_cycles: list[str] = []
        merged_cycle_time_isos: list[str] = []
        grid_shape: tuple[int, ...] | None = None
        source_tile_stride: int | None = None

        for cycle_dir in cycle_dirs:
            index = _read_index(cycle_dir)
            if not isinstance(index, dict):
                continue
            cycle_label = index.get("cycle", cycle_dir.name)
            cycle_time = _parse_iso(index.get("cycleTimeISO"))
            if cycle_time is None:
                continue
            ready_hours = _int_list(index.get("readyForecastHours"))
            if not ready_hours:
                continue

            cycle_contributed = False
            for forecast_hour in sorted(ready_hours):
                valid_time = cycle_time + timedelta(hours=forecast_hour)
                in_window = (
                    d1_valid <= valid_time <= d1_expire
                    if window_expire_inclusive
                    else d1_valid <= valid_time < d1_expire
                )
                if not in_window:
                    continue
                tile_path = cycle_dir / "hours" / f"f{forecast_hour:02d}" / "probability_tile.json"
                tile = _read_json(tile_path)
                if not isinstance(tile, dict):
                    continue
                try:
                    hour_lats, hour_lons, category_grid = _tile_grid_payload(tile)
                except (ValueError, KeyError):
                    continue
                tile_stride = _positive_int(tile.get("stride"))
                if tile_stride is not None:
                    source_tile_stride = tile_stride if source_tile_stride is None else min(source_tile_stride, tile_stride)

                if grid_shape is None:
                    grid_shape = category_grid.shape
                    tile_lats = hour_lats
                    tile_lons = hour_lons
                elif category_grid.shape != grid_shape:
                    continue

                category_grids.append(category_grid)
                probs = tile.get("probabilities", {})
                for hazard in ("tornado", "hail", "wind", "thunder"):
                    grid_data = probs.get(hazard)
                    if grid_data is not None:
                        h_grid = np.asarray(grid_data, dtype=float)
                        if h_grid.shape == grid_shape:
                            hazard_grids_by_name[hazard].append(h_grid)
                cig_shapes = tile.get("cigShapes")
                if isinstance(cig_shapes, Mapping):
                    cig_feature_collections.append(cig_shapes)

                contributing_hours.append({
                    "cycle": cycle_label,
                    "forecastHour": forecast_hour,
                    "validTimeISO": valid_time.isoformat().replace("+00:00", "Z"),
                })
                cycle_contributed = True

            if cycle_contributed:
                if cycle_label not in merged_cycles:
                    merged_cycles.append(cycle_label)
                cycle_time_iso = cycle_time.isoformat().replace("+00:00", "Z")
                if cycle_time_iso not in merged_cycle_time_isos:
                    merged_cycle_time_isos.append(cycle_time_iso)

        if tile_lats is None or tile_lons is None or not category_grids:
            raise ValueError(
                "No qualifying forecast hours from any cycle fall within the "
                f"SPC D1 window ({d1_valid.isoformat()} – {d1_expire.isoformat()})"
            )

        merged_grid = np.maximum.reduce(category_grids)
        merged_probs = {}
        for hazard, grids in hazard_grids_by_name.items():
            if grids:
                merged_probs[hazard] = np.maximum.reduce(grids)
            else:
                merged_probs[hazard] = np.zeros(grid_shape)

        # Back the merged categorical outlook with the official SPC Day 1 outlook.
        # By default the HRRR/XGBoost guidance drives the outlook but is nudged
        # 25% toward SPC (see _spc_support_weight); hazard probabilities are kept
        # consistent with the resulting risk levels.
        spc_support_report: dict[str, Any] | None = None
        spc_support_weight = _spc_support_weight()
        if spc_support_weight > 0.0:
            blended = blend_merged_outlook_with_spc(
                tile_lats,
                tile_lons,
                merged_grid,
                merged_probs,
                spc_geojson,
                weight=spc_support_weight,
            )
            merged_grid = blended["category_grid"]
            merged_probs = blended["probabilities"]
            spc_support_report = blended["report"]

        # Generate merged GeoJSON risk polygons and hazard shapes
        valid_time_str = d1_valid.isoformat().replace("+00:00", "Z")
        merged_risk_polygons = risk_polygons_from_grid(
            tile_lats,
            tile_lons,
            merged_grid,
            forecast_hour=0,
            valid_time_iso=valid_time_str,
            probabilities=merged_probs,
        )

        merged_hazard_shapes = hazard_probability_shapes_from_grids(
            tile_lats,
            tile_lons,
            merged_probs,
            merged_grid,
            forecast_hour=0,
            valid_time_iso=valid_time_str,
        )
        merged_hazard_shapes = constrain_hazard_probability_shapes_to_risk_support(
            merged_hazard_shapes,
            merged_risk_polygons,
        )
        merged_cig_shapes = _merge_cig_shape_collections(
            cig_feature_collections,
            valid_time_str,
        )

        summary = compare_prediction_to_spc(tile_lats, tile_lons, merged_grid, spc_geojson, None)
        summary["mergedCycles"] = merged_cycles
        summary["mergedCycleTimeISOs"] = merged_cycle_time_isos
        summary["d1WindowValidISO"] = d1_valid.isoformat().replace("+00:00", "Z")
        summary["d1WindowExpireISO"] = d1_expire.isoformat().replace("+00:00", "Z")
        summary["contributingHours"] = contributing_hours
        summary["mergeMethod"] = "maximum"
        summary["spcSupport"] = spc_support_report
        summary["spcDay1Url"] = spc.get("day1Url")
        summary["spcGeojsonZipUrl"] = spc.get("geojsonZipUrl")
        summary["spcFetchedAtISO"] = spc.get("fetchedAtISO")
        summary["spcFetchedAfterPredictionArtifacts"] = True
        summary["generatedAtISO"] = _now_iso()
        summary["latencyMs"] = int((time.perf_counter() - started) * 1000)
        summary["tileStride"] = source_tile_stride

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_json(output_dir / "merged_verification_summary.json", summary)
            _write_json(output_dir / "spc_day1_cat.geojson", spc_geojson)
            _write_json(output_dir / "merged_risk_polygons.geojson", merged_risk_polygons)
            _write_json(output_dir / "merged_hazard_probability_shapes.geojson", merged_hazard_shapes)
            _write_json(output_dir / "merged_cig_shapes.geojson", merged_cig_shapes)

            # Generate the merged probability tile that contains everything
            # (matches structure of OutlookProbabilityTile)
            labels = ["NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
            merged_probability_tile = {
                "forecastHour": 0,
                "validTimeISO": valid_time_str,
                "stride": source_tile_stride or 4,
                "shape": list(merged_grid.shape),
                "lats": tile_lats.tolist(),
                "lons": tile_lons.tolist(),
                "categoryOrdinal": merged_grid.tolist(),
                "categoryLabel": [[labels[int(val)] for val in row] for row in merged_grid],
                "probabilities": {
                    hazard: grid.tolist()
                    for hazard, grid in merged_probs.items()
                },
                "riskShapes": merged_risk_polygons,
                "hazardProbabilityShapes": merged_hazard_shapes,
                "cigShapes": merged_cig_shapes,
            }
            _write_json(output_dir / "merged_probability_tile.json", merged_probability_tile)

            _write_json(output_dir / "merged_d1_index.json", {
                "generatedAtISO": summary["generatedAtISO"],
                "mergedCycles": merged_cycles,
                "mergedCycleTimeISOs": merged_cycle_time_isos,
                "d1WindowValidISO": summary["d1WindowValidISO"],
                "d1WindowExpireISO": summary["d1WindowExpireISO"],
                "contributingHourCount": len(contributing_hours),
                "mergeMethod": "maximum",
                "spcSupportWeight": spc_support_weight,
                "spcDay1Url": spc.get("day1Url"),
                "latencyMs": summary["latencyMs"],
                "tileStride": source_tile_stride,
            })

        return summary
    except Exception as exc:
        error_summary: dict[str, Any] = {
            "error": f"{type(exc).__name__}: {exc}",
            "spcFetchedAfterPredictionArtifacts": True,
            "mergedCycles": [],
            "contributingHours": [],
            "mergeMethod": "maximum",
            "generatedAtISO": _now_iso(),
            "latencyMs": int((time.perf_counter() - started) * 1000),
        }
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_json(output_dir / "merged_verification_summary.json", error_summary)
        raise
    finally:
        if own_session:
            session.close()


def _tile_grid_payload(tile: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extract lats, lons, and category ordinal grid from a probability tile."""
    category_grid = np.asarray(tile.get("categoryOrdinal"), dtype=np.int16)
    lats = np.asarray(tile.get("lats"), dtype=float)
    lons = np.asarray(tile.get("lons"), dtype=float)
    if category_grid.ndim != 2 or lats.shape != category_grid.shape or lons.shape != category_grid.shape:
        raise ValueError("probability tile is missing matching category/lats/lons grids")
    return lats, lons, category_grid


def spc_day_window(valid_dt: datetime) -> tuple[datetime, datetime]:
    """The 12Z-to-12Z SPC convective-day window that contains ``valid_dt``."""
    valid_dt = valid_dt.astimezone(timezone.utc)
    anchor = valid_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    if valid_dt < anchor:
        anchor -= timedelta(days=1)
    return anchor, anchor + timedelta(days=1)


def _spc_geojson_window(geojson: Mapping[str, Any]) -> tuple[datetime, datetime] | None:
    """Return the (valid, expire) window declared in an SPC categorical geojson."""
    if not isinstance(geojson, Mapping):
        return None
    for feature in geojson.get("features", []):
        props = feature.get("properties", {}) if isinstance(feature, Mapping) else {}
        valid = _parse_iso(props.get("VALID_ISO"))
        expire = _parse_iso(props.get("EXPIRE_ISO"))
        if valid is not None and expire is not None:
            return valid, expire
    return None


def select_spc_geojson_for_valid_time(
    spc_geojsons: list[Mapping[str, Any]],
    valid_dt: datetime,
) -> Mapping[str, Any] | None:
    """Pick the SPC categorical geojson whose declared window contains ``valid_dt``.

    This is what lets the hourly scrubber automatically switch between the SPC
    Day 1 and Day 2 outlooks as the scrubbed forecast hour crosses 12Z.
    """
    valid_dt = valid_dt.astimezone(timezone.utc)
    for geojson in spc_geojsons:
        window = _spc_geojson_window(geojson)
        if window is None:
            continue
        valid, expire = window
        if valid <= valid_dt < expire:
            return geojson
    return None


def spc_backed_hour_tile(
    tile: Mapping[str, Any],
    spc_geojsons: list[Mapping[str, Any]],
    *,
    mode: str = "ceiling",
    weight: float = DEFAULT_SPC_SUPPORT_WEIGHT,
    coarsen_max_dim: int | None = 160,
) -> dict[str, Any]:
    """Apply SPC backing to a single hourly probability tile at serve time.

    The tile's valid time selects the SPC day window (Day 1 / Day 2), and the
    category grid + hazard probabilities are re-derived through
    :func:`blend_merged_outlook_with_spc` (default ``mode="ceiling"`` so SPC acts
    as a per-day envelope). Risk polygons and hazard-probability shapes are
    regenerated so the served tile is internally consistent.

    Because re-vectorizing a full-resolution grid per request is slow, the grid
    is coarsened to at most ``coarsen_max_dim`` cells per axis before the
    transform (set to ``None`` to disable). This keeps the interactive scrubber
    responsive at a small cost to polygon fidelity.

    Returns a dict with ``tile`` (possibly unchanged), ``applied`` (bool), and a
    ``report``. The raw on-disk artifact is never modified, preserving the
    post-prediction SPC verification guarantee.
    """
    valid_iso = tile.get("validTimeISO")
    valid_dt = _parse_iso(valid_iso)
    if valid_dt is None:
        return {"tile": dict(tile), "applied": False, "report": {"reason": "tile has no valid time"}}

    spc_geojson = select_spc_geojson_for_valid_time(spc_geojsons, valid_dt)
    if spc_geojson is None:
        return {
            "tile": dict(tile),
            "applied": False,
            "report": {"reason": "no SPC day window covers the tile valid time"},
        }

    try:
        lats, lons, grid = _tile_grid_payload(tile)
    except (ValueError, KeyError):
        return {"tile": dict(tile), "applied": False, "report": {"reason": "tile grid payload invalid"}}

    probabilities = {
        hazard: np.asarray(values, dtype=float)
        for hazard, values in (tile.get("probabilities") or {}).items()
    }

    # Coarsen the grid for a responsive serve-time re-vectorization.
    step = 1
    if coarsen_max_dim and coarsen_max_dim > 0:
        step = max(1, int(np.ceil(max(grid.shape) / int(coarsen_max_dim))))
    if step > 1:
        lats = lats[::step, ::step]
        lons = lons[::step, ::step]
        grid = grid[::step, ::step]
        probabilities = {hazard: values[::step, ::step] for hazard, values in probabilities.items()}

    blended = blend_merged_outlook_with_spc(
        lats, lons, grid, probabilities, spc_geojson, weight=weight, mode=mode
    )
    new_grid = np.asarray(blended["category_grid"], dtype=np.int16)
    new_probs = blended["probabilities"]
    forecast_hour = int(tile.get("forecastHour", 0) or 0)

    risk_polygons = risk_polygons_from_grid(
        lats, lons, new_grid, forecast_hour=forecast_hour,
        valid_time_iso=valid_iso, probabilities=new_probs,
    )
    hazard_shapes = hazard_probability_shapes_from_grids(
        lats, lons, new_probs, new_grid, forecast_hour=forecast_hour, valid_time_iso=valid_iso,
    )
    hazard_shapes = constrain_hazard_probability_shapes_to_risk_support(hazard_shapes, risk_polygons)

    spc_window = _spc_geojson_window(spc_geojson)
    out_tile = dict(tile)
    out_tile["lats"] = lats.tolist()
    out_tile["lons"] = lons.tolist()
    out_tile["categoryOrdinal"] = new_grid.tolist()
    out_tile["categoryLabel"] = [[SPC_RISK_LABELS[int(value)] for value in row] for row in new_grid]
    out_tile["probabilities"] = {hazard: np.asarray(values).tolist() for hazard, values in new_probs.items()}
    out_tile["riskShapes"] = risk_polygons
    out_tile["hazardProbabilityShapes"] = hazard_shapes
    report = dict(blended["report"])
    report["coarsenStep"] = step
    report["spcWindowValidISO"] = spc_window[0].isoformat().replace("+00:00", "Z") if spc_window else None
    report["spcWindowExpireISO"] = spc_window[1].isoformat().replace("+00:00", "Z") if spc_window else None
    out_tile["spcBacking"] = report
    return {"tile": out_tile, "applied": True, "report": report}


def _merge_cig_shape_collections(
    collections: list[Mapping[str, Any]],
    valid_time_iso: str,
) -> dict[str, Any]:
    """Dissolve hourly CIG overlays into one geometry per hazard and level."""
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for collection in collections:
        for feature in collection.get("features", []):
            if not isinstance(feature, Mapping):
                continue
            properties = feature.get("properties")
            geometry = feature.get("geometry")
            if not isinstance(properties, Mapping) or not isinstance(geometry, Mapping):
                continue
            hazard = str(properties.get("hazard") or "").lower()
            try:
                cig = int(properties.get("cig"))
                source_hour = int(properties.get("forecastHour"))
                source_geometry = shape(geometry)
            except (TypeError, ValueError):
                continue
            if hazard not in {"tornado", "hail", "wind"} or cig < 1 or source_geometry.is_empty:
                continue
            group = grouped.setdefault(
                (hazard, cig),
                {
                    "geometries": [],
                    "properties": dict(properties),
                    "sourceForecastHours": set(),
                },
            )
            group["geometries"].append(source_geometry)
            group["sourceForecastHours"].add(source_hour)

    hazard_order = {"tornado": 0, "hail": 1, "wind": 2}
    features: list[dict[str, Any]] = []
    for (hazard, cig), group in sorted(
        grouped.items(),
        key=lambda item: (hazard_order.get(item[0][0], 99), item[0][1]),
    ):
        merged_geometry = unary_union(group["geometries"])
        if not merged_geometry.is_valid:
            merged_geometry = merged_geometry.buffer(0)
        if merged_geometry.is_empty:
            continue
        merged_geometry, corridor_metadata = _generalize_merged_cig_corridor(
            merged_geometry,
            cig,
        )
        if merged_geometry.is_empty:
            continue
        source_hours = sorted(group["sourceForecastHours"])
        properties = group["properties"]
        vectorization = dict(properties.get("vectorization") or {})
        vectorization.update(corridor_metadata)
        features.append({
            "type": "Feature",
            "geometry": mapping(merged_geometry),
            "properties": {
                **properties,
                "hazard": hazard,
                "cig": cig,
                "label": f"{hazard.upper()} CIG{cig}",
                "forecastHour": 0,
                "validTimeISO": valid_time_iso,
                "sourceForecastHours": source_hours,
                "sourceFeatureCount": len(group["geometries"]),
                "vectorization": vectorization,
            },
        })

    features = _spc_cumulative_cig_features(features)
    return {"type": "FeatureCollection", "features": features}


def _spc_cumulative_cig_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build nested SPC-style CIG contours with non-overlapping hatch areas."""
    if not features:
        return features

    from pyproj import Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shapely_transform
    from shapely.ops import unary_union

    to_projected = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    to_lonlat = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True).transform
    grouped: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        hazard = str(feature.get("properties", {}).get("hazard") or "")
        grouped.setdefault(hazard, []).append(feature)

    output: list[dict[str, Any]] = []
    for group_features in grouped.values():
        projected_by_cig: dict[int, Any] = {}
        feature_by_cig: dict[int, dict[str, Any]] = {}
        for feature in group_features:
            try:
                cig = int(feature.get("properties", {}).get("cig"))
                projected = _clean_projected_geometry(
                    shapely_transform(to_projected, shape(feature.get("geometry"))),
                )
            except (TypeError, ValueError):
                continue
            if projected.is_empty:
                continue
            projected_by_cig[cig] = projected
            feature_by_cig[cig] = feature

        cumulative_by_cig: dict[int, Any] = {}
        higher_cumulative = None
        for cig in sorted(projected_by_cig, reverse=True):
            projected = projected_by_cig[cig]
            if higher_cumulative is not None and not higher_cumulative.is_empty:
                projected = _clean_projected_geometry(
                    unary_union([projected, higher_cumulative]),
                )
            if projected.is_empty:
                continue
            cumulative_by_cig[cig] = projected
            higher_cumulative = projected

        ordered_cigs = sorted(cumulative_by_cig)
        for position, cig in enumerate(ordered_cigs):
            cumulative = cumulative_by_cig[cig]
            immediate_higher = (
                cumulative_by_cig[ordered_cigs[position + 1]]
                if position + 1 < len(ordered_cigs)
                else None
            )
            hatch = (
                _clean_projected_geometry(cumulative.difference(immediate_higher))
                if immediate_higher is not None and not immediate_higher.is_empty
                else cumulative
            )
            hatch = _drop_small_projected_parts(hatch, 1.0)
            if hatch.is_empty:
                hatch = cumulative

            feature = feature_by_cig[cig]
            props = dict(feature.get("properties", {}))
            vectorization = dict(props.get("vectorization") or {})
            vectorization.update({
                "displayGeometry": "spc_cumulative_contour",
                "hatchGeometry": "spc_exclusive_hatch",
                "displayProjection": "EPSG:5070",
            })
            props["vectorization"] = vectorization
            props["componentCount"] = _projected_component_count(cumulative)
            props["displayAreaKm2"] = round(float(cumulative.area) / 1_000_000.0, 1)
            props["hatchGeometry"] = mapping(shapely_transform(to_lonlat, hatch))
            output.append({
                **feature,
                "geometry": mapping(shapely_transform(to_lonlat, cumulative)),
                "properties": props,
            })

    hazard_order = {"tornado": 0, "hail": 1, "wind": 2}
    return sorted(
        output,
        key=lambda feature: (
            hazard_order.get(str(feature.get("properties", {}).get("hazard")), 99),
            int(feature.get("properties", {}).get("cig", 0)),
        ),
    )


def _generalize_merged_cig_corridor(geometry: Any, cig: int) -> tuple[Any, dict[str, Any]]:
    """Join nearby hourly CIG areas into a clean merged-outlook corridor."""
    from pyproj import Transformer
    from shapely.ops import transform as shapely_transform

    settings = {
        1: {
            "joinMeters": 55_000.0,
            "smoothMeters": 12_000.0,
            "simplifyMeters": 3_500.0,
            "minimumAreaKm2": 1_500.0,
        },
        2: {
            "joinMeters": 55_000.0,
            "smoothMeters": 10_000.0,
            "simplifyMeters": 3_000.0,
            "minimumAreaKm2": 900.0,
        },
        3: {
            "joinMeters": 45_000.0,
            "smoothMeters": 8_000.0,
            "simplifyMeters": 2_500.0,
            "minimumAreaKm2": 500.0,
        },
    }.get(
        int(cig),
        {
            "joinMeters": 45_000.0,
            "smoothMeters": 8_000.0,
            "simplifyMeters": 2_500.0,
            "minimumAreaKm2": 500.0,
        },
    )
    to_projected = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    to_lonlat = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True).transform
    projected = _clean_projected_geometry(shapely_transform(to_projected, geometry))
    if projected.is_empty:
        return geometry, {}

    join_m = settings["joinMeters"]
    joined = _clean_projected_geometry(
        projected.buffer(join_m, quad_segs=10, join_style=1).buffer(
            -join_m,
            quad_segs=10,
            join_style=1,
        ),
    )
    if not joined.is_empty:
        projected = joined
    projected = _smooth_display_projected_geometry(
        projected,
        smooth_m=settings["smoothMeters"],
        simplify_m=settings["simplifyMeters"],
    )
    projected = _drop_small_projected_parts(
        projected,
        settings["minimumAreaKm2"] * 1_000_000.0,
    )
    if projected.is_empty:
        return geometry, {}

    return shapely_transform(to_lonlat, projected), {
        "mergedCorridorGeometry": "joined_hourly_corridor",
        "mergedCorridorJoinKm": round(join_m / 1_000.0, 1),
        "mergedCorridorSmoothKm": round(settings["smoothMeters"] / 1_000.0, 1),
        "mergedCorridorMinimumAreaKm2": round(settings["minimumAreaKm2"], 1),
        "mergedCorridorProjection": "EPSG:5070",
    }


def _read_index(path: Path) -> dict[str, Any] | None:
    return _read_json(path / "index.json") or _read_json(path / "metadata.json")


def _merged_d1_cycle_candidates(
    artifact_root: Path,
    model: str,
) -> list[tuple[datetime, Path, dict[str, Any]]]:
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    if not artifact_root.exists():
        return candidates

    for child in artifact_root.iterdir():
        if not child.is_dir():
            continue
        index = _read_index(child)
        if not isinstance(index, dict):
            continue
        status = index.get("status")
        if status not in ("complete", "partial"):
            continue
        ready = _int_list(index.get("readyForecastHours"))
        if not ready:
            continue
        cycle_time = _parse_iso(index.get("cycleTimeISO"))
        if cycle_time is None:
            continue
        if not _index_model_matches(index, model):
            continue
        candidates.append((cycle_time, child, index))

    candidates.sort(key=_merged_d1_cycle_rank, reverse=True)
    return candidates


def _preferred_merged_d1_cycle_for_date(
    candidates: list[tuple[datetime, Path, dict[str, Any]]],
    target_date: date,
) -> tuple[datetime, Path, dict[str, Any]] | None:
    same_day = [item for item in candidates if item[0].date() == target_date]
    if not same_day:
        return None
    same_day_00z = [item for item in same_day if item[0].hour == 0]
    preferred = same_day_00z or same_day
    preferred.sort(key=_merged_d1_cycle_rank, reverse=True)
    return preferred[0]


def _select_merged_d1_anchor_cycle(
    artifact_root: Path,
    model: str,
) -> tuple[datetime, Path, dict[str, Any]] | None:
    candidates = _merged_d1_cycle_candidates(artifact_root, model)
    if not candidates:
        return None

    latest_cycle_date = max(cycle_time.date() for cycle_time, _path, _index in candidates)
    return _preferred_merged_d1_cycle_for_date(candidates, latest_cycle_date)


def _merged_d1_cycle_rank(item: tuple[datetime, Path, dict[str, Any]]) -> tuple[Any, ...]:
    cycle_time, path, index = item
    status_rank = 1 if index.get("status") == "complete" else 0
    ready_count = len(_int_list(index.get("readyForecastHours")))
    name = path.name.lower()
    path_rank = 0
    if "complete" in name:
        path_rank += 2
    if name.startswith("latest_incremental"):
        path_rank += 1
    return (cycle_time, status_rank, ready_count, path_rank)


def _index_model_matches(index: Mapping[str, Any], model: str) -> bool:
    cycle_policy = index.get("cyclePolicy") or {}
    cycle_model = cycle_policy.get("model") or index.get("model", {}).get("name") or "HRRR"
    return str(cycle_model).lower() == model.lower()


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
