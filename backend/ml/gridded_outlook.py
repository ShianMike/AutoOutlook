"""Gridded HRRR feature engineering and SPC-style category artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from .features import FEATURE_NAMES
from .inference import predict_ml_hazard_matrix

SPC_RISK_LABELS = ("NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH")

NORMALIZATION_LIMITS: dict[str, tuple[float, float]] = {
    "forecastHour": (0.0, 48.0),
    "mlcape": (0.0, 4500.0),
    "mucape": (0.0, 5000.0),
    "sbcape": (0.0, 4500.0),
    "cin": (-250.0, 0.0),
    "sfcDewpointF": (35.0, 78.0),
    "pwatIn": (0.25, 2.3),
    "lclM": (250.0, 3000.0),
    "moistureDepthM": (700.0, 3800.0),
    "srh01": (0.0, 350.0),
    "srh03": (0.0, 650.0),
    "shear06Kt": (0.0, 90.0),
    "stormRelWindKt": (0.0, 55.0),
    "hgt500": (5200.0, 6000.0),
}


@dataclass
class GriddedFeatures:
    raw: dict[str, np.ndarray]
    normalized: dict[str, np.ndarray]
    matrix: np.ndarray
    shape: tuple[int, int]


def gridded_features_from_fields(
    fields: Mapping[str, np.ndarray],
    forecast_hour: int,
) -> GriddedFeatures:
    """Convert selected HRRR fields into raw and normalized gridded model features."""
    cape = _field(fields, "cape")
    shape = cape.shape
    mlcape = _field(fields, "cape_ml", cape * 0.85)
    mucape = _field(fields, "cape_mu", np.maximum(cape, mlcape))
    cin = _field(fields, "cin_ml", _field(fields, "cin", np.zeros(shape)))
    td2m = _field(fields, "td2m", np.full(shape, 283.15))
    t2m = _field(fields, "t2m", td2m + 8.0)
    pwat = _field(fields, "pwat", np.full(shape, 20.0))
    u10 = _field(fields, "u10", np.zeros(shape))
    v10 = _field(fields, "v10", np.zeros(shape))
    u500 = _field(fields, "u500", u10)
    v500 = _field(fields, "v500", v10)
    hgt500 = _field(fields, "hgt500", np.full(shape, 5700.0))

    shear = np.hypot(u500 - u10, v500 - v10) * 1.9438445
    srh01 = np.clip(_field(fields, "srh01", np.maximum(shear - 15.0, 0.0) * 6.0), 0.0, None)
    srh03 = np.clip(_field(fields, "srh03", srh01 * 1.4), 0.0, None)
    td_f = (td2m - 273.15) * 9.0 / 5.0 + 32.0
    lcl_m = np.clip(125.0 * np.maximum(t2m - td2m, 0.0), 100.0, 3500.0)
    pwat_in = np.clip(pwat / 25.4, 0.0, None)

    raw = {
        "forecastHour": np.full(shape, float(forecast_hour), dtype=float),
        "mlcape": np.clip(mlcape, 0.0, None),
        "mucape": np.clip(mucape, 0.0, None),
        "sbcape": np.clip(cape, 0.0, None),
        "cin": np.minimum(cin, 0.0),
        "sfcDewpointF": td_f,
        "pwatIn": pwat_in,
        "lclM": lcl_m,
        "moistureDepthM": np.maximum(800.0, pwat_in * 1500.0),
        "srh01": srh01,
        "srh03": srh03,
        "shear06Kt": shear,
        "stormRelWindKt": shear * 0.5,
        "hgt500": hgt500,
    }
    raw = {key: _finite(raw_value, _default_for(key)) for key, raw_value in raw.items()}
    normalized = {key: normalize_feature(key, value) for key, value in raw.items()}
    matrix = np.column_stack([raw[name].reshape(-1) for name in FEATURE_NAMES]).astype(float)
    return GriddedFeatures(raw=raw, normalized=normalized, matrix=matrix, shape=shape)


def predict_hazard_grids(features: GriddedFeatures) -> dict[str, np.ndarray] | None:
    matrix_probs = predict_ml_hazard_matrix(features.matrix)
    if matrix_probs is None:
        return None
    return {
        hazard: np.asarray(values, dtype=float).reshape(features.shape)
        for hazard, values in matrix_probs.items()
    }


def category_grid_from_probabilities(
    probabilities: Mapping[str, np.ndarray],
    features: GriddedFeatures,
) -> np.ndarray:
    tornado = _hazard_ord("tornado", np.asarray(probabilities["tornado"], dtype=float))
    hail = _hazard_ord("hail", np.asarray(probabilities["hail"], dtype=float))
    wind = _hazard_ord("wind", np.asarray(probabilities["wind"], dtype=float))
    severe_ord = np.maximum.reduce([tornado, hail, wind])
    tstm_mask = (
        (np.maximum(features.raw["sbcape"], features.raw["mucape"]) >= 100.0)
        & (features.raw["sfcDewpointF"] >= 45.0)
        & (features.raw["cin"] > -250.0)
    )
    return np.where(severe_ord > 0, severe_ord, np.where(tstm_mask, 1, 0)).astype(np.int16)


def risk_polygons_from_grid(
    lats: np.ndarray,
    lons: np.ndarray,
    category_grid: np.ndarray,
    forecast_hour: int,
    valid_time_iso: str,
    min_cells: int = 10,
) -> dict[str, Any]:
    lat_grid, lon_grid = _lat_lon_grid(lats, lons, category_grid.shape)
    features: list[dict[str, Any]] = []
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None

    for ordinal in range(1, len(SPC_RISK_LABELS)):
        mask = np.asarray(category_grid >= ordinal)
        if not np.any(mask):
            continue
        if ndimage is None:
            components = [(mask, int(mask.sum()))]
        else:
            labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
            components = [(labels == idx, int(np.sum(labels == idx))) for idx in range(1, count + 1)]
        for component_idx, (component, cell_count) in enumerate(components):
            if cell_count < min_cells:
                continue
            coords = _component_polygon(lon_grid[component], lat_grid[component])
            if len(coords) < 4:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {
                    "category": SPC_RISK_LABELS[ordinal],
                    "ordinal": ordinal,
                    "forecastHour": forecast_hour,
                    "validTimeISO": valid_time_iso,
                    "component": component_idx,
                    "cellCount": cell_count,
                },
            })
    return {"type": "FeatureCollection", "features": features}


def merge_feature_collections(collections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [feature for collection in collections for feature in collection.get("features", [])],
    }


def probability_tile(
    lats: np.ndarray,
    lons: np.ndarray,
    probabilities: Mapping[str, np.ndarray],
    category_grid: np.ndarray,
    forecast_hour: int,
    valid_time_iso: str,
    stride: int = 4,
) -> dict[str, Any]:
    stride = max(1, int(stride))
    lat_grid, lon_grid = _lat_lon_grid(lats, lons, category_grid.shape)
    rows = slice(None, None, stride)
    cols = slice(None, None, stride)
    cats = category_grid[rows, cols]
    return {
        "forecastHour": forecast_hour,
        "validTimeISO": valid_time_iso,
        "stride": stride,
        "shape": list(cats.shape),
        "lats": _round_nested(lat_grid[rows, cols]),
        "lons": _round_nested(lon_grid[rows, cols]),
        "categoryOrdinal": cats.astype(int).tolist(),
        "categoryLabel": [[SPC_RISK_LABELS[int(value)] for value in row] for row in cats],
        "probabilities": {
            hazard: _round_nested(np.asarray(grid, dtype=float)[rows, cols], digits=4)
            for hazard, grid in probabilities.items()
        },
    }


def feature_stats(features: GriddedFeatures) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for key, values in features.raw.items():
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            stats[key] = {"min": 0.0, "mean": 0.0, "max": 0.0}
            continue
        stats[key] = {
            "min": float(np.nanmin(finite)),
            "mean": float(np.nanmean(finite)),
            "max": float(np.nanmax(finite)),
        }
    return stats


def category_counts(category_grid: np.ndarray) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ordinal, label in enumerate(SPC_RISK_LABELS):
        count = int(np.sum(category_grid == ordinal))
        if count:
            counts[label] = count
    return counts


def normalize_feature(name: str, values: np.ndarray) -> np.ndarray:
    lo, hi = NORMALIZATION_LIMITS.get(name, (0.0, 1.0))
    span = max(1e-6, hi - lo)
    return np.clip((np.asarray(values, dtype=float) - lo) / span, 0.0, 1.0)


def _field(fields: Mapping[str, np.ndarray], key: str, default: np.ndarray | float | None = None) -> np.ndarray:
    if key in fields:
        return np.asarray(fields[key], dtype=float)
    if default is None:
        raise KeyError(key)
    if isinstance(default, np.ndarray):
        return np.asarray(default, dtype=float)
    first = np.asarray(next(iter(fields.values())), dtype=float)
    return np.full(first.shape, float(default), dtype=float)


def _finite(values: np.ndarray, default: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return np.where(np.isfinite(arr), arr, default)


def _default_for(name: str) -> float:
    defaults = {
        "sfcDewpointF": 50.0,
        "pwatIn": 0.8,
        "lclM": 1500.0,
        "moistureDepthM": 1500.0,
        "hgt500": 5700.0,
    }
    return defaults.get(name, 0.0)


def _hazard_ord(hazard: str, probability: np.ndarray) -> np.ndarray:
    if hazard == "tornado":
        thresholds = ((0.30, 6), (0.15, 5), (0.10, 4), (0.05, 3), (0.02, 2))
    else:
        thresholds = ((0.60, 6), (0.45, 5), (0.30, 4), (0.15, 3), (0.05, 2))
    out = np.zeros_like(probability, dtype=np.int16)
    for threshold, ordinal in thresholds:
        out = np.where((probability >= threshold) & (out < ordinal), ordinal, out)
    return out


def _lat_lon_grid(lats: np.ndarray, lons: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)
    else:
        lat_grid, lon_grid = lat_arr, lon_arr
    if lat_grid.shape != shape or lon_grid.shape != shape:
        raise ValueError(f"lat/lon grid shape mismatch: {lat_grid.shape}, {lon_grid.shape}, expected {shape}")
    return lat_grid, lon_grid


def _component_polygon(lons: np.ndarray, lats: np.ndarray) -> list[list[float]]:
    points = np.column_stack([np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)])
    points = points[np.isfinite(points).all(axis=1)]
    if points.shape[0] < 3:
        return _bbox_polygon(points)
    if points.shape[0] > 1200:
        step = max(1, points.shape[0] // 1200)
        points = points[::step]
    try:
        from scipy.spatial import ConvexHull
        hull = ConvexHull(points)
        ring = points[hull.vertices]
        coords = [[round(float(lon), 4), round(float(lat), 4)] for lon, lat in ring]
    except Exception:
        return _bbox_polygon(points)
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _bbox_polygon(points: np.ndarray) -> list[list[float]]:
    if points.size == 0:
        return []
    min_lon = float(np.nanmin(points[:, 0]))
    max_lon = float(np.nanmax(points[:, 0]))
    min_lat = float(np.nanmin(points[:, 1]))
    max_lat = float(np.nanmax(points[:, 1]))
    if min_lon == max_lon:
        min_lon -= 0.05
        max_lon += 0.05
    if min_lat == max_lat:
        min_lat -= 0.05
        max_lat += 0.05
    coords = [
        [round(min_lon, 4), round(min_lat, 4)],
        [round(max_lon, 4), round(min_lat, 4)],
        [round(max_lon, 4), round(max_lat, 4)],
        [round(min_lon, 4), round(max_lat, 4)],
    ]
    coords.append(coords[0])
    return coords


def _round_nested(values: np.ndarray, digits: int = 3) -> list[list[float]]:
    return np.round(np.asarray(values, dtype=float), digits).tolist()
