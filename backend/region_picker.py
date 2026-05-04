"""Pick the focus region from an xarray dataset of CAPE/shear/dewpoint."""
from __future__ import annotations

from typing import Tuple

import numpy as np
import xarray as xr


def _latlon_names(ds: xr.Dataset) -> Tuple[str, str]:
    lat = next((c for c in ds.coords if str(c).lower() in ("lat", "latitude", "y")), "lat")
    lon = next((c for c in ds.coords if str(c).lower() in ("lon", "longitude", "x")), "lon")
    return lat, lon


def _normalize_lon(lon: float) -> float:
    """Convert 0..360 longitudes to -180..180 (which is what the frontend expects)."""
    if lon > 180:
        return lon - 360
    if lon < -180:
        return lon + 360
    return lon


def _interp_anchor(x: np.ndarray, anchors: list[tuple[float, float]]) -> np.ndarray:
    """Piecewise-linear interpolation with clamped end points."""
    xp = np.asarray([a[0] for a in anchors], dtype=float)
    fp = np.asarray([a[1] for a in anchors], dtype=float)
    return np.interp(x, xp, fp, left=fp[0], right=fp[-1])


def _gulf_min_land_lat(lon_grid: np.ndarray) -> np.ndarray:
    anchors = [
        (-98.5, 26.7),
        (-96.0, 28.2),
        (-94.0, 29.2),
        (-91.0, 29.4),
        (-88.5, 30.0),
        (-86.0, 30.2),
        (-84.0, 29.9),
        (-82.0, 28.5),
        (-81.0, 27.0),
    ]
    return _interp_anchor(lon_grid, anchors)


def _atlantic_max_land_lon(lat_grid: np.ndarray) -> np.ndarray:
    anchors = [
        (25.0, -80.1),
        (27.0, -80.1),
        (29.0, -80.7),
        (30.5, -81.1),
        (32.0, -80.2),
        (34.0, -78.4),
        (36.0, -75.5),
        (38.0, -74.5),
        (40.0, -73.7),
        (42.0, -70.1),
        (44.0, -69.0),
        (46.0, -67.5),
    ]
    return _interp_anchor(lat_grid, anchors)


