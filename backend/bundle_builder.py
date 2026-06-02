"""Builds the JSON bundle returned by /api/forecast.

Uses NOMADS HRRR GRIB-filter subsets to scan CONUS for the focus region,
then fetches smaller regional HRRR anchor grids and interpolates hourly
ingredients through +48h in the TS frontend's Ingredients shape.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import xarray as xr

from . import metpy_diagnostics as diag
from .hrrr_filter import fetch_hrrr_500mb_overlay_valid_time, fetch_hrrr_grib_valid_time
from .ml.inference import model_status, predict_ml_hazards
from .nomads_pipeline import NomadsFetchError
from .region_picker import _southern_border_multiplier, pick_focus_region

log = logging.getLogger(__name__)

FORECAST_HOURS = list(range(0, 49))
HGT500_CONTOUR_LEVELS = tuple(range(5280, 5941, 60))
FULL_CONUS_OVERLAY_GRID_STRIDE = 4
FULL_CONUS_WIND_BARB_STRIDE = 22
_MATPLOTLIB_CONTOUR_LOCK = threading.Lock()

QUICK_CATEGORY_ORD = {"TSTM": 0, "MRGL": 1, "SLGT": 2, "ENH": 3, "MOD": 4, "HIGH": 5}

SECONDARY_FOCUS_BOXES = [
    ("Pacific Northwest", (40.0, 49.0, -125.0, -116.0)),
    ("Northern Rockies", (42.0, 49.0, -116.0, -104.0)),
    ("Desert Southwest", (31.0, 37.5, -116.0, -103.0)),
    ("Southern Plains", (27.0, 35.0, -104.0, -94.0)),
    ("Central Plains", (33.0, 40.5, -104.0, -94.0)),
    ("Northern Plains", (40.0, 49.0, -104.0, -94.0)),
    ("Midwest", (36.0, 45.0, -95.0, -82.0)),
    ("Southeast", (26.0, 36.0, -91.0, -76.0)),
    ("Northeast", (40.0, 47.0, -80.0, -67.0)),
]

CONUS_CITIES = [
    {"name": "Norman", "lat": 35.22, "lon": -97.44},
    {"name": "Oklahoma City", "lat": 35.47, "lon": -97.52},
    {"name": "Wichita", "lat": 37.69, "lon": -97.34},
    {"name": "Tulsa", "lat": 36.15, "lon": -95.99},
    {"name": "Amarillo", "lat": 35.22, "lon": -101.83},
    {"name": "Dallas", "lat": 32.78, "lon": -96.80},
    {"name": "Topeka", "lat": 39.05, "lon": -95.68},
    {"name": "Springfield", "lat": 37.21, "lon": -93.30},
    {"name": "Lubbock", "lat": 33.58, "lon": -101.85},
    {"name": "Joplin", "lat": 37.08, "lon": -94.51},
    {"name": "Memphis", "lat": 35.15, "lon": -90.05},
    {"name": "Little Rock", "lat": 34.74, "lon": -92.29},
    {"name": "St. Louis", "lat": 38.63, "lon": -90.20},
    {"name": "Nashville", "lat": 36.16, "lon": -86.78},
    {"name": "Atlanta", "lat": 33.75, "lon": -84.39},
    {"name": "Birmingham", "lat": 33.52, "lon": -86.81},
    {"name": "Chicago", "lat": 41.88, "lon": -87.63},
    {"name": "Indianapolis", "lat": 39.77, "lon": -86.16},
    {"name": "Omaha", "lat": 41.26, "lon": -95.93},
    {"name": "Denver", "lat": 39.74, "lon": -104.99},
    {"name": "Minneapolis", "lat": 44.98, "lon": -93.27},
]


def _detect_dims(ds: xr.Dataset) -> dict[str, str]:
    """Return canonical names for the dims we care about."""
    out: dict[str, str] = {}
    if "time" in ds.dims and ds.sizes.get("time", 0) > 1:
        out["validtime"] = "time"

    # validtime - the forecast-time axis (size > 1, name starts with 'validtime')
    if "validtime" not in out:
        for c in ds.coords:
            n = str(c).lower()
            if (
                n.startswith("validtime")
                and not n.endswith("forecast")
                and ds.coords[c].size > 1
            ):
                out["validtime"] = c
                break
    if "validtime" not in out:
        # fallback: any dim of size > 1 not in (reftime, latitude, longitude, isobaric, height_above_ground*)
        for d, sz in ds.sizes.items():
            ld = d.lower()
            if (
                sz > 1
                and ld not in ("latitude", "longitude", "bounds_dim")
                and not ld.startswith("isobaric")
                and not ld.startswith("height")
                and ld != "reftime"
            ):
                out["validtime"] = d
                break
    out["latitude"] = next(
        (c for c in ds.coords if str(c).lower() in ("latitude", "lat", "y")), "latitude"
    )
    out["longitude"] = next(
        (c for c in ds.coords if str(c).lower() in ("longitude", "lon", "x")),
        "longitude",
    )
    return out


def _grid_latlon(ds: xr.Dataset, dims: dict[str, str]) -> tuple[np.ndarray, np.ndarray]:
    """Return geographic lat/lon arrays, deriving them for HRRR Lambert grids."""
    lat_name = dims["latitude"]
    lon_name = dims["longitude"]
    lat_vals = ds.coords[lat_name].values
    lon_vals = ds.coords[lon_name].values

    if np.nanmin(lat_vals) >= -90 and np.nanmax(lat_vals) <= 90:
        return lat_vals, np.where(lon_vals > 180, lon_vals - 360, lon_vals)

    grid_mapping = next(
        (ds[v] for v in ds.data_vars if "grid_mapping_name" in ds[v].attrs),
        None,
    )
    if grid_mapping is None:
        return lat_vals, lon_vals

    try:
        from pyproj import CRS, Transformer
    except Exception:
        return lat_vals, lon_vals

    attrs = grid_mapping.attrs
    lon_0 = float(attrs.get("longitude_of_central_meridian", -95.0))
    if lon_0 > 180:
        lon_0 -= 360
    lat_0 = float(attrs.get("latitude_of_projection_origin", 25.0))
    std_parallel = attrs.get("standard_parallel", 25.0)
    if isinstance(std_parallel, (list, tuple, np.ndarray)):
        lat_1 = float(std_parallel[0])
    else:
        lat_1 = float(std_parallel)
    earth_radius = float(attrs.get("earth_radius", 6371229.0))
    crs = CRS.from_proj4(
        f"+proj=lcc +lat_1={lat_1} +lat_0={lat_0} +lon_0={lon_0} +R={earth_radius} +units=m +no_defs"
    )
    transformer = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    x = np.asarray(lon_vals, dtype=float) * 1000.0
    y = np.asarray(lat_vals, dtype=float) * 1000.0
    xg, yg = np.meshgrid(x, y)
    lons, lats = transformer.transform(xg, yg)
    return np.asarray(lats), np.asarray(lons)


def _nearest_grid_index(
    lats: np.ndarray, lons: np.ndarray, lat: float, lon: float
) -> tuple[int, int]:
    if lats.ndim == 1 and lons.ndim == 1:
        return int(np.argmin(np.abs(lats - lat))), int(np.argmin(np.abs(lons - lon)))
    dist = (lats - lat) ** 2 + (lons - lon) ** 2
    idx = np.unravel_index(int(np.nanargmin(dist)), dist.shape)
    return int(idx[0]), int(idx[1])


def _normalize(ds: xr.Dataset) -> xr.Dataset:
    """Squeeze the singletons we don't want without renaming dims."""
    if "reftime" in ds.coords or "reftime" in ds.dims:
        try:
            ds = ds.isel(reftime=0, drop=True)
        except Exception:
            pass

    for var in list(ds.data_vars):
        da = ds[var]
        for d in list(da.dims):
            if not d.lower().startswith("height_above_ground"):
                continue
            coord_vals = ds.coords[d].values if d in ds.coords else None
            if var in ("td2m", "t2m"):
                target = 2.0
            elif var in ("u10", "v10"):
                target = 10.0
            else:
                target = None
            if coord_vals is not None and coord_vals.size > 1 and target is not None:
                idx = int(np.argmin(np.abs(coord_vals - target)))
                da = da.isel({d: idx}, drop=True)
            else:
                da = da.isel({d: 0}, drop=True)
        ds[var] = da
    return ds


def _hour_indices(
    ds: xr.Dataset, dims: dict[str, str], base_time: datetime, hours: list[int]
) -> list[int]:
    vt_name = dims.get("validtime")
    if vt_name is None or vt_name not in ds.coords:
        n = ds.sizes.get(vt_name or "validtime", len(hours))
        return [min(i, n - 1) for i in range(len(hours))]
    vt = ds.coords[vt_name].values
    out = []
    for h in hours:
        target = np.datetime64(
            int((base_time + timedelta(hours=h)).timestamp() * 1e9), "ns"
        )
        out.append(int(np.argmin(np.abs(vt - target))))
    return out


def _max_available_hour(
    ds: xr.Dataset, dims: dict[str, str], base_time: datetime
) -> float:
    vt_name = dims.get("validtime")
    if vt_name is None or vt_name not in ds.coords:
        return max(FORECAST_HOURS)
    vt = ds.coords[vt_name].values
    if len(vt) == 0:
        return max(FORECAST_HOURS)
    max_ts = np.max(vt).astype("datetime64[ns]").astype("int64") / 1e9
    return max(0.0, (max_ts - base_time.timestamp()) / 3600.0)


