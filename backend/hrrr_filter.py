"""Direct NOAA NOMADS HRRR GRIB-filter provider.

This mirrors the working approach in the user's Model Forecast app: use the
NOMADS HRRR 2D filter endpoint, then backtrack through earlier cycles when the
latest hourly run does not contain the requested valid time.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import requests

from .grib2 import decode_grib2
from .nomads_pipeline import CONUS_BBOX, NomadsFetchError

log = logging.getLogger(__name__)

FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
DIR_PATTERN = "/hrrr.{date}/conus"
FILE_PATTERN = "hrrr.t{cycle:02d}z.wrfsfcf{fhour:02d}.grib2"

FULL_GRIB_PARAMS = [
    "var_CAPE",
    "var_CIN",
    "var_DPT",
    "var_HLCY",
    "var_PWAT",
    "var_TMP",
    "var_UGRD",
    "var_VGRD",
    "var_HGT",
]
FULL_LEVEL_PARAMS = [
    "lev_surface",
    "lev_2_m_above_ground",
    "lev_1000-0_m_above_ground",
    "lev_3000-0_m_above_ground",
    "lev_90-0_mb_above_ground",
    "lev_180-0_mb_above_ground",
    "lev_255-0_mb_above_ground",
    "lev_entire_atmosphere_%28considered_as_a_single_layer%29",
    "lev_entire_atmosphere",
    "lev_10_m_above_ground",
    "lev_500_mb",
]
FOCUS_GRIB_PARAMS = ["var_CAPE", "var_CIN", "var_DPT", "var_UGRD", "var_VGRD"]
FOCUS_LEVEL_PARAMS = ["lev_surface", "lev_2_m_above_ground", "lev_10_m_above_ground", "lev_500_mb"]
OVERLAY_500_GRIB_PARAMS = ["var_HGT", "var_UGRD", "var_VGRD"]
OVERLAY_500_LEVEL_PARAMS = ["lev_500_mb"]

MAX_CELLS = 90_000
MAX_SIDE = 380
CACHE_TTL = 1800
DEFAULT_OVERLAY_500_CACHE_DIR = Path(__file__).resolve().parent / "cache" / "hrrr_overlay_500mb"
OVERLAY_500_FIELD_KEYS = ("hgt500", "u500", "v500")

_session = requests.Session()
_session.headers["User-Agent"] = "AutoOutlook/1.0"
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def fetch_hrrr_grib_valid_time(
    target_dt: datetime,
    bbox: dict[str, float] | None = None,
    profile: str = "full",
) -> dict[str, Any]:
    """Fetch one HRRR hour for a requested valid time.

    Returns lats/lons plus fields needed by the AutoOutlook bundle builder.
    The selected model run is the newest available HRRR cycle that covers
    target_dt, so extended hours use the latest 00/06/12/18Z long run instead
    of freezing at the latest short hourly run.
    """
    if bbox is None:
        bbox = CONUS_BBOX
    if profile not in ("full", "focus"):
        raise ValueError(f"Unsupported HRRR GRIB profile: {profile}")
    target_dt = _floor_hour(target_dt)
    fbbox = _filter_bbox(bbox)
    key = _cache_key(target_dt, fbbox, profile)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    errors: list[str] = []
    for run_date, run_cycle, fhour in _candidate_runs_for_valid_time(target_dt):
        url = _build_url(run_date, run_cycle, fhour, fbbox, profile)
        try:
            grib_bytes = _download_grib(url)
            messages = decode_grib2(grib_bytes)
            lats, lons, fields = _messages_to_fields(messages)
            lats, lons, fields = _crop_and_thin(lats, lons, fields, fbbox)
            cycle_dt = datetime.strptime(f"{run_date}{run_cycle:02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
            valid_dt = cycle_dt + timedelta(hours=fhour)
            result = {
                "model": "hrrr",
                "source": "nomads_grib_filter",
                "profile": profile,
                "run": f"{run_date}/{run_cycle:02d}z",
                "runDate": run_date,
                "runCycle": run_cycle,
                "modelForecastHour": fhour,
                "validTimeISO": valid_dt.isoformat().replace("+00:00", "Z"),
                "lats": lats,
                "lons": lons,
                "fields": fields,
            }
            _cache_set(key, result)
            return result
        except FileNotFoundError as exc:
            errors.append(f"{run_date}/{run_cycle:02d}z F{fhour:02d}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{run_date}/{run_cycle:02d}z F{fhour:02d}: {type(exc).__name__}: {exc}")

    detail = "; ".join(errors[-4:]) if errors else "no candidate HRRR run covers target valid time"
    raise NomadsFetchError(f"HRRR GRIB filter unavailable for {target_dt.isoformat()}: {detail}")


def fetch_hrrr_500mb_overlay_valid_time(
    target_dt: datetime,
    grid_stride: int = 4,
    cache_dir: Path | str | None = DEFAULT_OVERLAY_500_CACHE_DIR,
    cache_ttl: int = CACHE_TTL,
    no_cache: bool = False,
) -> dict[str, Any]:
    """Fetch real full-CONUS HRRR 500 mb HGT/U/V fields for map overlays."""
    target_dt = _floor_hour(target_dt)
    fbbox = _filter_bbox(CONUS_BBOX)
    grid_stride = max(1, int(grid_stride))
    errors: list[str] = []

    for run_date, run_cycle, fhour in _candidate_runs_for_valid_time(target_dt):
        cache_path = _overlay_cache_path(run_date, run_cycle, fhour, cache_dir)
        if not no_cache and cache_path is not None:
            cached = _load_overlay_cache(cache_path, cache_ttl)
            if cached is not None:
                result = {
                    **cached,
                    "cacheHit": True,
                    "cachePath": str(cache_path),
                }
                return result

        url = _build_url(run_date, run_cycle, fhour, fbbox, "overlay500")
        try:
            grib_bytes = _download_grib(url)
            messages = decode_grib2(grib_bytes)
            lats, lons, fields = _messages_to_fields(messages, require_cape=False)
            missing = [key for key in OVERLAY_500_FIELD_KEYS if key not in fields]
            if missing:
                raise ValueError("HRRR overlay payload missing fields: " + ", ".join(missing))
            lats, lons, fields = _crop_and_stride(lats, lons, fields, fbbox, grid_stride)
            cycle_dt = datetime.strptime(f"{run_date}{run_cycle:02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
            valid_dt = cycle_dt + timedelta(hours=fhour)
            result = {
                "model": "hrrr",
                "source": "HRRR",
                "profile": "overlay500",
                "domain": "CONUS",
                "level": "500mb",
                "run": f"{run_date}/{run_cycle:02d}z",
                "runDate": run_date,
                "runCycle": run_cycle,
                "modelForecastHour": fhour,
                "validTimeISO": valid_dt.isoformat().replace("+00:00", "Z"),
                "gridStride": grid_stride,
                "lats": lats,
                "lons": lons,
                "fields": {key: fields[key] for key in OVERLAY_500_FIELD_KEYS},
                "cacheHit": False,
                "cachePath": str(cache_path) if cache_path else None,
            }
            if cache_path is not None and not no_cache:
                _save_overlay_cache(cache_path, result)
            return result
        except FileNotFoundError as exc:
            errors.append(f"{run_date}/{run_cycle:02d}z F{fhour:02d}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{run_date}/{run_cycle:02d}z F{fhour:02d}: {type(exc).__name__}: {exc}")

    detail = "; ".join(errors[-4:]) if errors else "no candidate HRRR run covers target valid time"
    raise NomadsFetchError(f"Full-CONUS HRRR 500 mb overlay unavailable for {target_dt.isoformat()}: {detail}")


def _floor_hour(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _filter_bbox(bbox: dict[str, float]) -> dict[str, float]:
    if "north" in bbox:
        return {
            "lat_min": float(bbox["south"]),
            "lat_max": float(bbox["north"]),
            "lon_min": float(bbox["west"]),
            "lon_max": float(bbox["east"]),
        }
    return {
        "lat_min": float(bbox["lat_min"]),
        "lat_max": float(bbox["lat_max"]),
        "lon_min": float(bbox["lon_min"]),
        "lon_max": float(bbox["lon_max"]),
    }


def _cache_key(target_dt: datetime, bbox: dict[str, float], profile: str) -> str:
    return (
        f"{profile}:{target_dt.isoformat()}:{bbox['lat_min']:.2f}:{bbox['lat_max']:.2f}:"
        f"{bbox['lon_min']:.2f}:{bbox['lon_max']:.2f}"
    )


def _cache_get(key: str) -> dict[str, Any] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry[0]) < CACHE_TTL:
            return entry[1]
        if entry:
            del _cache[key]
    return None


def _cache_set(key: str, value: dict[str, Any]) -> None:
    with _cache_lock:
        if len(_cache) > 80:
            now = time.time()
            for old_key, (ts, _) in list(_cache.items()):
                if now - ts > CACHE_TTL:
                    del _cache[old_key]
        _cache[key] = (time.time(), value)


def _candidate_runs_for_valid_time(target_dt: datetime):
    now = datetime.now(timezone.utc)
    cursor = min(_floor_hour(now - timedelta(hours=1)), target_dt)
    for _ in range(72):
        max_hour = 48 if cursor.hour % 6 == 0 else 18
        fhour_float = (target_dt - cursor).total_seconds() / 3600.0
        fhour = int(round(fhour_float))
        if abs(fhour_float - fhour) < 0.01 and 0 <= fhour <= max_hour:
            yield cursor.strftime("%Y%m%d"), cursor.hour, fhour
        cursor -= timedelta(hours=1)


def _build_url(run_date: str, run_cycle: int, fhour: int, bbox: dict[str, float], profile: str) -> str:
    parts = [
        FILTER_URL,
        "?dir=", DIR_PATTERN.format(date=run_date),
        "&file=", FILE_PATTERN.format(cycle=run_cycle, fhour=fhour),
    ]
    if profile == "full":
        grib_params = FULL_GRIB_PARAMS
        level_params = FULL_LEVEL_PARAMS
    elif profile == "focus":
        grib_params = FOCUS_GRIB_PARAMS
        level_params = FOCUS_LEVEL_PARAMS
    elif profile == "overlay500":
        grib_params = OVERLAY_500_GRIB_PARAMS
        level_params = OVERLAY_500_LEVEL_PARAMS
    else:
        raise ValueError(f"Unsupported HRRR GRIB profile: {profile}")
    for param in grib_params:
        parts.append(f"&{param}=on")
    for param in level_params:
        parts.append(f"&{param}=on")
    parts.extend([
        "&subregion=",
        f"&toplat={bbox['lat_max']}",
        f"&bottomlat={bbox['lat_min']}",
        f"&leftlon={bbox['lon_min']}",
        f"&rightlon={bbox['lon_max']}",
    ])
    return "".join(parts)


def _download_grib(url: str, retries: int = 2) -> bytes:
    transient = {429, 500, 502, 503, 504}
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, timeout=45)
            if resp.status_code == 404:
                raise FileNotFoundError("NOMADS HRRR file not available")
            if resp.status_code == 403:
                raise PermissionError(f"NOMADS returned 403 for {url}")
            if resp.status_code in transient and attempt < retries:
                continue
            resp.raise_for_status()
            if len(resp.content) < 50 or resp.content[:4] != b"GRIB":
                raise ValueError("Invalid GRIB response from NOMADS")
            return resp.content
        except (FileNotFoundError, PermissionError):
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries:
                raise
    raise last_exc or RuntimeError("NOMADS GRIB download failed")


def _messages_to_fields(messages: list[dict[str, Any]], require_cape: bool = True) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    if not messages:
        raise ValueError("No GRIB messages decoded")

    lats = np.asarray(messages[0]["lats"], dtype=float)
    lons = _normalize_lons(np.asarray(messages[0]["lons"], dtype=float))
    fields: dict[str, np.ndarray] = {}

    for msg in messages:
        cat = msg.get("category")
        param = msg.get("parameter")
        vals = np.asarray(msg.get("values"), dtype=float)
        if vals.size == 0:
            continue

        if cat == 7 and param == 6 and _is_surface(msg):
            fields["cape"] = vals
        elif cat == 7 and param == 7 and _is_surface(msg):
            fields["cin"] = -np.abs(vals)
        elif cat == 7 and param == 6 and _is_pressure_depth_agl(msg, 18000.0):
            fields["cape_ml"] = vals
        elif cat == 7 and param == 7 and _is_pressure_depth_agl(msg, 18000.0):
            fields["cin_ml"] = -np.abs(vals)
        elif cat == 7 and param == 6 and _is_pressure_depth_agl(msg, 25500.0):
            fields["cape_mu"] = vals
        elif cat == 7 and param == 7 and _is_pressure_depth_agl(msg, 25500.0):
            fields["cin_mu"] = -np.abs(vals)
        elif cat == 7 and param == 6 and _is_pressure_depth_agl(msg, 9000.0):
            fields["cape_90"] = vals
        elif cat == 7 and param == 7 and _is_pressure_depth_agl(msg, 9000.0):
            fields["cin_90"] = -np.abs(vals)
        elif cat == 1 and param == 3:
            fields["pwat"] = vals
        elif cat == 0 and param == 0 and _is_height_agl(msg, 2.0):
            fields["t2m"] = vals
        elif cat == 0 and param == 6 and _is_height_agl(msg, 2.0):
            fields["td2m"] = vals
        elif cat == 2 and param == 2 and _is_height_agl(msg, 10.0):
            fields["u10"] = vals
        elif cat == 2 and param == 3 and _is_height_agl(msg, 10.0):
            fields["v10"] = vals
        elif cat == 2 and param == 2 and _is_pressure(msg, 50000.0):
            fields["u500"] = vals
        elif cat == 2 and param == 3 and _is_pressure(msg, 50000.0):
            fields["v500"] = vals
        elif cat == 3 and param == 5 and _is_pressure(msg, 50000.0):
            fields["hgt500"] = vals
        elif cat == 7 and param == 8 and _is_height_agl(msg, 1000.0):
            fields["srh01"] = np.clip(vals, 0.0, None)
        elif cat == 7 and param == 8 and _is_height_agl(msg, 3000.0):
            fields["srh03"] = np.clip(vals, 0.0, None)

    if require_cape and "cape" not in fields:
        raise ValueError("HRRR GRIB payload did not contain surface CAPE")
    return lats, lons, fields


def _normalize_lons(lons: np.ndarray) -> np.ndarray:
    out = np.where(lons > 180, lons - 360, lons)
    out = np.where(out < -180, out + 360, out)
    return out


def _is_surface(msg: dict[str, Any]) -> bool:
    return msg.get("level_type") in (1, None)


def _is_height_agl(msg: dict[str, Any], target_m: float) -> bool:
    if msg.get("level_type") != 103:
        return False
    value = msg.get("level_value")
    return value is not None and abs(float(value) - target_m) <= 0.25


def _is_pressure(msg: dict[str, Any], target_pa: float) -> bool:
    if msg.get("level_type") != 100:
        return False
    value = msg.get("level_value")
    if value is None:
        return False
    value = float(value)
    return abs(value - target_pa) <= 1000.0 or abs(value - target_pa / 100.0) <= 1.0


def _is_pressure_depth_agl(msg: dict[str, Any], target_pa: float) -> bool:
    if msg.get("level_type") != 108:
        return False
    value = msg.get("level_value")
    if value is None:
        return False
    return abs(float(value) - target_pa) <= 1.0


def _crop_and_thin(
    lats: np.ndarray,
    lons: np.ndarray,
    fields: dict[str, np.ndarray],
    bbox: dict[str, float],
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    row_idx = _coord_indices(lats, bbox["lat_min"], bbox["lat_max"])
    col_idx = _coord_indices(lons, bbox["lon_min"], bbox["lon_max"])

    total_cells = max(1, row_idx.size * col_idx.size)
    stride = 1
    if total_cells > MAX_CELLS:
        stride = max(stride, math.ceil(math.sqrt(total_cells / MAX_CELLS)))
    if row_idx.size > MAX_SIDE:
        stride = max(stride, math.ceil(row_idx.size / MAX_SIDE))
    if col_idx.size > MAX_SIDE:
        stride = max(stride, math.ceil(col_idx.size / MAX_SIDE))
    row_idx = _thin_indices(row_idx, stride)
    col_idx = _thin_indices(col_idx, stride)

    lats_out = lats[row_idx]
    lons_out = lons[col_idx]
    fields_out = {
        key: np.asarray(value)[np.ix_(row_idx, col_idx)]
        for key, value in fields.items()
        if np.asarray(value).ndim == 2
    }
    return lats_out, lons_out, fields_out


def _crop_and_stride(
    lats: np.ndarray,
    lons: np.ndarray,
    fields: dict[str, np.ndarray],
    bbox: dict[str, float],
    stride: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    row_idx = _coord_indices(lats, bbox["lat_min"], bbox["lat_max"])
    col_idx = _coord_indices(lons, bbox["lon_min"], bbox["lon_max"])
    row_idx = _thin_indices(row_idx, max(1, int(stride)))
    col_idx = _thin_indices(col_idx, max(1, int(stride)))
    lats_out = np.asarray(lats, dtype=float)[row_idx]
    lons_out = np.asarray(lons, dtype=float)[col_idx]
    fields_out = {
        key: np.asarray(value, dtype=float)[np.ix_(row_idx, col_idx)]
        for key, value in fields.items()
        if np.asarray(value).ndim == 2
    }
    return lats_out, lons_out, fields_out


def _overlay_cache_path(run_date: str, run_cycle: int, fhour: int, cache_dir: Path | str | None) -> Path | None:
    if cache_dir is None:
        return None
    return Path(cache_dir) / run_date / f"{run_cycle:02d}" / f"f{fhour:02d}.npz"


def _load_overlay_cache(path: Path, cache_ttl: int) -> dict[str, Any] | None:
    if cache_ttl <= 0 or not path.exists():
        return None
    if time.time() - path.stat().st_mtime > cache_ttl:
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            fields = {key: np.asarray(data[f"field__{key}"], dtype=float) for key in OVERLAY_500_FIELD_KEYS}
            return {
                "model": "hrrr",
                "source": "HRRR",
                "profile": "overlay500",
                "domain": "CONUS",
                "level": "500mb",
                "run": str(data["run"].item()),
                "runDate": str(data["runDate"].item()),
                "runCycle": int(data["runCycle"].item()),
                "modelForecastHour": int(data["modelForecastHour"].item()),
                "validTimeISO": str(data["validTimeISO"].item()),
                "gridStride": int(data["gridStride"].item()),
                "lats": np.asarray(data["lats"], dtype=float),
                "lons": np.asarray(data["lons"], dtype=float),
                "fields": fields,
            }
    except Exception:
        return None


def _save_overlay_cache(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fields = result["fields"]
    payload: dict[str, Any] = {
        "lats": np.asarray(result["lats"], dtype=float),
        "lons": np.asarray(result["lons"], dtype=float),
        "run": np.asarray(str(result.get("run", ""))),
        "runDate": np.asarray(str(result.get("runDate", ""))),
        "runCycle": np.asarray(int(result.get("runCycle", 0))),
        "modelForecastHour": np.asarray(int(result.get("modelForecastHour", 0))),
        "validTimeISO": np.asarray(str(result.get("validTimeISO", ""))),
        "gridStride": np.asarray(int(result.get("gridStride", 1))),
    }
    for key in OVERLAY_500_FIELD_KEYS:
        payload[f"field__{key}"] = np.asarray(fields[key], dtype=float)
    with tmp_path.open("wb") as fh:
        np.savez_compressed(fh, **payload)
    tmp_path.replace(path)


def _coord_indices(coords: np.ndarray, lower: float, upper: float) -> np.ndarray:
    if coords.size == 0:
        return np.asarray([], dtype=int)
    lo = min(lower, upper)
    hi = max(lower, upper)
    idx = np.flatnonzero((coords >= lo) & (coords <= hi))
    if idx.size:
        return idx.astype(int, copy=False)
    nearest = int(np.argmin(np.abs(coords - ((lo + hi) / 2.0))))
    return np.asarray([nearest], dtype=int)


def _thin_indices(indices: np.ndarray, stride: int) -> np.ndarray:
    if indices.size <= 1 or stride <= 1:
        return indices
    sampled = indices[::stride]
    if sampled[-1] != indices[-1]:
        sampled = np.concatenate([sampled, indices[-1:]])
    return sampled.astype(int, copy=False)