def _coastal_land_multiplier(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    """Approximate inland confidence for CONUS focus picking.

    HRRR CAPE/shear maxima often sit over the Gulf or western Atlantic early
    in the day. Those cells can look impressive in raw ingredients but they
    make poor anchors for a categorical SPC-style outlook, and Rawinsonde/SPC
    products generally keep organized severe areas on land. This multiplier
    suppresses water/coastal-edge cells before the focus max is selected.
    """
    mult = np.ones_like(lat_grid, dtype=float)

    gulf_domain = (lon_grid >= -98.5) & (lon_grid <= -81.0)
    gulf_edge = _gulf_min_land_lat(lon_grid)
    gulf_inland = lat_grid - gulf_edge
    mult = np.where(gulf_domain & (gulf_inland < 0.0), mult * 0.015, mult)
    mult = np.where(gulf_domain & (gulf_inland >= 0.0) & (gulf_inland < 0.75), mult * 0.35, mult)
    mult = np.where(gulf_domain & (gulf_inland >= 0.75) & (gulf_inland < 1.5), mult * 0.65, mult)

    atl_domain = (lat_grid >= 25.0) & (lat_grid <= 46.0)
    atl_edge = _atlantic_max_land_lon(lat_grid)
    atl_inland = atl_edge - lon_grid  # positive west/inland, negative offshore
    mult = np.where(atl_domain & (atl_inland < 0.0), mult * 0.015, mult)
    mult = np.where(atl_domain & (atl_inland >= 0.0) & (atl_inland < 0.85), mult * 0.30, mult)
    mult = np.where(atl_domain & (atl_inland >= 0.85) & (atl_inland < 1.8), mult * 0.60, mult)

    pac = (lon_grid < -123.0) & (lat_grid > 30.0) & (lat_grid < 49.0)
    mult = np.where(pac, mult * 0.05, mult)

    # The eastern seaboard often has broad warm-sector CAPE but limited
    # organized-severe support at the focus-selection stage. Keep it eligible,
    # but require it to clearly beat inland/western alternatives.
    east_warm_sector = (lon_grid > -84.0) & (lat_grid < 36.5)
    carolina_offshore_edge = (lon_grid > -80.5) & (lat_grid < 35.0)
    mult = np.where(east_warm_sector, mult * 0.58, mult)
    mult = np.where(carolina_offshore_edge, mult * 0.45, mult)

    # Florida/GA/Carolinas severe corridors often hug the coast or peninsula.
    # Keep true offshore cells suppressed, but do not let the crude Gulf/Atlantic
    # edge penalties erase an inland Southeast warm-sector maximum.
    southeast_coastal_land = (
        (lon_grid >= -86.5) & (lon_grid <= -76.0) &
        (lat_grid >= 27.0) & (lat_grid <= 35.5) &
        ((lat_grid - gulf_edge) >= -0.10) &
        ((atl_edge - lon_grid) >= 0.10)
    )
    mult = np.where(southeast_coastal_land, np.maximum(mult, 0.55), mult)

    return mult


def _focus_tiebreak_multiplier(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    """Prefer inland/upstream severe initiation zones when scores are close."""
    west_bonus = np.clip((-88.0 - lon_grid) / 18.0, 0.0, 1.0) * 0.28
    plains_bonus = (
        ((lon_grid >= -103.0) & (lon_grid <= -94.0) & (lat_grid >= 28.5) & (lat_grid <= 38.5))
        .astype(float)
        * 0.18
    )
    return 1.0 + west_bonus + plains_bonus


def _finite_fill(field: np.ndarray, default: float = 0.0) -> np.ndarray:
    arr = np.asarray(field, dtype=float)
    finite = arr[np.isfinite(arr)]
    fill = float(np.nanmedian(finite)) if finite.size else default
    return np.where(np.isfinite(arr), arr, fill)


def _norm_field(field: np.ndarray, lo_pct: float = 55.0, hi_pct: float = 96.0) -> np.ndarray:
    arr = np.asarray(field, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size < 8:
        return np.zeros_like(arr, dtype=float)
    lo = float(np.nanpercentile(finite, lo_pct))
    hi = float(np.nanpercentile(finite, hi_pct))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.zeros_like(arr, dtype=float)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def _boundary_signal(td_F: np.ndarray, cape_arr: np.ndarray, shear_arr: np.ndarray) -> np.ndarray:
    """Highlight dryline/frontal gradients so moisture pools do not dominate."""
    td_fill = _finite_fill(td_F, 50.0)
    grad_y, grad_x = np.gradient(td_fill)
    dew_gradient = np.hypot(grad_x, grad_y)
    east_moistening = np.maximum(grad_x, 0.0)
    return (
        _norm_field(dew_gradient) * 0.52 +
        _norm_field(east_moistening) * 0.31 +
        _norm_field(cape_arr, 50.0, 94.0) * 0.10 +
        _norm_field(shear_arr, 50.0, 95.0) * 0.07
    )


def _southern_border_lat(lon_grid: np.ndarray) -> np.ndarray:
    """Very coarse southern U.S. boundary used only to suppress Mexico maxima."""
    anchors = [
        (-124.0, 32.5),
        (-117.0, 32.5),
        (-114.5, 32.0),
        (-111.0, 31.3),
        (-108.0, 31.3),
        (-106.3, 31.7),
        (-104.5, 30.2),
        (-103.0, 29.4),
        (-101.0, 28.9),
        (-99.5, 27.2),
        (-97.4, 25.9),
        (-95.0, 28.7),
    ]
    return _interp_anchor(lon_grid, anchors)


def _southern_border_multiplier(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    """Keep the focus on U.S.-relevant land unless a border event clearly wins."""
    mult = np.ones_like(lat_grid, dtype=float)
    domain = (lon_grid >= -124.0) & (lon_grid <= -95.0) & (lat_grid <= 33.0)
    south_of_border = _southern_border_lat(lon_grid) - lat_grid
    mult = np.where(domain & (south_of_border > 1.2), mult * 0.18, mult)
    mult = np.where(domain & (south_of_border > 0.45) & (south_of_border <= 1.2), mult * 0.45, mult)
    mult = np.where(domain & (south_of_border > 0.0) & (south_of_border <= 0.45), mult * 0.75, mult)
    return mult


def _focus_structure_multiplier(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    td_F: np.ndarray,
    cape_arr: np.ndarray,
    shear_arr: np.ndarray,
) -> np.ndarray:
    boundary = _boundary_signal(td_F, cape_arr, shear_arr)
    plains_dryline = (
        (lon_grid >= -106.0) & (lon_grid <= -98.0) &
        (lat_grid >= 27.5) & (lat_grid <= 36.5)
    )
    dryline_bonus = 1.0 + plains_dryline.astype(float) * (
        boundary * 0.35 + np.clip((shear_arr - 45.0) / 30.0, 0.0, 1.0) * 0.18
    )

    coastal_moisture = (
        (lon_grid >= -98.5) & (lon_grid <= -90.0) &
        (lat_grid <= 30.5)
    )
    coastal_penalty = 1.0 - coastal_moisture.astype(float) * (
        np.clip((td_F - 68.0) / 8.0, 0.0, 1.0) *
        (1.0 - np.clip(boundary, 0.0, 1.0) * 0.4) *
        0.28
    )

    return (0.75 + 0.55 * boundary) * dryline_bonus * coastal_penalty


def pick_focus_region(
    cape: np.ndarray,
    shear_kt: np.ndarray,
    td2m_K: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    cin: np.ndarray | None = None,
) -> dict:
    """Score grid cells by CAPE * shear * dewpoint and return a region dict.

    Lons are normalized to -180..180 in the returned dict, matching the
    frontend / react-simple-maps convention.
    """
    cape_arr = np.where(np.isfinite(cape), cape, 0.0)
    shear_arr = np.where(np.isfinite(shear_kt), shear_kt, 0.0)
    td_arr = np.where(np.isfinite(td2m_K), td2m_K, 250.0)
    td_F = (td_arr - 273.15) * 9 / 5 + 32
    # Cap the dewpoint contribution. Gulf/coastal moisture pools should widen
    # thunder coverage, but they should not outrank a sharper inland dryline
    # or frontal focus with stronger shear.
    moist = np.clip(td_F - 52, 0, 14)
    if cin is not None:
        cin_arr = np.where(np.isfinite(cin), cin, 0.0)
        cap_mult = np.where(cin_arr <= -200, 0.25,
                   np.where(cin_arr <= -100, 0.55,
                   np.where(cin_arr <= -50, 0.80, 1.0)))
    else:
        cap_mult = 1.0

    # Normalize lons we use for filtering (don't mutate input).
    norm_lons = np.array([_normalize_lon(float(x)) for x in np.asarray(lons).ravel()]).reshape(np.asarray(lons).shape)

    # Soft land mask: favor CONUS interior, penalize obvious water cells
    # (Gulf of Mexico, offshore Atlantic, offshore Pacific). Crude but
    # avoids the focus picking the Gulf when it's the warmest CAPE source.
    if cape_arr.ndim == 2 and lats.ndim == 1 and norm_lons.ndim == 1:
        lat_grid = np.broadcast_to(lats[:, None], cape_arr.shape)
        lon_grid = np.broadcast_to(norm_lons[None, :], cape_arr.shape)
        in_box = (
            (lat_grid >= 25) & (lat_grid <= 49) &
            (lon_grid >= -125) & (lon_grid <= -66)
        )
        land_mult = np.where(in_box, 1.0, 0.3)
        land_mult = land_mult * _coastal_land_multiplier(lat_grid, lon_grid)

        # Additional soft edge penalty keeps generated probability blobs mostly
        # inside the plotted CONUS domain while still allowing far-south Texas
        # and Florida events when they are clearly dominant.
        south_edge = np.clip((lat_grid - 26.5) / 4.0, 0.15, 1.0)
        land_mult = land_mult * south_edge
        land_mult = land_mult * _southern_border_multiplier(lat_grid, lon_grid)
        tie_mult = _focus_tiebreak_multiplier(lat_grid, lon_grid)
        structure_mult = _focus_structure_multiplier(lat_grid, lon_grid, td_F, cape_arr, shear_arr)
    else:
        land_mult = 1.0
        tie_mult = 1.0
        structure_mult = 1.0

    # Primary severe focus should favor organized CAPE/shear overlap. Very
    # high CAPE with weak flow can still deserve TSTM/MRGL secondary contours,
    # but it should not outrank a stronger sheared warm sector.
    organized_shear_mult = np.clip((shear_arr - 12.0) / 35.0, 0.20, 1.25)
    score = (cape_arr / 2000.0) * (shear_arr / 30.0) * organized_shear_mult * (moist / 15.0) * cap_mult * land_mult
    score = np.where(np.isfinite(score), score, 0.0)
    focus_score = np.where(score > 0, score * tie_mult * structure_mult, 0.0)

    if not np.isfinite(focus_score).any() or focus_score.max() <= 0:
        return dict(
            label="Central Plains (default)",
            centerLat=36.0,
            centerLon=-98.0,
            bbox=[-104.0, 32.0, -94.0, 41.0],
            states=["OK", "KS", "TX", "AR", "MO"],
        )

    if lats.ndim == 1 and norm_lons.ndim == 1:
        idx = np.unravel_index(np.argmax(focus_score), focus_score.shape)
        center_lat = float(lats[idx[0]])
        center_lon = float(norm_lons[idx[1]])
    else:
        idx = np.unravel_index(np.argmax(focus_score), focus_score.shape)
        center_lat = float(np.asarray(lats)[idx])
        center_lon = float(norm_lons[idx])

    if -98.5 <= center_lon <= -81.0:
        center_lat = max(center_lat, float(_gulf_min_land_lat(np.asarray(center_lon))) + 0.35)
    if 25.0 <= center_lat <= 46.0:
        center_lon = min(center_lon, float(_atlantic_max_land_lon(np.asarray(center_lat))) - 0.35)

    bbox = [center_lon - 5, center_lat - 3, center_lon + 5, center_lat + 3]
    label = _label_for(center_lat, center_lon)
    states = _states_for(center_lat, center_lon)
    return dict(
        label=label,
        centerLat=center_lat,
        centerLon=center_lon,
        bbox=bbox,
        states=states,
    )


# Very rough bucketing for a friendly region label.
_REGIONS = [
    dict(label="Central Plains",        latRange=(33, 40), lonRange=(-102, -94), states=["OK", "KS", "TX"]),
    dict(label="Southern High Plains",  latRange=(28.5, 33.5), lonRange=(-105, -99), states=["TX", "NM"]),
    dict(label="Southern Plains",       latRange=(28.5, 33.5), lonRange=(-99, -94), states=["TX", "LA"]),
    dict(label="Northern Plains",       latRange=(40, 49), lonRange=(-104, -95), states=["SD", "ND", "NE"]),
    dict(label="Mid-South",             latRange=(33, 38), lonRange=(-94, -86),  states=["AR", "TN", "MS"]),
    dict(label="Midwest",               latRange=(38, 45), lonRange=(-95, -82),  states=["IL", "IA", "IN", "MO"]),
    dict(label="Ohio Valley",           latRange=(36, 42), lonRange=(-90, -78),  states=["KY", "OH", "WV"]),
    dict(label="Southeast",             latRange=(28, 35), lonRange=(-90, -78),  states=["AL", "GA", "FL"]),
    dict(label="Mid-Atlantic",          latRange=(35, 42), lonRange=(-82, -73),  states=["NC", "VA", "MD"]),
    dict(label="Northeast",             latRange=(40, 47), lonRange=(-80, -67),  states=["PA", "NY", "NJ", "MA"]),
    dict(label="Northern Rockies",      latRange=(42, 49), lonRange=(-116, -104), states=["MT", "WY", "ID"]),
    dict(label="Desert Southwest",      latRange=(31, 37), lonRange=(-115, -103), states=["AZ", "NM"]),
    dict(label="Pacific Northwest",     latRange=(42, 49), lonRange=(-125, -116), states=["WA", "OR"]),
]


def _label_for(lat: float, lon: float) -> str:
    for r in _REGIONS:
        a, b = r["latRange"]
        c, d = r["lonRange"]
        if a <= lat <= b and c <= lon <= d:
            return f"{r['label']} (auto-detected)"
    return f"CONUS focus ({lat:.1f}°N {abs(lon):.1f}°W)"


def _states_for(lat: float, lon: float) -> list[str]:
    for r in _REGIONS:
        a, b = r["latRange"]
        c, d = r["lonRange"]
        if a <= lat <= b and c <= lon <= d:
            return list(r["states"])
    return ["US"]