def _advect_region(
    region: dict[str, Any], hours_beyond: float, shear_kt: float, mode: str
) -> dict[str, Any]:
    if hours_beyond <= 0:
        return region
    speed = float(np.clip(shear_kt / 45.0, 0.4, 1.4))
    mode_turn = 0.06 if mode == "linear" else 0.10 if mode == "discrete" else 0.08
    dlon = hours_beyond * 0.12 * speed
    dlat = hours_beyond * mode_turn * speed
    center_lat = float(np.clip(region["centerLat"] + dlat, 25.0, 49.0))
    center_lon = float(np.clip(region["centerLon"] + dlon, -125.0, -66.0))
    return {
        **region,
        "label": f"{region['label']} (advected)",
        "centerLat": center_lat,
        "centerLon": center_lon,
        "bbox": [center_lon - 5, center_lat - 3, center_lon + 5, center_lat + 3],
    }


def _classify_signal(value: float, thresholds: tuple[float, float, float]) -> str:
    s_weak, s_mod, s_strong = thresholds
    if value >= s_strong:
        return "strong"
    if value >= s_mod:
        return "moderate"
    if value >= s_weak:
        return "weak"
    return "none"


def _classify_cap(cin: float) -> str:
    a = abs(cin)
    if a >= 150:
        return "strong"
    if a >= 50:
        return "moderate"
    if a >= 15:
        return "weak"
    return "none"


def _classify_storm_mode(
    shear_kt: float,
    srh03: float,
    front: str,
    boundary_kind: str | None = None,
) -> str:
    if shear_kt < 25:
        return "multicell"
    supercell_signal = shear_kt >= 40 and srh03 >= 150
    if boundary_kind in {"dryline", "triple-point"} and supercell_signal:
        return "discrete"
    if front == "strong" and boundary_kind == "frontal" and supercell_signal:
        return "mixed"
    if front == "strong" and shear_kt < 45 and srh03 < 200:
        return "linear"
    if supercell_signal:
        return "discrete"
    return "mixed"


def _initiation_confidence(front: str, cin: float, cape: float, td_f: float) -> float:
    forcing_factor = {
        "strong": 0.90,
        "moderate": 0.60,
        "weak": 0.30,
        "none": 0.08,
    }.get(front, 0.08)
    cap_relief = np.clip((200.0 + cin) / 150.0, 0.0, 1.0)
    moisture_factor = np.clip((td_f - 50.0) / 18.0, 0.0, 1.0)
    cape_floor = np.clip(cape / 1000.0, 0.0, 1.0)
    return float(
        np.clip(
            0.50 * forcing_factor
            + 0.30 * cap_relief
            + 0.10 * moisture_factor
            + 0.10 * cape_floor,
            0.0,
            1.0,
        )
    )


def _ingredients_at_point(
    surface_cape: float,
    mlcape: float,
    mucape: float,
    surface_cin: float,
    mlcin: float,
    mucin: float,
    cape3km: float,
    cape180: float,
    cin180: float,
    td2m_K: float,
    t2m_K: float,
    pwat_kg_m2: float,
    shear_kt: float,
    srh01: float,
    srh03: float,
    sr_wind_kt: float,
    composites: dict[str, float],
    front_override: str | None = None,
    boundary_kind: str | None = None,
) -> dict[str, Any]:
    sbcape = max(0.0, surface_cape) if np.isfinite(surface_cape) else 0.0
    mlcape = max(0.0, mlcape) if np.isfinite(mlcape) else sbcape * 0.85
    mucape = max(0.0, mucape) if np.isfinite(mucape) else max(sbcape, mlcape)
    surface_cin = min(0.0, surface_cin) if np.isfinite(surface_cin) else 0.0
    mlcin = min(0.0, mlcin) if np.isfinite(mlcin) else surface_cin
    mucin = min(0.0, mucin) if np.isfinite(mucin) else mlcin
    cape3km = max(0.0, cape3km) if np.isfinite(cape3km) else 0.0
    cape180 = max(0.0, cape180) if np.isfinite(cape180) else mlcape
    cin180 = min(0.0, cin180) if np.isfinite(cin180) else mlcin
    td_F = (td2m_K - 273.15) * 9 / 5 + 32 if np.isfinite(td2m_K) else 50.0
    pwat_in = (pwat_kg_m2 / 25.4) if np.isfinite(pwat_kg_m2) else 0.8
    front = front_override or "none"
    cap = _classify_cap(mlcin)
    init_conf = _initiation_confidence(front, mlcin, mlcape, td_F)
    storm_mode = _classify_storm_mode(shear_kt, srh03, front, boundary_kind)
    if np.isfinite(t2m_K) and np.isfinite(td2m_K):
        lcl_m = float(np.clip(125.0 * max(0.0, t2m_K - td2m_K), 100.0, 3500.0))
    else:
        lcl_m = float(max(400, 1500 - max(0, td_F - 50) * 25))
    return {
        "mlcape": float(mlcape),
        "mucape": float(mucape),
        "sbcape": float(sbcape),
        "cape3km": float(cape3km),
        "cape180": float(cape180),
        "cin": float(mlcin),
        "cinSb": float(surface_cin),
        "cinMl": float(mlcin),
        "cinMu": float(mucin),
        "cin180": float(cin180),
        "sfcDewpointF": float(td_F),
        "pwatIn": float(pwat_in),
        "lclM": lcl_m,
        "moistureDepthM": float(max(800, pwat_in * 1500)),
        "srh01": float(srh01),
        "srh03": float(srh03),
        "shear06Kt": float(shear_kt),
        "stormRelWindKt": float(sr_wind_kt),
        "frontSignal": front,
        "initiationConf": init_conf,
        "stormMode": storm_mode,
        "capStrength": cap,
        "stp": float(composites["stp"]),
        "scp": float(composites["scp"]),
        "ehi": float(composites["ehi"]),
        "ship": float(composites["ship"]),
        "tornadoComposite": float(composites["tor_comp"]),
        "lapseRate700500CPerKm": float(
            np.nan_to_num(composites.get("lapse_rate_700_500", 0.0), nan=0.0)
        ),
        "freezingLevelM": float(
            np.nan_to_num(composites.get("freezing_level_m", 0.0), nan=0.0)
        ),
        "mixingRatioGKg": float(
            np.nan_to_num(composites.get("mixing_ratio_gkg", 0.0), nan=0.0)
        ),
        "shipAvailable": bool(float(composites.get("ship_available", 0.0)) >= 0.5),
    }


def _valid_dt_for_hour(now: datetime, hour: int) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour)


def _valid_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _array_point(
    arr: np.ndarray | None, i_lat: int, i_lon: int, default: float = 0.0
) -> float:
    if arr is None:
        return default
    arr = np.asarray(arr, dtype=float)
    if (
        arr.ndim != 2
        or i_lat < 0
        or i_lon < 0
        or i_lat >= arr.shape[0]
        or i_lon >= arr.shape[1]
    ):
        return default
    value = float(arr[i_lat, i_lon])
    return value if np.isfinite(value) else default


def _surrogate_srh_from_shear(shear_field: np.ndarray) -> np.ndarray:
    return np.clip((np.asarray(shear_field, dtype=float) - 15.0) * 6.0, 0.0, 300.0)


