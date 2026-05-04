"""Post-prediction verification against the current official SPC Day 1 outlook."""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

import numpy as np
import requests

from .gridded_outlook import SPC_RISK_LABELS

SPC_DAY1_URL = "https://www.spc.noaa.gov/products/outlook/day1otlk.html"
GEOJSON_ZIP_RE = re.compile(r'href=["\']([^"\']*day1otlk_[^"\']+-geojson\.zip)["\']', re.IGNORECASE)


def fetch_current_spc_day1_category(
    session: requests.Session | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Fetch current SPC Day 1 categorical GeoJSON without using it as model input."""
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-SPC-verifier/1.0")
    try:
        page = session.get(SPC_DAY1_URL, timeout=30)
        page.raise_for_status()
        match = GEOJSON_ZIP_RE.search(page.text)
        if not match:
            raise ValueError("SPC Day 1 page did not expose a geojson zip link")
        zip_url = urljoin(SPC_DAY1_URL, match.group(1))
        zip_response = session.get(zip_url, timeout=45)
        zip_response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zf:
            cat_name = next(
                name for name in zf.namelist()
                if name.endswith("_cat.nolyr.geojson") or name.endswith("day1otlk_cat.nolyr.geojson")
            )
            category_geojson = json.loads(zf.read(cat_name).decode("utf-8"))

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "spc_day1_cat.geojson").write_text(json.dumps(category_geojson), encoding="utf-8")
            (output_dir / "spc_source.json").write_text(json.dumps({
                "day1Url": SPC_DAY1_URL,
                "geojsonZipUrl": zip_url,
                "fetchedAtISO": _now_iso(),
            }, indent=2), encoding="utf-8")
        return {
            "day1Url": SPC_DAY1_URL,
            "geojsonZipUrl": zip_url,
            "fetchedAtISO": _now_iso(),
            "categoryGeojson": category_geojson,
        }
    finally:
        if own_session:
            session.close()


def compare_prediction_to_spc(
    lats: np.ndarray,
    lons: np.ndarray,
    predicted_category: np.ndarray,
    spc_category_geojson: Mapping[str, Any],
    feature_grids: Mapping[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Compare AutoOutlook categories to official SPC categories on the prediction grid."""
    pred = np.asarray(predicted_category, dtype=int)
    lat_grid, lon_grid = _lat_lon_grid(lats, lons, pred.shape)
    official = official_category_grid(lat_grid, lon_grid, spc_category_geojson)
    valid = (pred > 0) | (official > 0)
    total = int(np.sum(valid))
    same = int(np.sum((pred == official) & valid))
    under = official > pred
    over = pred > official
    summary = {
        "source": "SPC Day 1 categorical outlook",
        "spcValidTimeISO": _first_property(spc_category_geojson, "VALID_ISO"),
        "spcExpireTimeISO": _first_property(spc_category_geojson, "EXPIRE_ISO"),
        "spcIssueTimeISO": _first_property(spc_category_geojson, "ISSUE_ISO"),
        "spcForecaster": _first_property(spc_category_geojson, "FORECASTER"),
        "comparisonGridCells": total,
        "agreementCells": same,
        "agreementFraction": float(same / total) if total else None,
        "underforecastCells": int(np.sum(under)),
        "overforecastCells": int(np.sum(over)),
        "predictedCategories": _category_counts(pred),
        "officialCategories": _category_counts(official),
        "underforecastRegions": _mask_regions(lat_grid, lon_grid, under),
        "overforecastRegions": _mask_regions(lat_grid, lon_grid, over),
        "meteorologicalExplanations": _explanations(pred, official, under, over, feature_grids),
        "leakageGuard": "Official SPC outlook fetched only after AutoOutlook prediction artifacts were generated.",
    }
    return summary


def official_category_grid(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    spc_category_geojson: Mapping[str, Any],
) -> np.ndarray:
    out = np.zeros(lat_grid.shape, dtype=np.int16)
    features = list(spc_category_geojson.get("features", []))
    features.sort(key=lambda feature: _label_ord(str(feature.get("properties", {}).get("LABEL", ""))))
    for feature in features:
        label = str(feature.get("properties", {}).get("LABEL", ""))
        ordinal = _label_ord(label)
        if ordinal <= 0:
            continue
        geometry = feature.get("geometry")
        for idx in np.ndindex(lat_grid.shape):
            if ordinal <= out[idx]:
                continue
            if _contains_geometry(geometry, float(lon_grid[idx]), float(lat_grid[idx])):
                out[idx] = ordinal
    return out


def _contains_geometry(geometry: Mapping[str, Any] | None, lon: float, lat: float) -> bool:
    if not geometry:
        return False
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if geom_type == "Polygon":
        return _contains_polygon(coords, lon, lat)
    if geom_type == "MultiPolygon":
        return any(_contains_polygon(poly, lon, lat) for poly in coords)
    if geom_type == "GeometryCollection":
        return any(_contains_geometry(geom, lon, lat) for geom in geometry.get("geometries", []))
    return False


def _contains_polygon(rings: list[Any], lon: float, lat: float) -> bool:
    if not rings:
        return False
    if not _point_in_ring(lon, lat, rings[0]):
        return False
    return not any(_point_in_ring(lon, lat, ring) for ring in rings[1:])


def _point_in_ring(lon: float, lat: float, ring: list[Any]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _mask_regions(lat_grid: np.ndarray, lon_grid: np.ndarray, mask: np.ndarray) -> list[dict[str, Any]]:
    if not np.any(mask):
        return []
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None
    if ndimage is None:
        components = [(mask, int(np.sum(mask)))]
    else:
        labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
        components = [(labels == idx, int(np.sum(labels == idx))) for idx in range(1, count + 1)]
    regions: list[dict[str, Any]] = []
    for component, cells in components:
        if cells < 3:
            continue
        lats = lat_grid[component]
        lons = lon_grid[component]
        regions.append({
            "cells": cells,
            "bbox": [
                round(float(np.nanmin(lons)), 3),
                round(float(np.nanmin(lats)), 3),
                round(float(np.nanmax(lons)), 3),
                round(float(np.nanmax(lats)), 3),
            ],
            "centerLat": round(float(np.nanmean(lats)), 3),
            "centerLon": round(float(np.nanmean(lons)), 3),
        })
    return sorted(regions, key=lambda item: item["cells"], reverse=True)[:8]


def _explanations(
    pred: np.ndarray,
    official: np.ndarray,
    under: np.ndarray,
    over: np.ndarray,
    feature_grids: Mapping[str, np.ndarray] | None,
) -> list[str]:
    out: list[str] = []
    if np.any(over):
        stats = _feature_sentence(feature_grids, over)
        out.append(
            "Overforecast areas are where AutoOutlook risk exceeds SPC. This usually means the HRRR/XGBoost fields "
            f"are emphasizing local instability, shear, or hail/wind probabilities more aggressively than the human outlook. {stats}"
        )
    if np.any(under):
        stats = _feature_sentence(feature_grids, under)
        out.append(
            "Underforecast areas are where SPC risk exceeds AutoOutlook. This usually points to missed large-scale forcing, "
            f"storm coverage, or forecaster confidence signals that are not fully represented in the selected HRRR fields. {stats}"
        )
    if not out:
        out.append("No category displacement was detected on the comparison grid.")
    if int(np.nanmax(pred)) >= 4 and int(np.nanmax(official)) <= 2:
        out.append(
            "AutoOutlook produced ENH-or-higher risk while SPC stayed MRGL-or-lower; review model calibration before operational use."
        )
    return out


def _feature_sentence(feature_grids: Mapping[str, np.ndarray] | None, mask: np.ndarray) -> str:
    if not feature_grids:
        return "Feature-grid diagnostics were not available."
    parts = []
    for key in ("mucape", "cin", "sfcDewpointF", "shear06Kt", "srh03", "pwatIn"):
        values = np.asarray(feature_grids.get(key), dtype=float) if key in feature_grids else None
        if values is None or values.shape != mask.shape or not np.any(mask):
            continue
        subset = values[mask & np.isfinite(values)]
        if subset.size:
            parts.append(f"{key} mean {float(np.nanmean(subset)):.1f}")
    return "; ".join(parts) + "." if parts else "Feature-grid diagnostics were sparse."


def _category_counts(grid: np.ndarray) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ordinal, label in enumerate(SPC_RISK_LABELS):
        count = int(np.sum(grid == ordinal))
        if count:
            counts[label] = count
    return counts


def _label_ord(label: str) -> int:
    normalized = "MDT" if label == "MOD" else label
    try:
        return SPC_RISK_LABELS.index(normalized)
    except ValueError:
        return 0


def _first_property(collection: Mapping[str, Any], key: str) -> Any:
    for feature in collection.get("features", []):
        props = feature.get("properties", {})
        if key in props:
            return props[key]
    return None


def _lat_lon_grid(lats: np.ndarray, lons: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)
    else:
        lat_grid, lon_grid = lat_arr, lon_arr
    if lat_grid.shape != shape or lon_grid.shape != shape:
        raise ValueError("lat/lon shape mismatch for SPC verification")
    return lat_grid, lon_grid


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
