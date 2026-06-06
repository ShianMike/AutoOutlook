"""Merge multiple HRRR cycle artifacts into a combined D1 outlook for SPC comparison."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import requests

from backend.ml.spc_verification import compare_prediction_to_spc, fetch_current_spc_day1_category
from backend.ml.gridded_outlook import (
    constrain_hazard_probability_shapes_to_risk_support,
    risk_polygons_from_grid,
    hazard_probability_shapes_from_grids,
)



SpcFetchFn = Callable[[requests.Session, Path | None], dict[str, Any]]


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


def fetch_archived_spc_day1_category(
    target_date: Any,
    session: requests.Session | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Fetch historical SPC Day 1 categorical GeoJSON from NOAA archives."""
    from datetime import date
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-SPC-verifier/1.0")

    year = target_date.year
    date_str = target_date.strftime("%Y%m%d")

    # Common issue times in the archive zip filenames.
    # Try 1200, then 1300, 1630, 2000, 0100.
    run_times = ["1200", "1300", "1630", "2000", "0100"]
    zip_url = None
    selected_run_time = None
    category_geojson = None
    selected_ordinal = -1
    last_error = None

    for run_time in run_times:
        url = f"https://www.spc.noaa.gov/products/outlook/archive/{year}/day1otlk_{date_str}_{run_time}-geojson.zip"
        try:
            res = session.get(url, timeout=15)
            if res.status_code != 200:
                continue
            import io
            import zipfile
            with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
                cat_name = next(
                    name for name in zf.namelist()
                    if name.endswith("_cat.nolyr.geojson") or name.endswith("day1otlk_cat.nolyr.geojson")
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
            f"Could not find SPC Day 1 archive zip for date {date_str}. "
            f"Tried run times {run_times}. Last error: {last_error}"
        )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "spc_day1_cat.geojson").write_text(json.dumps(category_geojson), encoding="utf-8")
        (output_dir / "spc_source.json").write_text(json.dumps({
            "day1Url": f"https://www.spc.noaa.gov/products/outlook/archive/{year}/day1otlk_{date_str}.html",
            "geojsonZipUrl": zip_url,
            "selectedIssueTimeUTC": selected_run_time,
            "fetchedAtISO": _now_iso(),
        }, indent=2), encoding="utf-8")

    return {
        "day1Url": f"https://www.spc.noaa.gov/products/outlook/archive/{year}/day1otlk_{date_str}.html",
        "geojsonZipUrl": zip_url,
        "selectedIssueTimeUTC": selected_run_time,
        "fetchedAtISO": _now_iso(),
        "categoryGeojson": category_geojson,
    }


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
        contributing_hours: list[dict[str, Any]] = []
        merged_cycles: list[str] = []
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

                contributing_hours.append({
                    "cycle": cycle_label,
                    "forecastHour": forecast_hour,
                    "validTimeISO": valid_time.isoformat().replace("+00:00", "Z"),
                })
                cycle_contributed = True

            if cycle_contributed:
                merged_cycles.append(cycle_label)

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

        # Generate merged GeoJSON risk polygons and hazard shapes
        valid_time_str = d1_valid.isoformat().replace("+00:00", "Z")
        merged_risk_polygons = risk_polygons_from_grid(
            tile_lats,
            tile_lons,
            merged_grid,
            forecast_hour=0,
            valid_time_iso=valid_time_str,
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

        summary = compare_prediction_to_spc(tile_lats, tile_lons, merged_grid, spc_geojson, None)
        summary["mergedCycles"] = merged_cycles
        summary["d1WindowValidISO"] = d1_valid.isoformat().replace("+00:00", "Z")
        summary["d1WindowExpireISO"] = d1_expire.isoformat().replace("+00:00", "Z")
        summary["contributingHours"] = contributing_hours
        summary["mergeMethod"] = "maximum"
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
            }
            _write_json(output_dir / "merged_probability_tile.json", merged_probability_tile)

            _write_json(output_dir / "merged_d1_index.json", {
                "generatedAtISO": summary["generatedAtISO"],
                "mergedCycles": merged_cycles,
                "d1WindowValidISO": summary["d1WindowValidISO"],
                "d1WindowExpireISO": summary["d1WindowExpireISO"],
                "contributingHourCount": len(contributing_hours),
                "mergeMethod": "maximum",
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


def _read_index(path: Path) -> dict[str, Any] | None:
    return _read_json(path / "index.json") or _read_json(path / "metadata.json")


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