def _norm_field(
    field: np.ndarray, lo_pct: float = 55.0, hi_pct: float = 96.0
) -> np.ndarray:
    arr = np.asarray(field, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size < 8:
        return np.zeros_like(arr, dtype=float)
    lo = float(np.nanpercentile(finite, lo_pct))
    hi = float(np.nanpercentile(finite, hi_pct))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.zeros_like(arr, dtype=float)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def _finite_fill(field: np.ndarray, default: float = 0.0) -> np.ndarray:
    arr = np.asarray(field, dtype=float)
    finite = arr[np.isfinite(arr)]
    fill = float(np.nanmedian(finite)) if finite.size else default
    return np.where(np.isfinite(arr), arr, fill)


def _surface_boundary_focus(
    lats: np.ndarray,
    lons: np.ndarray,
    td_field: np.ndarray,
    cape_field: np.ndarray,
    u10_field: np.ndarray | None,
    v10_field: np.ndarray | None,
    region: dict[str, Any],
) -> dict[str, Any] | None:
    """Estimate the model boundary focus from HRRR surface gradients.

    This is intentionally a diagnostic placement aid, not a replacement for
    WPC/SPC surface analysis. It uses HRRR 2 m dewpoint gradients, 10 m
    convergence, instability, and distance from the picked severe focus.
    """
    td = np.asarray(td_field, dtype=float)
    cape = np.asarray(cape_field, dtype=float)
    if td.ndim != 2 or cape.ndim != 2 or td.shape != cape.shape:
        return None

    if lats.ndim == 1 and lons.ndim == 1:
        lon_grid, lat_grid = np.meshgrid(lons, lats)
    else:
        lat_grid = np.asarray(lats, dtype=float)
        lon_grid = np.asarray(lons, dtype=float)
    if lat_grid.shape != td.shape or lon_grid.shape != td.shape:
        return None

    td_f = (td - 273.15) * 9 / 5 + 32
    finite = (
        np.isfinite(td_f)
        & np.isfinite(cape)
        & np.isfinite(lat_grid)
        & np.isfinite(lon_grid)
    )
    if not np.any(finite):
        return None

    grad_y, grad_x = np.gradient(_finite_fill(td_f, 50.0))
    lon_step = (
        float(np.nanmedian(np.diff(lon_grid, axis=1))) if lon_grid.shape[1] > 1 else 1.0
    )
    east_moistening = np.maximum(grad_x * (1 if lon_step >= 0 else -1), 0.0)
    dew_gradient = np.hypot(grad_x, grad_y)

    convergence = np.zeros_like(td_f, dtype=float)
    if u10_field is not None and v10_field is not None:
        u10 = np.asarray(u10_field, dtype=float)
        v10 = np.asarray(v10_field, dtype=float)
        if u10.shape == td.shape and v10.shape == td.shape:
            du_dx = np.gradient(_finite_fill(u10), axis=1)
            dv_dy = np.gradient(_finite_fill(v10), axis=0)
            convergence = np.maximum(-(du_dx + dv_dy), 0.0)

    center_lat = float(region["centerLat"])
    center_lon = float(region["centerLon"])
    distance = np.sqrt(
        ((lat_grid - center_lat) / 2.6) ** 2 + ((lon_grid - center_lon) / 3.8) ** 2
    )
    focus_weight = np.exp(-0.5 * distance * distance)
    upstream_weight = np.clip((center_lon - lon_grid + 1.2) / 5.0, 0.0, 1.0)

    score = (
        _norm_field(dew_gradient) * 0.32
        + _norm_field(east_moistening) * 0.26
        + _norm_field(convergence, 60.0, 97.0) * 0.16
        + _norm_field(cape, 50.0, 94.0) * 0.14
        + focus_weight * 0.12
    )
    score *= np.where(td_f >= 52, 1.0, 0.45)
    score *= 0.72 + upstream_weight * 0.28
    score *= _southern_border_multiplier(lat_grid, lon_grid)
    score = np.where(finite, score, -np.inf)

    if not np.isfinite(score).any():
        return None

    i_lat, i_lon = np.unravel_index(int(np.nanargmax(score)), score.shape)
    confidence = float(np.clip(score[i_lat, i_lon], 0.0, 1.0))
    if confidence < 0.34:
        return None

    lon = float(lon_grid[i_lat, i_lon])
    lat = float(lat_grid[i_lat, i_lon])
    dryline_states = {"TX", "OK", "KS", "NE", "SD", "ND", "CO", "NM", "WY", "MT"}
    plains = any(state in dryline_states for state in region.get("states", []))
    dryline_like = (
        plains
        and lon <= center_lon + 0.7
        and float(east_moistening[i_lat, i_lon])
        >= np.nanpercentile(east_moistening[finite], 70)
    )
    kind = (
        "triple-point"
        if dryline_like and confidence >= 0.46
        else "dryline"
        if dryline_like
        else "frontal"
    )
    return {
        "kind": kind,
        "lat": lat,
        "lon": lon,
        "confidence": confidence,
    }


def _surface_boundary_signal(surface_boundary: dict[str, Any] | None) -> str | None:
    if surface_boundary is None:
        return None
    confidence = float(surface_boundary.get("confidence", 0.0))
    if confidence >= 0.75:
        return "strong"
    if confidence >= 0.58:
        return "moderate"
    if confidence >= 0.38:
        return "weak"
    return None


def _hour_payload_from_fields(
    h: int,
    valid_iso: str,
    lats: np.ndarray,
    lons_norm: np.ndarray,
    cape_field: np.ndarray,
    cin_field: np.ndarray,
    mlcape_field: np.ndarray | None,
    mucape_field: np.ndarray | None,
    mlcin_field: np.ndarray | None,
    mucin_field: np.ndarray | None,
    cape3km_field: np.ndarray | None,
    cape180_field: np.ndarray | None,
    cin180_field: np.ndarray | None,
    td_field: np.ndarray,
    t2m_field: np.ndarray | None,
    pwat_field: np.ndarray,
    shear_field: np.ndarray,
    srh_field: np.ndarray,
    srh03_field: np.ndarray | None,
    upper_air_lines: list[dict[str, Any]],
    upper_air_vectors: list[dict[str, Any]] | None = None,
    u10_field: np.ndarray | None = None,
    v10_field: np.ndarray | None = None,
    surface_pressure_field: np.ndarray | None = None,
    t850_field: np.ndarray | None = None,
    t700_field: np.ndarray | None = None,
    t500_field: np.ndarray | None = None,
    hgt850_field: np.ndarray | None = None,
    hgt700_field: np.ndarray | None = None,
    hgt500_field: np.ndarray | None = None,
) -> dict[str, Any]:
    model_region = pick_focus_region(
        cape_field, shear_field, td_field, lats, lons_norm, cin_field
    )
    i_lat, i_lon = _nearest_grid_index(
        lats, lons_norm, model_region["centerLat"], model_region["centerLon"]
    )

    surface_cape = _array_point(cape_field, i_lat, i_lon)
    mlcape = (
        _array_point(mlcape_field, i_lat, i_lon, surface_cape * 0.85)
        if mlcape_field is not None
        else surface_cape * 0.85
    )
    mucape = (
        _array_point(mucape_field, i_lat, i_lon, max(surface_cape, mlcape))
        if mucape_field is not None
        else max(surface_cape, mlcape)
    )
    surface_cin = _array_point(cin_field, i_lat, i_lon)
    mlcin = (
        _array_point(mlcin_field, i_lat, i_lon)
        if mlcin_field is not None
        else surface_cin
    )
    mucin = (
        _array_point(mucin_field, i_lat, i_lon)
        if mucin_field is not None
        else mlcin
    )
    cape3km = (
        _array_point(cape3km_field, i_lat, i_lon)
        if cape3km_field is not None
        else 0.0
    )
    cape180 = (
        _array_point(cape180_field, i_lat, i_lon)
        if cape180_field is not None
        else mlcape
    )
    cin180 = (
        _array_point(cin180_field, i_lat, i_lon)
        if cin180_field is not None
        else mlcin
    )
    td2m = _array_point(td_field, i_lat, i_lon, 285.0)
    t2m = (
        _array_point(t2m_field, i_lat, i_lon, td2m + 8.0)
        if t2m_field is not None
        else td2m + 8.0
    )
    pwat = _array_point(pwat_field, i_lat, i_lon, 20.0)
    shear_kt = _array_point(shear_field, i_lat, i_lon)
    srh01 = _array_point(srh_field, i_lat, i_lon)
    srh03 = (
        _array_point(srh03_field, i_lat, i_lon)
        if srh03_field is not None
        else srh01 * 1.4
    )
    sr_wind = shear_kt * 0.5
    surface_boundary = _surface_boundary_focus(
        lats, lons_norm, td_field, cape_field, u10_field, v10_field, model_region
    )
    lcl_m = float(np.clip(125.0 * max(0.0, t2m - td2m), 100.0, 3500.0))
    comps = diag.composites(
        cape=np.array([surface_cape]),
        mlcape=np.array([mlcape]),
        mucape=np.array([mucape]),
        shear_kt=np.array([shear_kt]),
        srh01=np.array([srh01]),
        srh03=np.array([srh03]),
        cin=np.array([surface_cin]),
        cin_mu=np.array([mucin]),
        td2m_K=np.array([td2m]),
        t2m_K=np.array([t2m]),
        lcl_m=np.array([lcl_m]),
        surface_pressure_pa=np.array(
            [_array_point(surface_pressure_field, i_lat, i_lon, np.nan)]
        ),
        t850_K=np.array([_array_point(t850_field, i_lat, i_lon, np.nan)]),
        t700_K=np.array([_array_point(t700_field, i_lat, i_lon, np.nan)]),
        t500_K=np.array([_array_point(t500_field, i_lat, i_lon, np.nan)]),
        hgt850_m=np.array([_array_point(hgt850_field, i_lat, i_lon, np.nan)]),
        hgt700_m=np.array([_array_point(hgt700_field, i_lat, i_lon, np.nan)]),
        hgt500_m=np.array([_array_point(hgt500_field, i_lat, i_lon, np.nan)]),
    )
    comps_scalar = {k: float(v[0]) for k, v in comps.items()}
    ing = _ingredients_at_point(
        surface_cape,
        mlcape,
        mucape,
        surface_cin,
        mlcin,
        mucin,
        cape3km,
        cape180,
        cin180,
        td2m,
        t2m,
        pwat,
        shear_kt,
        srh01,
        srh03,
        sr_wind,
        comps_scalar,
        _surface_boundary_signal(surface_boundary),
        surface_boundary.get("kind") if surface_boundary else None,
    )
    payload = {
        "forecastHour": h,
        "validTimeISO": valid_iso,
        "region": model_region,
        "ingredients": ing,
        "upperAirLines": upper_air_lines,
        "upperAirVectors": upper_air_vectors or [],
    }
    if surface_boundary is not None:
        payload["surfaceBoundary"] = surface_boundary
    return payload


def _dataset_hour_payload(
    ds: xr.Dataset,
    dims: dict[str, str],
    lats: np.ndarray,
    lons_norm: np.ndarray,
    idx: int,
    h: int,
    valid_iso: str,
) -> dict[str, Any]:
    cape_field = _safe2d(_isel_time(ds["cape"], dims, idx))
    cin_field = (
        -np.abs(_safe2d(_isel_time(ds["cin"], dims, idx)))
        if "cin" in ds.variables
        else np.zeros_like(cape_field)
    )
    shear_field = _shear06_field(ds, dims, idx)
    if "td2m" in ds.variables:
        td_field = _safe2d(_isel_time(ds["td2m"], dims, idx))
    else:
        td_field = np.full_like(cape_field, 285.0)
    if "pwat" in ds.variables:
        pwat_field = _safe2d(_isel_time(ds["pwat"], dims, idx))
    else:
        pwat_field = np.full_like(cape_field, 20.0)
    srh_field = _srh_field(ds, dims, idx)
    return _hour_payload_from_fields(
        h,
        valid_iso,
        lats,
        lons_norm,
        cape_field,
        cin_field,
        _safe2d(_isel_time(ds["cape_ml"], dims, idx)) if "cape_ml" in ds.variables else None,
        _safe2d(_isel_time(ds["cape_mu"], dims, idx)) if "cape_mu" in ds.variables else None,
        -np.abs(_safe2d(_isel_time(ds["cin_ml"], dims, idx))) if "cin_ml" in ds.variables else None,
        -np.abs(_safe2d(_isel_time(ds["cin_mu"], dims, idx))) if "cin_mu" in ds.variables else None,
        _safe2d(_isel_time(ds["cape_3km"], dims, idx)) if "cape_3km" in ds.variables else None,
        _safe2d(_isel_time(ds["cape_180"], dims, idx)) if "cape_180" in ds.variables else None,
        -np.abs(_safe2d(_isel_time(ds["cin_180"], dims, idx))) if "cin_180" in ds.variables else None,
        td_field,
        _safe2d(_isel_time(ds["t2m"], dims, idx)) if "t2m" in ds.variables else None,
        pwat_field,
        shear_field,
        srh_field,
        None,
        _hgt500_lines(ds, dims, idx, lats, lons_norm),
        _wind500_vectors(ds, dims, idx, lats, lons_norm),
        _safe2d(_isel_time(ds["u10"], dims, idx)) if "u10" in ds.variables else None,
        _safe2d(_isel_time(ds["v10"], dims, idx)) if "v10" in ds.variables else None,
        _safe2d(_isel_time(ds["surface_pressure"], dims, idx))
        if "surface_pressure" in ds.variables
        else None,
        _safe2d(_isel_time(ds["t850"], dims, idx)) if "t850" in ds.variables else None,
        _safe2d(_isel_time(ds["t700"], dims, idx)) if "t700" in ds.variables else None,
        _safe2d(_isel_time(ds["t500"], dims, idx)) if "t500" in ds.variables else None,
        _safe2d(_isel_time(ds["hgt850"], dims, idx))
        if "hgt850" in ds.variables
        else None,
        _safe2d(_isel_time(ds["hgt700"], dims, idx))
        if "hgt700" in ds.variables
        else None,
        _safe2d(_isel_time(ds["hgt500"], dims, idx))
        if "hgt500" in ds.variables
        else None,
    )


def _direct_hrrr_hour_payload(h: int, grib_hour: dict[str, Any]) -> dict[str, Any]:
    fields = grib_hour["fields"]
    lats = np.asarray(grib_hour["lats"], dtype=float)
    lons = np.asarray(grib_hour["lons"], dtype=float)
    cape_field = np.asarray(fields["cape"], dtype=float)
    cin_field = np.asarray(fields.get("cin", np.zeros_like(cape_field)), dtype=float)
    td_field = np.asarray(
        fields.get("td2m", np.full_like(cape_field, 285.0)), dtype=float
    )
    t2m_field = np.asarray(fields["t2m"], dtype=float) if "t2m" in fields else None
    pwat_field = np.asarray(
        fields.get("pwat", np.full_like(cape_field, 20.0)), dtype=float
    )
    if all(k in fields for k in ("u500", "v500", "u10", "v10")):
        shear_field = (
            np.hypot(fields["u500"] - fields["u10"], fields["v500"] - fields["v10"])
            * 1.9438445
        )
    else:
        shear_field = np.zeros_like(cape_field)
    srh_field = np.asarray(
        fields.get("srh01", _surrogate_srh_from_shear(shear_field)), dtype=float
    )
    srh03_field = (
        np.asarray(fields["srh03"], dtype=float) if "srh03" in fields else None
    )
    return _hour_payload_from_fields(
        h,
        grib_hour["validTimeISO"],
        lats,
        lons,
        cape_field,
        cin_field,
        np.asarray(fields["cape_ml"], dtype=float) if "cape_ml" in fields else None,
        np.asarray(fields["cape_mu"], dtype=float) if "cape_mu" in fields else None,
        np.asarray(fields["cin_ml"], dtype=float) if "cin_ml" in fields else None,
        np.asarray(fields["cin_mu"], dtype=float) if "cin_mu" in fields else None,
        np.asarray(fields["cape_3km"], dtype=float) if "cape_3km" in fields else None,
        np.asarray(fields["cape_180"], dtype=float) if "cape_180" in fields else None,
        np.asarray(fields["cin_180"], dtype=float) if "cin_180" in fields else None,
        td_field,
        t2m_field,
        pwat_field,
        shear_field,
        srh_field,
        srh03_field,
        _hgt500_lines_from_field(fields.get("hgt500"), lats, lons),
        _wind500_vectors_from_fields(
            fields.get("u500"), fields.get("v500"), lats, lons
        ),
        np.asarray(fields.get("u10"), dtype=float) if "u10" in fields else None,
        np.asarray(fields.get("v10"), dtype=float) if "v10" in fields else None,
        np.asarray(fields.get("surface_pressure"), dtype=float)
        if "surface_pressure" in fields
        else None,
        np.asarray(fields.get("t850"), dtype=float) if "t850" in fields else None,
        np.asarray(fields.get("t700"), dtype=float) if "t700" in fields else None,
        np.asarray(fields.get("t500"), dtype=float) if "t500" in fields else None,
        np.asarray(fields.get("hgt850"), dtype=float) if "hgt850" in fields else None,
        np.asarray(fields.get("hgt700"), dtype=float) if "hgt700" in fields else None,
        np.asarray(fields.get("hgt500"), dtype=float) if "hgt500" in fields else None,
    )


def fetch_full_conus_500mb_overlay(
    target_dt: datetime,
    grid_stride: int = FULL_CONUS_OVERLAY_GRID_STRIDE,
    wind_barb_stride: int = FULL_CONUS_WIND_BARB_STRIDE,
    fetcher=fetch_hrrr_500mb_overlay_valid_time,
) -> dict[str, Any]:
    """Return real full-CONUS 500 mb contours and wind barbs from HRRR fields."""
    grib_hour = fetcher(target_dt, grid_stride=grid_stride)
    fields = grib_hour.get("fields", {})
    lats = np.asarray(grib_hour.get("lats", []), dtype=float)
    lons = np.asarray(grib_hour.get("lons", []), dtype=float)
    hgt500 = fields.get("hgt500")
    u500 = fields.get("u500")
    v500 = fields.get("v500")
    upper_air_lines = _hgt500_lines_from_field(
        hgt500, lats, lons, levels=HGT500_CONTOUR_LEVELS
    )
    upper_air_vectors = _wind500_vectors_from_fields(
        u500, v500, lats, lons, stride=wind_barb_stride
    )
    source_cycle = (
        f"HRRR {int(grib_hour.get('runCycle', 0)):02d}Z {grib_hour.get('runDate', '')}"
    )
    model_forecast_hour = int(grib_hour.get("modelForecastHour", 0))
    metadata = {
        "domain": "CONUS",
        "level": "500mb",
        "fields": ["hgt500", "u500", "v500"],
        "gridStride": int(grib_hour.get("gridStride", grid_stride)),
        "windBarbStride": max(1, int(wind_barb_stride)),
        "source": "HRRR",
        "hasHeightContours": len(upper_air_lines) > 0,
        "hasWindVectors": len(upper_air_vectors) > 0,
        "windVectorCount": len(upper_air_vectors),
        "heightContourCount": len(upper_air_lines),
        "sourceCycle": source_cycle,
        "forecastHour": model_forecast_hour,
        "runDate": grib_hour.get("runDate"),
        "runCycle": grib_hour.get("runCycle"),
        "modelForecastHour": model_forecast_hour,
        "validTimeISO": grib_hour.get("validTimeISO"),
        "cacheHit": bool(grib_hour.get("cacheHit")),
        "cachePath": grib_hour.get("cachePath"),
    }
    return {
        "upperAirLines": upper_air_lines,
        "upperAirVectors": upper_air_vectors,
        "metadata": metadata,
    }


def _empty_full_conus_500mb_overlay(
    target_dt: datetime, forecast_hour: int, error: str | None = None
) -> dict[str, Any]:
    metadata = {
        "domain": "CONUS",
        "level": "500mb",
        "fields": ["hgt500", "u500", "v500"],
        "gridStride": FULL_CONUS_OVERLAY_GRID_STRIDE,
        "windBarbStride": FULL_CONUS_WIND_BARB_STRIDE,
        "source": "HRRR",
        "hasHeightContours": False,
        "hasWindVectors": False,
        "windVectorCount": 0,
        "heightContourCount": 0,
        "sourceCycle": None,
        "forecastHour": int(forecast_hour),
        "validTimeISO": target_dt.isoformat().replace("+00:00", "Z"),
    }
    if error:
        metadata["error"] = error
    return {"upperAirLines": [], "upperAirVectors": [], "metadata": metadata}


def _overlay_has_content(overlay: dict[str, Any] | None) -> bool:
    if not overlay:
        return False
    metadata = overlay.get("metadata") or {}
    return bool(
        overlay.get("upperAirLines")
        and overlay.get("upperAirVectors")
        and metadata.get("domain") == "CONUS"
        and metadata.get("level") == "500mb"
    )


def _nearest_full_conus_500mb_overlay(
    overlays: dict[int, dict[str, Any]],
    forecast_hour: int,
    target_dt: datetime,
) -> dict[str, Any] | None:
    candidates = [
        (abs(hour - forecast_hour), hour, overlay)
        for hour, overlay in overlays.items()
        if _overlay_has_content(overlay)
    ]
    if not candidates:
        return None
    _, source_hour, source_overlay = min(
        candidates, key=lambda item: (item[0], item[1])
    )
    metadata = dict(source_overlay.get("metadata") or {})
    metadata.update(
        {
            "forecastHour": int(forecast_hour),
            "validTimeISO": target_dt.isoformat().replace("+00:00", "Z"),
            "fallbackFromForecastHour": int(source_hour),
            "fallbackFromValidTimeISO": metadata.get("validTimeISO"),
            "fallbackReason": "nearest_full_conus_500mb_overlay",
        }
    )
    return {
        "upperAirLines": list(source_overlay.get("upperAirLines", [])),
        "upperAirVectors": list(source_overlay.get("upperAirVectors", [])),
        "metadata": metadata,
    }


def _attach_full_conus_500mb_overlay(
    payload: dict[str, Any], overlay: dict[str, Any]
) -> dict[str, Any]:
    return {
        **payload,
        "upperAirLines": overlay.get("upperAirLines", []),
        "upperAirVectors": overlay.get("upperAirVectors", []),
        "upperAirOverlay": overlay.get("metadata"),
    }


def _dataset_region(
    ds: xr.Dataset,
    dims: dict[str, str],
    lats: np.ndarray,
    lons_norm: np.ndarray,
    idx: int,
) -> dict[str, Any]:
    cape_field = _safe2d(_isel_time(ds["cape"], dims, idx))
    cin_field = (
        -np.abs(_safe2d(_isel_time(ds["cin"], dims, idx)))
        if "cin" in ds.variables
        else np.zeros_like(cape_field)
    )
    shear_field = _shear06_field(ds, dims, idx)
    if "td2m" in ds.variables:
        td_field = _safe2d(_isel_time(ds["td2m"], dims, idx))
    else:
        td_field = np.full_like(cape_field, 285.0)
    return pick_focus_region(
        cape_field, shear_field, td_field, lats, lons_norm, cin_field
    )


def _bbox_for_region(region: dict[str, Any]) -> dict[str, float]:
    return {
        "lat_min": float(np.clip(region["centerLat"] - 4.0, 24.0, 50.0)),
        "lat_max": float(np.clip(region["centerLat"] + 4.0, 24.0, 50.0)),
        "lon_min": float(np.clip(region["centerLon"] - 6.0, -125.0, -66.0)),
        "lon_max": float(np.clip(region["centerLon"] + 6.0, -125.0, -66.0)),
    }


def _quick_region_from_hrrr(grib_hour: dict[str, Any]) -> dict[str, Any]:
    fields = grib_hour["fields"]
    lats = np.asarray(grib_hour["lats"], dtype=float)
    lons = np.asarray(grib_hour["lons"], dtype=float)
    cape_field = np.asarray(fields["cape"], dtype=float)
    cin_field = np.asarray(fields.get("cin", np.zeros_like(cape_field)), dtype=float)
    td_field = np.asarray(
        fields.get("td2m", np.full_like(cape_field, 285.0)), dtype=float
    )
    if all(k in fields for k in ("u500", "v500", "u10", "v10")):
        shear_field = (
            np.hypot(fields["u500"] - fields["u10"], fields["v500"] - fields["v10"])
            * 1.9438445
        )
    else:
        shear_field = np.full_like(cape_field, 30.0)
    return pick_focus_region(cape_field, shear_field, td_field, lats, lons, cin_field)


def _quick_outlook_areas_from_hrrr(grib_hour: dict[str, Any]) -> list[dict[str, Any]]:
    """Find multiple coarse categorical areas from a CONUS focus scan.

    These are used only for drawing disconnected TSTM/MRGL/SLGT blobs on the
    levels map. The primary forecast cards still use the full regional HRRR
    payload and the stricter hazard engine.
    """
    fields = grib_hour["fields"]
    lats = np.asarray(grib_hour["lats"], dtype=float)
    lons = np.asarray(grib_hour["lons"], dtype=float)
    cape_field = np.asarray(fields["cape"], dtype=float)
    cin_field = np.asarray(fields.get("cin", np.zeros_like(cape_field)), dtype=float)
    td_field = np.asarray(
        fields.get("td2m", np.full_like(cape_field, 285.0)), dtype=float
    )
    if all(k in fields for k in ("u500", "v500", "u10", "v10")):
        shear_field = (
            np.hypot(fields["u500"] - fields["u10"], fields["v500"] - fields["v10"])
            * 1.9438445
        )
    else:
        shear_field = np.full_like(cape_field, 30.0)

    if cape_field.ndim != 2 or lats.ndim != 1 or lons.ndim != 1:
        return []

    lat_grid = np.broadcast_to(lats[:, None], cape_field.shape)
    lon_grid = np.broadcast_to(lons[None, :], cape_field.shape)
    td_f = (td_field - 273.15) * 9 / 5 + 32
    areas: list[dict[str, Any]] = []

    for _name, (lat_min, lat_max, lon_min, lon_max) in SECONDARY_FOCUS_BOXES:
        mask = (
            (lat_grid >= lat_min)
            & (lat_grid <= lat_max)
            & (lon_grid >= lon_min)
            & (lon_grid <= lon_max)
            & np.isfinite(cape_field)
        )
        if not np.any(mask):
            continue

        masked_cape = np.where(mask, cape_field, 0.0)
        if float(np.nanmax(masked_cape)) < 120.0:
            continue

        region = pick_focus_region(
            masked_cape, shear_field, td_field, lats, lons, cin_field
        )
        score_field = _quick_area_score_field(cape_field, shear_field, td_f, cin_field)
        i_lat, i_lon = np.unravel_index(
            int(np.nanargmax(np.where(mask, score_field, -np.inf))),
            score_field.shape,
        )
        cape = _array_point(cape_field, i_lat, i_lon)
        shear = _array_point(shear_field, i_lat, i_lon)
        td = _array_point(td_f, i_lat, i_lon, 50.0)
        cin = _array_point(cin_field, i_lat, i_lon)
        category = _quick_area_category(cape, shear, td, cin)
        if category is None:
            continue

        areas.append(
            {
                "region": region,
                "category": category,
                "score": _quick_area_score(cape, shear, td, cin),
                "ingredients": _quick_area_ingredients(cape, shear, td, cin),
            }
        )

    return _dedupe_outlook_areas(areas)


def _quick_area_category(
    cape: float, shear_kt: float, td_f: float, cin: float
) -> str | None:
    cin_abs = abs(min(0.0, cin)) if np.isfinite(cin) else 0.0
    if cape >= 900 and shear_kt >= 42 and td_f >= 60 and cin_abs < 150:
        return "SLGT"
    if (
        cape >= 450
        and td_f >= 50
        and cin_abs < 180
        and (shear_kt >= 24 or cape >= 1200)
    ):
        return "MRGL"
    if cape >= 150 and td_f >= 45 and cin_abs < 240:
        return "TSTM"
    return None


def _quick_area_score(cape: float, shear_kt: float, td_f: float, cin: float) -> float:
    cin_mult = (
        0.35 if cin <= -180 else 0.65 if cin <= -100 else 0.85 if cin <= -50 else 1.0
    )
    return float(
        (max(cape, 0) / 1200.0)
        * (max(shear_kt, 8) / 35.0)
        * (max(td_f - 45.0, 0) / 18.0)
        * cin_mult
    )


def _quick_area_score_field(
    cape: np.ndarray, shear_kt: np.ndarray, td_f: np.ndarray, cin: np.ndarray
) -> np.ndarray:
    cin_mult = np.where(
        cin <= -180, 0.35, np.where(cin <= -100, 0.65, np.where(cin <= -50, 0.85, 1.0))
    )
    return (
        np.maximum(cape, 0.0)
        / 1200.0
        * np.maximum(shear_kt, 8.0)
        / 35.0
        * np.maximum(td_f - 45.0, 0.0)
        / 18.0
        * cin_mult
    )


def _quick_area_ingredients(
    cape: float, shear_kt: float, td_f: float, cin: float
) -> dict[str, Any]:
    cap = _classify_cap(cin)
    front = "none"
    srh01 = float(max(0.0, (shear_kt - 25.0) * 3.0))
    srh03 = float(max(0.0, (shear_kt - 20.0) * 5.0))
    return {
        "mlcape": float(max(0.0, cape * 0.85)),
        "mucape": float(max(0.0, cape)),
        "sbcape": float(max(0.0, cape)),
        "cin": float(min(0.0, cin)),
        "sfcDewpointF": float(td_f),
        "pwatIn": float(1.5 if td_f >= 68 else 1.2 if td_f >= 60 else 0.9),
        "lclM": float(np.clip(1700.0 - max(td_f - 50.0, 0.0) * 55.0, 350.0, 2200.0)),
        "moistureDepthM": float(2500.0 if td_f >= 65 else 1800.0),
        "srh01": srh01,
        "srh03": srh03,
        "shear06Kt": float(max(0.0, shear_kt)),
        "stormRelWindKt": float(max(0.0, shear_kt * 0.5)),
        "frontSignal": front,
        "initiationConf": _initiation_confidence(
            front, min(0.0, cin), cape * 0.85, td_f
        ),
        "stormMode": _classify_storm_mode(shear_kt, srh03, front),
        "capStrength": cap,
        "stp": 0.0,
        "scp": float(np.clip((cape / 1000.0) * (shear_kt / 25.0), 0.0, 8.0)),
        "ehi": 0.0,
        "ship": float(np.clip((cape / 2500.0) * (shear_kt / 30.0) * 0.6, 0.0, 4.0)),
        "tornadoComposite": 0.0,
    }


def _dedupe_outlook_areas(areas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    areas = sorted(
        areas,
        key=lambda area: (
            QUICK_CATEGORY_ORD.get(str(area.get("category")), 0),
            float(area.get("score", 0.0)),
        ),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    for area in areas:
        region = area["region"]
        too_close = False
        for other in kept:
            other_region = other["region"]
            dist = np.hypot(
                float(region["centerLat"]) - float(other_region["centerLat"]),
                (float(region["centerLon"]) - float(other_region["centerLon"])) * 0.6,
            )
            if dist < 3.2:
                too_close = True
                break
        if not too_close:
            kept.append({k: v for k, v in area.items() if k != "score"})
        if len(kept) >= 8:
            break
    return kept


def _areas_for_hour_from_anchors(
    hour: int, anchors: dict[int, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    if not anchors:
        return []
    if hour in anchors:
        return anchors[hour]

    anchor_hours = sorted(anchors)
    prev_hours = [h for h in anchor_hours if h < hour]
    next_hours = [h for h in anchor_hours if h > hour]
    if prev_hours and next_hours:
        h0 = prev_hours[-1]
        h1 = next_hours[0]
    elif not next_hours and len(anchor_hours) >= 2:
        h0, h1 = anchor_hours[-2], anchor_hours[-1]
    elif len(anchor_hours) >= 2:
        h0, h1 = anchor_hours[0], anchor_hours[1]
    else:
        return anchors[anchor_hours[0]]

    weight = (hour - h0) / max(1, h1 - h0)
    blended: list[dict[str, Any]] = []
    used_other: set[int] = set()
    for area in anchors[h0]:
        match_idx = _matching_area_index(area, anchors[h1], used_other)
        if match_idx is None:
            blended.append(area)
            continue
        used_other.add(match_idx)
        target = anchors[h1][match_idx]
        blended.append(_blend_outlook_area(area, target, weight))

    if weight > 0.5:
        for idx, area in enumerate(anchors[h1]):
            if idx not in used_other:
                blended.append(area)

    return _dedupe_outlook_areas(blended)


def _matching_area_index(
    area: dict[str, Any],
    candidates: list[dict[str, Any]],
    used: set[int],
) -> int | None:
    best_idx: int | None = None
    best_distance = float("inf")
    for idx, candidate in enumerate(candidates):
        if idx in used or candidate.get("category") != area.get("category"):
            continue
        distance = _region_distance(area["region"], candidate["region"])
        if distance < best_distance:
            best_idx = idx
            best_distance = distance

    if best_idx is not None and best_distance <= 9.0:
        return best_idx
    return None


def _blend_outlook_area(
    a: dict[str, Any], b: dict[str, Any], weight: float
) -> dict[str, Any]:
    nearest = a if weight <= 0.5 else b
    r0 = a["region"]
    r1 = b["region"]
    center_lat = float(
        np.clip(
            _lerp(float(r0["centerLat"]), float(r1["centerLat"]), weight), 24.0, 50.0
        )
    )
    center_lon = float(
        np.clip(
            _lerp(float(r0["centerLon"]), float(r1["centerLon"]), weight), -125.0, -66.0
        )
    )
    region = {
        **nearest["region"],
        "centerLat": center_lat,
        "centerLon": center_lon,
        "bbox": [center_lon - 5, center_lat - 3, center_lon + 5, center_lat + 3],
    }
    ingredients = (
        _blend_ingredients(
            a.get("ingredients", {}),
            b.get("ingredients", {}),
            weight,
        )
        if a.get("ingredients") and b.get("ingredients")
        else nearest.get("ingredients")
    )
    out = {
        **nearest,
        "region": region,
        "score": float(
            _lerp(
                float(a.get("score", 0.0)),
                float(b.get("score", a.get("score", 0.0))),
                weight,
            )
        ),
    }
    if ingredients:
        out["ingredients"] = ingredients
    return out


def _region_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    mean_lat = (float(a["centerLat"]) + float(b["centerLat"])) * 0.5 * np.pi / 180.0
    return float(
        np.hypot(
            (float(a["centerLon"]) - float(b["centerLon"])) * np.cos(mean_lat),
            float(a["centerLat"]) - float(b["centerLat"]),
        )
    )


def _region_for_hour_from_anchors(
    hour: int, anchors: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    if hour in anchors:
        return anchors[hour]
    anchor_hours = sorted(anchors)
    prev_hours = [h for h in anchor_hours if h < hour]
    next_hours = [h for h in anchor_hours if h > hour]
    if prev_hours and next_hours:
        h0 = prev_hours[-1]
        h1 = next_hours[0]
    elif not next_hours and len(anchor_hours) >= 2:
        h0, h1 = anchor_hours[-2], anchor_hours[-1]
    elif len(anchor_hours) >= 2:
        h0, h1 = anchor_hours[0], anchor_hours[1]
    else:
        return anchors[anchor_hours[0]]

    r0 = anchors[h0]
    r1 = anchors[h1]
    weight = (hour - h0) / max(1, h1 - h0)
    nearest = r0 if weight <= 0.5 else r1
    center_lat = float(
        np.clip(_lerp(r0["centerLat"], r1["centerLat"], weight), 24.0, 50.0)
    )
    center_lon = float(
        np.clip(_lerp(r0["centerLon"], r1["centerLon"], weight), -125.0, -66.0)
    )
    return {
        **nearest,
        "centerLat": center_lat,
        "centerLon": center_lon,
        "bbox": [center_lon - 5, center_lat - 3, center_lon + 5, center_lat + 3],
    }


def _nearest_anchor_payload(
    hour: int, anchors: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    nearest_hour = min(anchors, key=lambda h: abs(h - hour))
    return anchors[nearest_hour]


def _payload_for_hour_from_anchors(
    hour: int,
    valid_iso: str,
    anchors: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if hour in anchors:
        return {**anchors[hour], "forecastHour": hour, "validTimeISO": valid_iso}

    anchor_hours = sorted(anchors)
    prev_hours = [h for h in anchor_hours if h < hour]
    next_hours = [h for h in anchor_hours if h > hour]
    if prev_hours and next_hours:
        h0 = prev_hours[-1]
        h1 = next_hours[0]
    elif len(anchor_hours) >= 2 and not next_hours:
        h0, h1 = anchor_hours[-2], anchor_hours[-1]
    elif len(anchor_hours) >= 2:
        h0, h1 = anchor_hours[0], anchor_hours[1]
    else:
        nearest = _nearest_anchor_payload(hour, anchors)
        return {**nearest, "forecastHour": hour, "validTimeISO": valid_iso}

    p0 = anchors[h0]
    p1 = anchors[h1]
    span = max(1, h1 - h0)
    weight = (hour - h0) / span
    nearest = p0 if abs(hour - h0) <= abs(hour - h1) else p1

    region0 = p0["region"]
    region1 = p1["region"]
    center_lat = _lerp(region0["centerLat"], region1["centerLat"], weight)
    center_lon = _lerp(region0["centerLon"], region1["centerLon"], weight)
    region = {
        **nearest["region"],
        "centerLat": float(np.clip(center_lat, 24.0, 50.0)),
        "centerLon": float(np.clip(center_lon, -125.0, -66.0)),
    }
    region["bbox"] = [
        region["centerLon"] - 5,
        region["centerLat"] - 3,
        region["centerLon"] + 5,
        region["centerLat"] + 3,
    ]

    ingredients = _blend_ingredients(p0["ingredients"], p1["ingredients"], weight)
    payload = {
        "forecastHour": hour,
        "validTimeISO": valid_iso,
        "region": region,
        "ingredients": ingredients,
        "upperAirLines": _blend_upper_air_lines(
            p0.get("upperAirLines", []),
            p1.get("upperAirLines", []),
            weight,
            nearest.get("upperAirLines", []),
        ),
        "upperAirVectors": _blend_upper_air_vectors(
            p0.get("upperAirVectors", []),
            p1.get("upperAirVectors", []),
            weight,
            nearest.get("upperAirVectors", []),
        ),
    }
    surface_boundary = _blend_surface_boundary(
        p0.get("surfaceBoundary"),
        p1.get("surfaceBoundary"),
        weight,
        nearest.get("surfaceBoundary"),
    )
    if surface_boundary is not None:
        payload["surfaceBoundary"] = surface_boundary
    return payload


def _lerp(a: float, b: float, weight: float) -> float:
    return float(a + (b - a) * weight)


def _blend_upper_air_lines(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    weight: float,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Interpolate 500 mb contours between fetched anchor hours."""
    if not a or not b:
        return fallback

    b_by_value: dict[int, list[dict[str, Any]]] = {}
    for line in b:
        key = int(round(float(line.get("value", 0))))
        b_by_value.setdefault(key, []).append(line)

    blended: list[dict[str, Any]] = []
    for idx, line0 in enumerate(a):
        value_key = int(round(float(line0.get("value", 0))))
        candidates = b_by_value.get(value_key)
        line1 = candidates.pop(0) if candidates else b[min(idx, len(b) - 1)]
        coords0 = line0.get("coords") or []
        coords1 = line1.get("coords") or []
        coords = _blend_polyline(coords0, coords1, weight)
        if len(coords) < 2:
            continue
        blended.append(
            {
                "level": "500mb",
                "value": float(
                    _lerp(
                        float(line0.get("value", 0)),
                        float(line1.get("value", line0.get("value", 0))),
                        weight,
                    )
                ),
                "coords": coords,
            }
        )

    return blended or fallback


def _blend_polyline(
    coords0: list[list[float]] | list[tuple[float, float]],
    coords1: list[list[float]] | list[tuple[float, float]],
    weight: float,
) -> list[list[float]]:
    if not coords0 or not coords1:
        return []
    count = max(2, min(96, max(len(coords0), len(coords1))))
    return [
        [
            _lerp(
                _coord_at(coords0, i, count)[0], _coord_at(coords1, i, count)[0], weight
            ),
            _lerp(
                _coord_at(coords0, i, count)[1], _coord_at(coords1, i, count)[1], weight
            ),
        ]
        for i in range(count)
    ]


def _coord_at(
    coords: list[list[float]] | list[tuple[float, float]],
    idx: int,
    count: int,
) -> tuple[float, float]:
    if len(coords) == 1 or count <= 1:
        lon, lat = coords[0]
        return float(lon), float(lat)
    pos = (idx / (count - 1)) * (len(coords) - 1)
    lo = int(np.floor(pos))
    hi = min(len(coords) - 1, lo + 1)
    frac = pos - lo
    lon0, lat0 = coords[lo]
    lon1, lat1 = coords[hi]
    return _lerp(float(lon0), float(lon1), frac), _lerp(float(lat0), float(lat1), frac)


def _blend_upper_air_vectors(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    weight: float,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Interpolate sampled 500 mb wind barbs between fetched anchor hours."""
    if not a or not b:
        return fallback

    a_sorted = sorted(a, key=lambda v: (float(v.get("lat", 0)), float(v.get("lon", 0))))
    b_sorted = sorted(b, key=lambda v: (float(v.get("lat", 0)), float(v.get("lon", 0))))
    count = min(len(a_sorted), len(b_sorted), 160)
    blended: list[dict[str, Any]] = []
    for idx in range(count):
        v0 = a_sorted[idx]
        v1 = b_sorted[idx]
        u_kt = _lerp(float(v0.get("uKt", 0)), float(v1.get("uKt", 0)), weight)
        v_kt = _lerp(float(v0.get("vKt", 0)), float(v1.get("vKt", 0)), weight)
        speed_kt = float(np.hypot(u_kt, v_kt))
        blended.append(
            {
                "level": "500mb",
                "lon": _lerp(float(v0.get("lon", 0)), float(v1.get("lon", 0)), weight),
                "lat": _lerp(float(v0.get("lat", 0)), float(v1.get("lat", 0)), weight),
                "uKt": u_kt,
                "vKt": v_kt,
                "speedKt": speed_kt,
            }
        )

    return blended or fallback


def _blend_surface_boundary(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
    weight: float,
    fallback: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if a and b:
        nearest = a if weight <= 0.5 else b
        return {
            "kind": nearest.get("kind", "frontal"),
            "lat": _lerp(
                float(a.get("lat", 0)), float(b.get("lat", a.get("lat", 0))), weight
            ),
            "lon": _lerp(
                float(a.get("lon", 0)), float(b.get("lon", a.get("lon", 0))), weight
            ),
            "confidence": float(
                np.clip(
                    _lerp(
                        float(a.get("confidence", 0)),
                        float(b.get("confidence", a.get("confidence", 0))),
                        weight,
                    ),
                    0.0,
                    1.0,
                )
            ),
        }
    return fallback


def _blend_ingredients(
    a: dict[str, Any], b: dict[str, Any], weight: float
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    categorical = {"frontSignal", "stormMode", "capStrength", "shipAvailable"}
    nearest = a if weight <= 0.5 else b
    nonnegative = {
        "mlcape",
        "mucape",
        "sbcape",
        "cape3km",
        "cape180",
        "sfcDewpointF",
        "pwatIn",
        "lclM",
        "moistureDepthM",
        "srh01",
        "srh03",
        "shear06Kt",
        "stormRelWindKt",
        "stp",
        "scp",
        "ehi",
        "ship",
        "tornadoComposite",
        "lapseRate700500CPerKm",
        "freezingLevelM",
        "mixingRatioGKg",
        "surfacePressurePa",
    }
    for key, value in a.items():
        if key in categorical:
            out[key] = nearest.get(key, value)
            continue
        av = float(value)
        bv = float(b.get(key, value))
        blended = _lerp(av, bv, weight)
        if key == "initiationConf":
            blended = float(np.clip(blended, 0.0, 1.0))
        elif key in {"cin", "cinSb", "cinMl", "cinMu", "cin180"}:
            blended = min(0.0, blended)
        elif key in nonnegative:
            blended = max(0.0, blended)
        out[key] = blended
    return out


def build_bundle(now: datetime | None = None) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    # Three-hour anchors are a practical minimum for convective outlooks:
    # broad 12-hour focus scans can skip short-lived severe corridors, as in
    # the 2026-05-02 15Z Southeast setup.
    focus_hours = list(range(0, 49, 3))
    anchor_hours = list(range(0, 49, 3))
    focus_regions: dict[int, dict[str, Any]] = {}
    focus_area_anchors: dict[int, list[dict[str, Any]]] = {}
    raw_anchors: dict[int, dict[str, Any]] = {}
    upper_air_overlays: dict[int, dict[str, Any]] = {}

    def _fetch_focus(hour: int) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
        grib_hour = fetch_hrrr_grib_valid_time(
            _valid_dt_for_hour(now, hour), profile="focus"
        )
        return (
            hour,
            _quick_region_from_hrrr(grib_hour),
            _quick_outlook_areas_from_hrrr(grib_hour),
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_fetch_focus, h) for h in focus_hours]
        for future in as_completed(futures):
            try:
                hour, focus_region, outlook_areas = future.result()
                focus_regions[hour] = focus_region
                focus_area_anchors[hour] = outlook_areas
            except NomadsFetchError as exc:
                log.info("HRRR focus scan skipped: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("HRRR focus scan failed: %s", exc)

    if not focus_regions:
        raise NomadsFetchError("Direct HRRR GRIB focus scan returned no usable hours")

    def _fetch_anchor(hour: int) -> tuple[int, dict[str, Any]]:
        focus_region = _region_for_hour_from_anchors(hour, focus_regions)
        return hour, fetch_hrrr_grib_valid_time(
            _valid_dt_for_hour(now, hour),
            _bbox_for_region(focus_region),
            profile="full",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_fetch_anchor, h) for h in anchor_hours]
        for future in as_completed(futures):
            try:
                hour, grib_hour = future.result()
                raw_anchors[hour] = grib_hour
            except NomadsFetchError as exc:
                log.info("HRRR anchor skipped: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("HRRR anchor failed: %s", exc)

    if len(raw_anchors) < 2:
        raise NomadsFetchError(
            "Direct HRRR GRIB filter did not return enough anchor hours"
        )

    anchor_payloads = {
        h: _direct_hrrr_hour_payload(h, raw_anchors[h]) for h in sorted(raw_anchors)
    }

    def _fetch_overlay(hour: int) -> tuple[int, dict[str, Any]]:
        valid_dt = _valid_dt_for_hour(now, hour)
        return hour, fetch_full_conus_500mb_overlay(valid_dt)

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_hour = {executor.submit(_fetch_overlay, h): h for h in FORECAST_HOURS}
        for future in as_completed(future_to_hour):
            try:
                hour, overlay = future.result()
                upper_air_overlays[hour] = overlay
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "Full-CONUS 500 mb overlay skipped for F%02d: %s",
                    future_to_hour[future],
                    exc,
                )

    ml_status = model_status()
    ml_used_hours = 0
    hours_out = []
    for h in FORECAST_HOURS:
        valid_dt = _valid_dt_for_hour(now, h)
        payload = _payload_for_hour_from_anchors(
            h, _valid_iso(valid_dt), anchor_payloads
        )
        overlay = upper_air_overlays.get(h)
        if not _overlay_has_content(overlay):
            overlay = _nearest_full_conus_500mb_overlay(upper_air_overlays, h, valid_dt)
        if overlay is None:
            overlay = _empty_full_conus_500mb_overlay(
                valid_dt, h, "Full-CONUS HRRR 500 mb overlay unavailable"
            )
        payload = _attach_full_conus_500mb_overlay(payload, overlay)
        ml_hazards = predict_ml_hazards(payload.get("ingredients", {}), h)
        if ml_hazards is not None:
            payload["mlHazards"] = ml_hazards
            ml_used_hours += 1
        outlook_areas = _areas_for_hour_from_anchors(h, focus_area_anchors)
        if outlook_areas:
            payload["outlookAreas"] = outlook_areas
        hours_out.append(payload)

    region = _nearest_anchor_payload(12, anchor_payloads)["region"]
    first_anchor = raw_anchors[min(raw_anchors)]
    cycle_dt = datetime.strptime(
        f"{first_anchor['runDate']}{first_anchor['runCycle']:02d}", "%Y%m%d%H"
    ).replace(tzinfo=timezone.utc)
    cycle_str = f"HRRR {first_anchor['runCycle']:02d}Z {first_anchor['runDate']}"
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if ml_status.get("active") and ml_used_hours:
        ml_note = f", ML hazards active: {ml_status.get('version', 'unknown')}"
    elif ml_status.get("active"):
        ml_note = ", ML hazards unavailable during inference; fallback rule hazards"
    else:
        ml_note = f", ML hazards inactive: {ml_status.get('reason', 'model artifacts unavailable')}; fallback rule hazards"
    overlay_count = sum(
        1
        for overlay in upper_air_overlays.values()
        if overlay.get("metadata", {}).get("hasHeightContours")
        or overlay.get("metadata", {}).get("hasWindVectors")
    )

    bundle = {
        "cycle": cycle_str,
        "issuedAtISO": cycle_dt.isoformat().replace("+00:00", "Z"),
        "providerNotes": (
            "NOMADS HRRR GRIB filter"
            f" ({len(focus_regions)} CONUS focus scans, {len(anchor_payloads)} regional anchor hrs,"
            f" {overlay_count} full-CONUS 500mb overlays, hourly interpolation, multi-area contours)"
            f" - focus: {region['label']}"
            f"{ml_note}"
        ),
        "latencyMs": elapsed_ms,
        "region": region,
        "cities": CONUS_CITIES,
        "hours": hours_out,
        "mlHazardHours": ml_used_hours,
        "mlModel": ml_status,
    }
    return bundle


# --- helpers ---


def _scalar(da: xr.DataArray) -> float:
    arr = np.asarray(da.values).ravel()
    if arr.size == 0:
        return float("nan")
    v = float(arr[0])
    return v if np.isfinite(v) else 0.0


def _safe2d(da: xr.DataArray) -> np.ndarray:
    arr = np.asarray(da.values, dtype=float)
    arr = np.where(np.isfinite(arr), arr, 0.0)
    while arr.ndim > 2 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim == 0:
        arr = arr.reshape(1, 1)
    return arr


def _isel_time(da: xr.DataArray, dims: dict[str, str], vtime_idx: int) -> xr.DataArray:
    """isel along the dataset's actual time-axis dim name."""
    vt = dims.get("validtime")
    if vt is not None and vt in da.dims:
        return da.isel({vt: vtime_idx})
    return da


def _point(
    da: xr.DataArray, dims: dict[str, str], vtime_idx: int, i_lat: int, i_lon: int
) -> float:
    sel: dict[str, int] = {}
    vt = dims.get("validtime")
    if vt and vt in da.dims:
        sel[vt] = vtime_idx
    lat_n = dims.get("latitude")
    if lat_n and lat_n in da.dims:
        sel[lat_n] = i_lat
    lon_n = dims.get("longitude")
    if lon_n and lon_n in da.dims:
        sel[lon_n] = i_lon
    return _scalar(da.isel(sel))


def _shear06_field(ds: xr.Dataset, dims: dict[str, str], vtime_idx: int) -> np.ndarray:
    """Approximate deep-layer shear from 10 m to 500 mb winds in knots."""
    base_shape = _safe2d(_isel_time(ds["cape"], dims, vtime_idx)).shape
    if "u_iso" not in ds.variables or "v_iso" not in ds.variables:
        return np.zeros(base_shape)
    iso_dim = next(
        (d for d in ds["u_iso"].dims if d.lower().startswith("isobaric")), None
    )
    if iso_dim is None:
        return np.zeros(base_shape)
    u500 = _safe2d(
        _isel_time(ds["u_iso"], dims, vtime_idx).sel({iso_dim: 50000}, method="nearest")
    )
    v500 = _safe2d(
        _isel_time(ds["v_iso"], dims, vtime_idx).sel({iso_dim: 50000}, method="nearest")
    )
    if "u10" in ds.variables and "v10" in ds.variables:
        u_low = _safe2d(_isel_time(ds["u10"], dims, vtime_idx))
        v_low = _safe2d(_isel_time(ds["v10"], dims, vtime_idx))
    else:
        u_low = _safe2d(
            _isel_time(ds["u_iso"], dims, vtime_idx).sel(
                {iso_dim: 100000}, method="nearest"
            )
        )
        v_low = _safe2d(
            _isel_time(ds["v_iso"], dims, vtime_idx).sel(
                {iso_dim: 100000}, method="nearest"
            )
        )
    du = u500 - u_low
    dv = v500 - v_low
    shear_ms = np.hypot(du, dv)
    return shear_ms * 1.9438445


def _srh_field(ds: xr.Dataset, dims: dict[str, str], vtime_idx: int) -> np.ndarray:
    if "srh01" in ds.variables:
        return _safe2d(_isel_time(ds["srh01"], dims, vtime_idx))
    base_shape = _safe2d(_isel_time(ds["cape"], dims, vtime_idx)).shape
    return np.zeros(base_shape)


def _hgt500_lines(
    ds: xr.Dataset,
    dims: dict[str, str],
    vtime_idx: int,
    lats: np.ndarray,
    lons: np.ndarray,
) -> list[dict[str, Any]]:
    """Generate 500 mb geopotential-height contour polylines for the map."""
    if "hgt_iso" not in ds.variables:
        return []
    iso_dim = next(
        (d for d in ds["hgt_iso"].dims if d.lower().startswith("isobaric")), None
    )
    if iso_dim is None:
        return []

    try:
        hgt = _safe2d(
            _isel_time(ds["hgt_iso"], dims, vtime_idx).sel(
                {iso_dim: 50000}, method="nearest"
            )
        )
    except Exception:
        return []

    return _hgt500_lines_from_field(hgt, lats, lons)


def _wind500_vectors(
    ds: xr.Dataset,
    dims: dict[str, str],
    vtime_idx: int,
    lats: np.ndarray,
    lons: np.ndarray,
) -> list[dict[str, Any]]:
    """Sample 500 mb wind vectors for frontend wind-barb rendering."""
    if "u_iso" not in ds.variables or "v_iso" not in ds.variables:
        return []
    iso_dim = next(
        (d for d in ds["u_iso"].dims if d.lower().startswith("isobaric")), None
    )
    if iso_dim is None:
        return []

    try:
        u500 = _safe2d(
            _isel_time(ds["u_iso"], dims, vtime_idx).sel(
                {iso_dim: 50000}, method="nearest"
            )
        )
        v500 = _safe2d(
            _isel_time(ds["v_iso"], dims, vtime_idx).sel(
                {iso_dim: 50000}, method="nearest"
            )
        )
    except Exception:
        return []

    return _wind500_vectors_from_fields(u500, v500, lats, lons)


def _wind500_vectors_from_fields(
    u500: np.ndarray | None,
    v500: np.ndarray | None,
    lats: np.ndarray,
    lons: np.ndarray,
    stride: int = 22,
) -> list[dict[str, Any]]:
    """Convert gridded 500 mb U/V wind components from m/s to sampled kt barbs."""
    if u500 is None or v500 is None:
        return []
    u = np.asarray(u500, dtype=float)
    v = np.asarray(v500, dtype=float)
    if u.ndim != 2 or v.ndim != 2 or u.shape != v.shape:
        return []

    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        if lat_arr.size != u.shape[0] or lon_arr.size != u.shape[1]:
            return []
        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)
    else:
        lat_grid = lat_arr
        lon_grid = lon_arr
        if lat_grid.shape != u.shape or lon_grid.shape != u.shape:
            return []

    rows, cols = u.shape
    stride = max(1, int(stride))
    row_start = 0 if rows <= stride else stride // 2
    col_start = 0 if cols <= stride else stride // 2
    vectors: list[dict[str, Any]] = []
    for i in range(row_start, rows, stride):
        for j in range(col_start, cols, stride):
            lon = float(lon_grid[i, j])
            lat = float(lat_grid[i, j])
            u_ms = float(u[i, j])
            v_ms = float(v[i, j])
            speed_ms = float(np.hypot(u_ms, v_ms))
            if not all(np.isfinite(x) for x in (lon, lat, u_ms, v_ms, speed_ms)):
                continue
            if -130 <= lon <= -60 and 20 <= lat <= 55:
                u_kt = float(u_ms * 1.9438445)
                v_kt = float(v_ms * 1.9438445)
                speed_kt = float(speed_ms * 1.9438445)
                vectors.append(
                    {
                        "level": "500mb",
                        "lon": lon,
                        "lat": lat,
                        "uKt": u_kt,
                        "vKt": v_kt,
                        "speedKt": speed_kt,
                    }
                )
    return vectors


def _hgt500_lines_from_field(
    hgt: np.ndarray | None,
    lats: np.ndarray,
    lons: np.ndarray,
    levels: tuple[int, ...] = HGT500_CONTOUR_LEVELS,
) -> list[dict[str, Any]]:
    """Generate 500 mb geopotential-height contour polylines for the map."""
    if hgt is None:
        return []
    hgt = np.asarray(hgt, dtype=float)
    finite = np.isfinite(hgt)
    if hgt.ndim != 2 or finite.sum() < 4:
        return []

    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        if lat_arr.size != hgt.shape[0] or lon_arr.size != hgt.shape[1]:
            return []
        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)
    else:
        lat_grid = lat_arr
        lon_grid = lon_arr
        if lat_grid.shape != hgt.shape or lon_grid.shape != hgt.shape:
            return []

    finite_hgt = hgt[finite]
    hmin = float(np.nanmin(finite_hgt))
    hmax = float(np.nanmax(finite_hgt))
    if not np.isfinite(hmin) or not np.isfinite(hmax) or hmax <= hmin:
        return []
    valid_levels = [float(level) for level in levels if hmin <= float(level) <= hmax]
    if not valid_levels:
        return []

    with _MATPLOTLIB_CONTOUR_LOCK:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
            return []

        fig, ax = plt.subplots(figsize=(1, 1))
        try:
            cs = ax.contour(lon_grid, lat_grid, hgt, levels=valid_levels)
            lines: list[dict[str, Any]] = []
            for level, segments in zip(cs.levels, cs.allsegs):
                for seg in segments:
                    if len(seg) < 12:
                        continue
                    decimated = seg[:: max(1, len(seg) // 80)]
                    coords = [
                        [float(lon), float(lat)]
                        for lon, lat in decimated
                        if -130 <= lon <= -60 and 20 <= lat <= 55
                    ]
                    if len(coords) >= 8:
                        lines.append(
                            {"level": "500mb", "value": float(level), "coords": coords}
                        )
            return lines[:48]
        finally:
            plt.close(fig)
