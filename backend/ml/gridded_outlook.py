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


@dataclass
class ProbabilityCapResult:
    probabilities: dict[str, np.ndarray]
    report: dict[str, Any]


@dataclass
class CategoryPostProcessResult:
    category_grid: np.ndarray
    report: dict[str, Any]


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


def apply_environmental_probability_caps(
    probabilities: Mapping[str, np.ndarray],
    features: GriddedFeatures,
    model_metadata: Mapping[str, Any] | None = None,
) -> ProbabilityCapResult:
    """Cap ML probabilities that are unsupported by the local environment.

    The caps are deliberately conservative because these probabilities feed
    both category generation and frontend hazard displays.
    """
    raw = {hazard: np.clip(np.asarray(values, dtype=float), 0.0, 1.0) for hazard, values in probabilities.items()}
    shape = features.shape
    capped = {hazard: values.copy() for hazard, values in raw.items()}
    reason_counts = {
        "weakInstability": 0,
        "weakKinematics": 0,
        "strongCapOrDryAir": 0,
        "experimentalModel": 0,
    }

    mucape = features.raw["mucape"]
    mlcape = features.raw["mlcape"]
    cin = features.raw["cin"]
    dewpoint = features.raw["sfcDewpointF"]
    lcl = features.raw["lclM"]
    srh01 = features.raw["srh01"]
    srh03 = features.raw["srh03"]
    shear = features.raw["shear06Kt"]
    storm_rel = features.raw["stormRelWindKt"]

    hail_cap = np.ones(shape, dtype=float)
    hail_cap = _cap_where(hail_cap, (mucape < 500.0) | (shear < 25.0), 0.04, reason_counts, "weakInstability")
    hail_cap = _cap_where(hail_cap, (mucape < 1000.0) | (shear < 32.0), 0.14, reason_counts, "weakKinematics")
    hail_cap = _cap_where(hail_cap, (mucape < 1500.0) | (shear < 38.0), 0.29, reason_counts, "weakKinematics")
    hail_cap = _cap_where(hail_cap, (mucape < 2500.0) | (shear < 45.0), 0.44, reason_counts, "weakInstability")
    hail_cap = _cap_where(hail_cap, (mucape < 3200.0) | (shear < 50.0), 0.59, reason_counts, "weakInstability")
    hail_cap = _cap_where(hail_cap, (cin <= -175.0) | (dewpoint < 52.0), 0.14, reason_counts, "strongCapOrDryAir")
    hail_cap = _cap_where(hail_cap, (cin <= -250.0) | (dewpoint < 48.0), 0.04, reason_counts, "strongCapOrDryAir")

    tornado_cap = np.ones(shape, dtype=float)
    tornado_cap = _cap_where(tornado_cap, (srh01 < 75.0) | (lcl > 1800.0) | (mlcape < 500.0), 0.019, reason_counts, "weakKinematics")
    tornado_cap = _cap_where(tornado_cap, (srh01 < 125.0) | (lcl > 1500.0) | (mlcape < 750.0), 0.049, reason_counts, "weakKinematics")
    tornado_cap = _cap_where(tornado_cap, (srh01 < 190.0) | (lcl > 1250.0) | (mlcape < 1000.0), 0.099, reason_counts, "weakKinematics")
    tornado_cap = _cap_where(
        tornado_cap,
        (srh03 < 150.0) | (shear < 30.0) | (dewpoint < 55.0) | (cin <= -200.0),
        0.049,
        reason_counts,
        "weakKinematics",
    )

    wind_cap = np.ones(shape, dtype=float)
    wind_cap = _cap_where(wind_cap, (mlcape < 500.0) | (shear < 25.0), 0.04, reason_counts, "weakInstability")
    wind_cap = _cap_where(wind_cap, (mlcape < 1000.0) | (shear < 32.0), 0.14, reason_counts, "weakKinematics")
    wind_cap = _cap_where(
        wind_cap,
        (storm_rel < 24.0) & (shear < 42.0),
        0.14,
        reason_counts,
        "weakKinematics",
    )
    wind_cap = _cap_where(
        wind_cap,
        (storm_rel < 32.0) & (shear < 52.0),
        0.29,
        reason_counts,
        "weakKinematics",
    )
    wind_cap = _cap_where(wind_cap, (dewpoint < 52.0) | (cin <= -225.0), 0.14, reason_counts, "strongCapOrDryAir")

    capped["hail"] = np.minimum(capped["hail"], hail_cap)
    capped["tornado"] = np.minimum(capped["tornado"], tornado_cap)
    capped["wind"] = np.minimum(capped["wind"], wind_cap)

    model_cap = _model_category_cap(model_metadata)
    model_probability_caps = _model_probability_caps(model_cap)
    if any(max_probability < 1.0 for max_probability in model_probability_caps.values()):
        for hazard, max_probability in model_probability_caps.items():
            before = capped[hazard].copy()
            capped[hazard] = np.minimum(capped[hazard], max_probability)
            reason_counts["experimentalModel"] += int(np.sum(before != capped[hazard]))

    report = {
        "environmentalCapsApplied": True,
        "modelCategoryCap": SPC_RISK_LABELS[model_cap],
        "rawProbabilityMax": _probability_max(raw),
        "cappedProbabilityMax": _probability_max(capped),
        "cappedCellCounts": {
            hazard: int(np.sum(np.asarray(raw[hazard]) > np.asarray(capped[hazard])))
            for hazard in raw
        },
        "downgradedCells": reason_counts,
    }
    return ProbabilityCapResult(capped, report)


def apply_category_probability_ceiling(
    probabilities: Mapping[str, np.ndarray],
    category_grid: np.ndarray,
) -> ProbabilityCapResult:
    """Keep displayed hazard probabilities consistent with final risk bands."""
    grid = np.asarray(category_grid, dtype=np.int16)
    capped: dict[str, np.ndarray] = {}
    capped_counts: dict[str, int] = {}
    for hazard, values in probabilities.items():
        arr = np.clip(np.asarray(values, dtype=float), 0.0, 1.0)
        cap = _category_probability_cap_grid(hazard, grid)
        capped_values = np.minimum(arr, cap)
        capped[hazard] = capped_values
        capped_counts[hazard] = int(np.sum(arr > capped_values))
    return ProbabilityCapResult(capped, {
        "categoryConsistencyCapsApplied": True,
        "categoryConsistencyCappedCellCounts": capped_counts,
        "categoryConsistencyProbabilityMax": _probability_max(capped),
    })


def apply_offshore_probability_suppression(
    probabilities: Mapping[str, np.ndarray],
    lats: np.ndarray,
    lons: np.ndarray,
) -> ProbabilityCapResult:
    """Remove severe hazard probabilities over open Gulf/Atlantic water."""
    first = next(iter(probabilities.values()))
    shape = np.asarray(first).shape
    lat_grid, lon_grid = _lat_lon_grid(lats, lons, shape)
    masks = _strict_offshore_masks(lat_grid, lon_grid)
    offshore_mask = np.zeros(shape, dtype=bool)
    for mask in masks.values():
        offshore_mask |= mask

    capped: dict[str, np.ndarray] = {}
    capped_counts: dict[str, int] = {}
    for hazard, values in probabilities.items():
        arr = np.clip(np.asarray(values, dtype=float), 0.0, 1.0)
        capped_values = np.where(offshore_mask, 0.0, arr)
        capped[hazard] = capped_values
        capped_counts[hazard] = int(np.sum(arr > capped_values))

    return ProbabilityCapResult(capped, {
        "offshoreProbabilitySuppressionApplied": True,
        "offshoreProbabilitySuppressedCells": {
            name: int(np.sum(mask))
            for name, mask in masks.items()
        },
        "offshoreProbabilitySuppressedHazardCells": capped_counts,
        "offshoreSuppressedProbabilityMax": _probability_max(capped),
    })


def category_grid_from_probabilities(
    probabilities: Mapping[str, np.ndarray],
    features: GriddedFeatures,
    model_metadata: Mapping[str, Any] | None = None,
) -> np.ndarray:
    tornado = _hazard_ord("tornado", np.asarray(probabilities["tornado"], dtype=float))
    hail = _hazard_ord("hail", np.asarray(probabilities["hail"], dtype=float))
    wind = _hazard_ord("wind", np.asarray(probabilities["wind"], dtype=float))
    severe_ord = np.maximum.reduce([tornado, hail, wind])
    organized_severe_mask = (
        (features.raw["mucape"] >= 500.0)
        & (features.raw["sfcDewpointF"] >= 50.0)
        & (features.raw["shear06Kt"] >= 25.0)
        & (features.raw["cin"] > -225.0)
    )
    severe_kinematic_mask = (
        (features.raw["shear06Kt"] >= 35.0)
        & (
            (features.raw["stormRelWindKt"] >= 24.0)
            | (features.raw["srh01"] >= 75.0)
            | (features.raw["srh03"] >= 150.0)
            | ((features.raw["shear06Kt"] >= 50.0) & (features.raw["mucape"] >= 1000.0))
        )
    )
    significant_severe_mask = (
        (features.raw["mucape"] >= 1250.0)
        & (features.raw["sfcDewpointF"] >= 55.0)
        & (features.raw["shear06Kt"] >= 35.0)
        & (features.raw["cin"] > -175.0)
    )
    significant_kinematic_mask = (
        (features.raw["shear06Kt"] >= 40.0)
        & (
            (features.raw["stormRelWindKt"] >= 30.0)
            | (features.raw["srh01"] >= 125.0)
            | (features.raw["srh03"] >= 220.0)
            | ((features.raw["shear06Kt"] >= 55.0) & (features.raw["mucape"] >= 1750.0))
        )
    )
    high_end_mask = (
        (features.raw["mucape"] >= 2200.0)
        & (features.raw["sfcDewpointF"] >= 59.0)
        & (features.raw["shear06Kt"] >= 45.0)
        & (features.raw["cin"] > -150.0)
    )
    severe_ord = np.where(organized_severe_mask, severe_ord, 0)
    severe_ord = np.where(severe_kinematic_mask, severe_ord, np.minimum(severe_ord, 2))
    severe_ord = np.where(
        significant_severe_mask & significant_kinematic_mask,
        severe_ord,
        np.minimum(severe_ord, 3),
    )
    severe_ord = np.where(
        high_end_mask & significant_kinematic_mask,
        severe_ord,
        np.minimum(severe_ord, 5),
    )
    category_cap = _model_category_cap(model_metadata)
    severe_ord = np.minimum(severe_ord, category_cap)
    tstm_mask = (
        (np.maximum(features.raw["sbcape"], features.raw["mucape"]) >= 250.0)
        & (features.raw["sfcDewpointF"] >= 48.0)
        & (features.raw["cin"] > -225.0)
    )
    return np.where(severe_ord > 0, severe_ord, np.where(tstm_mask, 1, 0)).astype(np.int16)


def postprocess_category_grid(
    category_grid: np.ndarray,
    probabilities: Mapping[str, np.ndarray],
    features: GriddedFeatures,
    lats: np.ndarray | None = None,
    lons: np.ndarray | None = None,
) -> CategoryPostProcessResult:
    """Remove noisy category islands and add conservative category buffers."""
    grid = np.asarray(category_grid, dtype=np.int16).copy()
    original = grid.copy()
    removed_components = 0
    downgraded = {
        "weakInstability": 0,
        "weakKinematics": 0,
        "isolatedComponent": 0,
        "coastalOffshore": 0,
        "gulfOfMexico": 0,
        "floridaGulf": 0,
        "atlanticOcean": 0,
        "southTexasGulfCoast": 0,
        "texasMexicoBorder": 0,
        "missingCategoryBuffer": 0,
    }
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None

    lat_grid = lon_grid = None
    land_mask = None
    gulf_offshore_mask = None
    florida_gulf_mask = None
    atlantic_offshore_mask = None
    south_texas_gulf_mask = None
    texas_mexico_border_mask = None
    if lats is not None and lons is not None:
        lat_grid, lon_grid = _lat_lon_grid(lats, lons, grid.shape)
        land_mask = _rough_conus_land_mask(lat_grid, lon_grid)
        offshore_masks = _strict_offshore_masks(lat_grid, lon_grid)
        gulf_offshore_mask = offshore_masks["gulfOfMexico"]
        florida_gulf_mask = offshore_masks["floridaGulf"]
        atlantic_offshore_mask = offshore_masks["atlanticOcean"]
        south_texas_gulf_mask = offshore_masks["southTexasGulfCoast"]
        texas_mexico_border_mask = _strict_category_cap_masks(lat_grid, lon_grid)["texasMexicoBorder"]

    if ndimage is not None:
        organized = _organized_support_mask(features)
        for ordinal in range(len(SPC_RISK_LABELS) - 1, 0, -1):
            mask = grid == ordinal
            if not np.any(mask):
                continue
            labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
            for component_id in range(1, count + 1):
                component = labels == component_id
                cell_count = int(np.sum(component))
                target = ordinal
                reason: str | None = None
                min_cells = _min_component_cells(ordinal)
                if cell_count < min_cells:
                    target = 0 if ordinal == 1 else ordinal - 1
                    reason = "isolatedComponent"
                    removed_components += 1
                elif ordinal >= 3 and cell_count < 20 and not _has_adjacent_support(grid, component, max(1, ordinal - 1), ndimage):
                    target = ordinal - 1
                    reason = "missingCategoryBuffer"
                elif ordinal >= 4 and not _component_has_significant_support(features, component):
                    target = SPC_RISK_LABELS.index("SLGT")
                    reason = "weakKinematics"
                elif ordinal >= 5 and not _component_has_high_end_support(features, component):
                    target = SPC_RISK_LABELS.index("ENH")
                    reason = "weakInstability"
                if florida_gulf_mask is not None and ordinal >= 2:
                    florida_gulf_fraction = float(np.mean(florida_gulf_mask[component]))
                    if florida_gulf_fraction >= 0.20:
                        florida_gulf_cap = (
                            SPC_RISK_LABELS.index("SLGT")
                            if _component_has_high_end_support(features, component)
                            else SPC_RISK_LABELS.index("MRGL")
                        )
                        if florida_gulf_cap < target:
                            target = florida_gulf_cap
                            reason = "floridaGulf"
                if gulf_offshore_mask is not None and ordinal >= 2:
                    gulf_fraction = float(np.mean(gulf_offshore_mask[component]))
                    if gulf_fraction >= 0.20:
                        gulf_cap = (
                            SPC_RISK_LABELS.index("SLGT")
                            if _component_has_high_end_support(features, component)
                            else SPC_RISK_LABELS.index("MRGL")
                        )
                        if gulf_cap < target:
                            target = gulf_cap
                            reason = "gulfOfMexico"
                if atlantic_offshore_mask is not None and ordinal >= 2:
                    atlantic_fraction = float(np.mean(atlantic_offshore_mask[component]))
                    if atlantic_fraction >= 0.20:
                        atlantic_cap = (
                            SPC_RISK_LABELS.index("SLGT")
                            if _component_has_high_end_support(features, component)
                            else SPC_RISK_LABELS.index("MRGL")
                        )
                        if atlantic_cap < target:
                            target = atlantic_cap
                            reason = "atlanticOcean"
                if land_mask is not None and ordinal >= 3:
                    land_fraction = float(np.mean(land_mask[component]))
                    if land_fraction < 0.35 and not _component_has_high_end_support(features, component):
                        coastal_cap = max(ordinal - 1, 0)
                        if coastal_cap < target:
                            target = coastal_cap
                            reason = "coastalOffshore"
                if target < ordinal:
                    grid[component] = target
                    if reason is not None:
                        downgraded[reason] += cell_count

        for ordinal in range(len(SPC_RISK_LABELS) - 1, 2, -1):
            high_mask = grid >= ordinal
            if not np.any(high_mask):
                continue
            buffer_mask = ndimage.binary_dilation(high_mask, structure=np.ones((3, 3), dtype=bool), iterations=1)
            buffer_mask &= ~high_mask
            buffer_mask &= organized
            buffer_mask &= grid < ordinal - 1
            grid[buffer_mask] = ordinal - 1

        if gulf_offshore_mask is not None:
            _force_offshore_none(grid, gulf_offshore_mask, downgraded, "gulfOfMexico")
        if florida_gulf_mask is not None:
            _force_offshore_none(grid, florida_gulf_mask, downgraded, "floridaGulf")
        if atlantic_offshore_mask is not None:
            _force_offshore_none(grid, atlantic_offshore_mask, downgraded, "atlanticOcean")
        if south_texas_gulf_mask is not None:
            _force_offshore_none(grid, south_texas_gulf_mask, downgraded, "southTexasGulfCoast")
        if texas_mexico_border_mask is not None:
            _cap_category_at_most(
                grid,
                texas_mexico_border_mask,
                SPC_RISK_LABELS.index("MRGL"),
                downgraded,
                "texasMexicoBorder",
            )

    report = {
        "morphologicalSmoothingApplied": ndimage is not None,
        "exactBandsGenerated": True,
        "removedComponents": removed_components,
        "downgradedCells": downgraded,
        "categoryCountsBeforePostprocess": category_counts(original),
        "categoryCountsAfterPostprocess": category_counts(grid),
    }
    return CategoryPostProcessResult(grid.astype(np.int16), report)


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
        mask = np.asarray(category_grid == ordinal)
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
            rings = _component_polygons(lon_grid, lat_grid, component)
            for ring_idx, coords in enumerate(rings):
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
                        "ring": ring_idx,
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
    cats, tile_lats, tile_lons, tile_probabilities = _block_probability_tile_arrays(
        lat_grid,
        lon_grid,
        category_grid,
        probabilities,
        stride,
    )
    strict_tile_mask = np.zeros(cats.shape, dtype=bool)
    for mask in _strict_offshore_masks(tile_lats, tile_lons).values():
        strict_tile_mask |= mask
    cats = np.where(strict_tile_mask, 0, cats)
    for hazard, grid in tile_probabilities.items():
        tile_probabilities[hazard] = np.where(strict_tile_mask, 0.0, grid)
    regional_cap = SPC_RISK_LABELS.index("MRGL")
    regional_cap_mask = np.zeros(cats.shape, dtype=bool)
    for mask in _strict_category_cap_masks(tile_lats, tile_lons).values():
        regional_cap_mask |= mask
    cats = np.where(regional_cap_mask & (cats > regional_cap), regional_cap, cats)
    for hazard, grid in tile_probabilities.items():
        regional_caps = _category_probability_cap_grid(hazard, np.full(cats.shape, regional_cap, dtype=np.int16))
        tile_probabilities[hazard] = np.where(regional_cap_mask, np.minimum(grid, regional_caps), grid)
    return {
        "forecastHour": forecast_hour,
        "validTimeISO": valid_time_iso,
        "stride": stride,
        "shape": list(cats.shape),
        "lats": _round_nested(tile_lats),
        "lons": _round_nested(tile_lons),
        "categoryOrdinal": cats.astype(int).tolist(),
        "categoryLabel": [[SPC_RISK_LABELS[int(value)] for value in row] for row in cats],
        "probabilities": {
            hazard: _round_nested(grid, digits=4)
            for hazard, grid in tile_probabilities.items()
        },
    }


def _block_probability_tile_arrays(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    category_grid: np.ndarray,
    probabilities: Mapping[str, np.ndarray],
    stride: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    rows = range(0, category_grid.shape[0], stride)
    cols = range(0, category_grid.shape[1], stride)
    out_shape = (len(list(rows)), len(list(cols)))
    cats = np.zeros(out_shape, dtype=np.int16)
    tile_lats = np.zeros(out_shape, dtype=float)
    tile_lons = np.zeros(out_shape, dtype=float)
    tile_probabilities = {
        hazard: np.zeros(out_shape, dtype=float)
        for hazard in probabilities
    }

    for out_row, row_start in enumerate(range(0, category_grid.shape[0], stride)):
        row_end = min(category_grid.shape[0], row_start + stride)
        for out_col, col_start in enumerate(range(0, category_grid.shape[1], stride)):
            col_end = min(category_grid.shape[1], col_start + stride)
            lat_block = np.asarray(lat_grid[row_start:row_end, col_start:col_end], dtype=float)
            lon_block = np.asarray(lon_grid[row_start:row_end, col_start:col_end], dtype=float)
            cat_block = np.asarray(category_grid[row_start:row_end, col_start:col_end], dtype=np.int16)
            cats[out_row, out_col] = int(np.nanmax(cat_block)) if cat_block.size else 0
            tile_lats[out_row, out_col] = float(np.nanmean(lat_block)) if lat_block.size else 0.0
            tile_lons[out_row, out_col] = float(np.nanmean(lon_block)) if lon_block.size else 0.0
            for hazard, grid in probabilities.items():
                block = np.asarray(grid, dtype=float)[row_start:row_end, col_start:col_end]
                tile_probabilities[hazard][out_row, out_col] = float(np.nanmax(block)) if block.size else 0.0
    return cats, tile_lats, tile_lons, tile_probabilities


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


def _cap_where(
    cap: np.ndarray,
    mask: np.ndarray,
    value: float,
    reason_counts: dict[str, int],
    reason: str,
) -> np.ndarray:
    capped = np.minimum(cap, np.where(mask, float(value), cap))
    reason_counts[reason] += int(np.sum(np.asarray(mask) & (cap > value)))
    return capped


def _probability_max(probabilities: Mapping[str, np.ndarray]) -> dict[str, float]:
    return {
        hazard: float(np.nanmax(np.asarray(values, dtype=float))) if np.asarray(values).size else 0.0
        for hazard, values in probabilities.items()
    }


def _model_category_cap(model_metadata: Mapping[str, Any] | None) -> int:
    if not model_metadata:
        return len(SPC_RISK_LABELS) - 1
    quality = model_metadata.get("datasetQuality")
    training_rows = _int_value(model_metadata.get("trainingRows"))
    minimum_rows = 5000
    experimental = False
    status = ""
    if isinstance(quality, Mapping):
        training_rows = max(training_rows, _int_value(quality.get("trainingRows")))
        minimum_rows = max(1, _int_value(quality.get("minimumRecommendedRows"), minimum_rows))
        experimental = bool(quality.get("experimentalOnly"))
        status = str(quality.get("status", "")).lower()
    if experimental or training_rows < minimum_rows:
        return SPC_RISK_LABELS.index("SLGT")
    if not bool(model_metadata.get("productionCapable")) and status not in {"production", "operational"}:
        return SPC_RISK_LABELS.index("ENH")
    return len(SPC_RISK_LABELS) - 1


def _model_probability_caps(category_cap: int) -> dict[str, float]:
    """Convert an ordinal category cap into hazard-specific probability caps."""
    if category_cap <= SPC_RISK_LABELS.index("SLGT"):
        return {"tornado": 0.099, "hail": 0.29, "wind": 0.29}
    if category_cap <= SPC_RISK_LABELS.index("ENH"):
        return {"tornado": 0.149, "hail": 0.44, "wind": 0.44}
    if category_cap <= SPC_RISK_LABELS.index("MDT"):
        return {"tornado": 0.299, "hail": 0.59, "wind": 0.59}
    return {"tornado": 1.0, "hail": 1.0, "wind": 1.0}


def _category_probability_cap_grid(hazard: str, category_grid: np.ndarray) -> np.ndarray:
    """Return per-cell ceilings just below the next higher category threshold."""
    caps_by_ordinal = {
        0: 0.014 if hazard == "tornado" else 0.044,
        1: 0.014 if hazard == "tornado" else 0.044,
        2: 0.044 if hazard == "tornado" else 0.144,
        3: 0.094 if hazard == "tornado" else 0.294,
        4: 0.144 if hazard == "tornado" else 0.444,
        5: 0.294 if hazard == "tornado" else 0.594,
        6: 1.0,
    }
    out = np.ones(np.asarray(category_grid).shape, dtype=float)
    for ordinal, cap in caps_by_ordinal.items():
        out = np.where(category_grid == ordinal, cap, out)
    return out


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _organized_support_mask(features: GriddedFeatures) -> np.ndarray:
    return (
        (np.maximum(features.raw["mucape"], features.raw["mlcape"]) >= 500.0)
        & (features.raw["sfcDewpointF"] >= 50.0)
        & (features.raw["cin"] > -225.0)
        & (features.raw["shear06Kt"] >= 25.0)
    )


def _min_component_cells(ordinal: int) -> int:
    return {
        1: 8,
        2: 7,
        3: 5,
        4: 5,
        5: 4,
        6: 4,
    }.get(int(ordinal), 4)


def _has_adjacent_support(grid: np.ndarray, component: np.ndarray, min_ordinal: int, ndimage: Any) -> bool:
    expanded = ndimage.binary_dilation(component, structure=np.ones((3, 3), dtype=bool), iterations=1)
    ring = expanded & ~component
    return bool(np.any(grid[ring] >= min_ordinal))


def _force_offshore_none(
    grid: np.ndarray,
    mask: np.ndarray,
    downgraded: dict[str, int],
    reason: str,
) -> None:
    target = np.asarray(mask, dtype=bool) & (grid > 0)
    downgraded[reason] += int(np.sum(target))
    grid[target] = 0


def _cap_category_at_most(
    grid: np.ndarray,
    mask: np.ndarray,
    max_ordinal: int,
    downgraded: dict[str, int],
    reason: str,
) -> None:
    target = np.asarray(mask, dtype=bool) & (grid > max_ordinal)
    downgraded[reason] += int(np.sum(target))
    grid[target] = int(max_ordinal)


def _component_has_significant_support(features: GriddedFeatures, component: np.ndarray) -> bool:
    mucape = features.raw["mucape"][component]
    mlcape = features.raw["mlcape"][component]
    shear = features.raw["shear06Kt"][component]
    storm_rel = features.raw["stormRelWindKt"][component]
    srh01 = features.raw["srh01"][component]
    srh03 = features.raw["srh03"][component]
    lcl = features.raw["lclM"][component]
    if mucape.size == 0:
        return False
    instability = (np.nanmax(mucape) >= 1750.0) or (np.nanmax(mlcape) >= 1250.0)
    organized_wind = (np.nanmax(shear) >= 50.0 and np.nanmax(storm_rel) >= 30.0)
    organized_rotation = (
        np.nanmax(srh01) >= 125.0
        and np.nanmax(srh03) >= 200.0
        and np.nanmin(lcl) <= 1500.0
        and np.nanmax(shear) >= 35.0
    )
    return bool(instability and (organized_wind or organized_rotation))


def _component_has_high_end_support(features: GriddedFeatures, component: np.ndarray) -> bool:
    mucape = features.raw["mucape"][component]
    mlcape = features.raw["mlcape"][component]
    shear = features.raw["shear06Kt"][component]
    storm_rel = features.raw["stormRelWindKt"][component]
    srh01 = features.raw["srh01"][component]
    srh03 = features.raw["srh03"][component]
    lcl = features.raw["lclM"][component]
    if mucape.size == 0:
        return False
    high_hail_or_wind = np.nanmax(mucape) >= 2700.0 and np.nanmax(shear) >= 50.0 and np.nanmax(storm_rel) >= 34.0
    high_tornado = (
        np.nanmax(mlcape) >= 1750.0
        and np.nanmax(srh01) >= 190.0
        and np.nanmax(srh03) >= 260.0
        and np.nanmin(lcl) <= 1250.0
        and np.nanmax(shear) >= 40.0
    )
    return bool(high_hail_or_wind or high_tornado)


def _rough_conus_land_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    # Coarse coastline-following polygon used only to damp isolated offshore
    # artifacts. It is intentionally conservative and not a cartographic mask.
    conus_ring = [
        (-124.8, 48.8), (-124.4, 42.0), (-122.8, 38.5), (-118.2, 32.5),
        (-114.7, 32.4), (-111.0, 31.3), (-106.5, 31.7), (-103.0, 29.8),
        (-97.4, 25.9), (-90.5, 29.0), (-85.0, 29.6), (-82.8, 27.8),
        (-81.2, 25.0), (-80.1, 25.0), (-80.0, 26.8), (-80.7, 29.0),
        (-81.2, 31.0), (-77.8, 34.0), (-75.4, 36.5), (-74.0, 40.5),
        (-70.0, 43.7), (-67.0, 45.2), (-70.5, 47.2), (-82.5, 46.0),
        (-95.0, 49.0), (-124.8, 48.8),
    ]
    points_lon = np.asarray(lon_grid, dtype=float)
    points_lat = np.asarray(lat_grid, dtype=float)
    return _points_in_polygon(points_lon, points_lat, conus_ring)


def _interp_anchor(x: np.ndarray, anchors: list[tuple[float, float]]) -> np.ndarray:
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


def _gulf_of_mexico_offshore_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    gulf_domain = (lon_grid >= -98.5) & (lon_grid <= -81.0) & (lat_grid >= 18.0) & (lat_grid <= 31.0)
    return gulf_domain & (lat_grid < (_gulf_min_land_lat(lon_grid) - 0.10))


def _florida_gulf_offshore_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    florida_gulf_domain = (lon_grid >= -87.8) & (lon_grid <= -80.0) & (lat_grid >= 23.0) & (lat_grid <= 31.0)
    west_florida_waters = lat_grid < (_gulf_min_land_lat(lon_grid) - 0.10)
    florida_straits = (lat_grid < 25.1) & (lon_grid >= -83.5) & (lon_grid <= -80.0)
    return florida_gulf_domain & (west_florida_waters | florida_straits)


def _atlantic_ocean_offshore_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    atlantic_domain = (lat_grid >= 21.0) & (lat_grid <= 46.0) & (lon_grid >= -82.0) & (lon_grid <= -66.0)
    bahamas_south_florida_waters = (
        (lat_grid >= 21.0)
        & (lat_grid < 25.15)
        & (lon_grid > -82.0)
        & (lon_grid < -73.0)
    )
    return (atlantic_domain & (lon_grid > (_atlantic_max_land_lon(lat_grid) + 0.10))) | bahamas_south_florida_waters


def _south_texas_gulf_coast_strict_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    domain = (
        (lat_grid >= 25.0)
        & (lat_grid <= 29.45)
        & (lon_grid >= -98.8)
        & (lon_grid <= -94.4)
    )
    gulf_edge = _gulf_min_land_lat(np.clip(lon_grid, -98.5, -81.0))
    return domain & (lat_grid <= (gulf_edge + 1.55))


def _texas_mexico_border_mrgl_cap_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    corridor = [
        (-106.70, 25.00),
        (-106.70, 32.35),
        (-99.00, 32.35),
        (-96.15, 32.15),
        (-95.55, 31.15),
        (-96.25, 29.00),
        (-97.20, 25.55),
        (-106.70, 25.00),
    ]
    return _points_in_polygon(lon_grid, lat_grid, corridor)


def _strict_offshore_masks(lat_grid: np.ndarray, lon_grid: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "gulfOfMexico": _gulf_of_mexico_offshore_mask(lat_grid, lon_grid),
        "floridaGulf": _florida_gulf_offshore_mask(lat_grid, lon_grid),
        "atlanticOcean": _atlantic_ocean_offshore_mask(lat_grid, lon_grid),
        "southTexasGulfCoast": _south_texas_gulf_coast_strict_mask(lat_grid, lon_grid),
    }


def _strict_category_cap_masks(lat_grid: np.ndarray, lon_grid: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "texasMexicoBorder": _texas_mexico_border_mrgl_cap_mask(lat_grid, lon_grid),
    }


def _points_in_polygon(x: np.ndarray, y: np.ndarray, ring: list[tuple[float, float]]) -> np.ndarray:
    inside = np.zeros(x.shape, dtype=bool)
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        crosses = ((yi > y) != (yj > y))
        denom = yj - yi
        if abs(denom) < 1e-9:
            denom = 1e-9 if denom >= 0.0 else -1e-9
        x_intersect = ((xj - xi) * (y - yi) / denom) + xi
        inside ^= crosses & (x < x_intersect)
        j = i
    return inside


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


def _component_polygons(lon_grid: np.ndarray, lat_grid: np.ndarray, component: np.ndarray) -> list[list[list[float]]]:
    rings = _component_contour_polygons(lon_grid, lat_grid, component)
    if rings:
        return rings
    return [_component_polygon(lon_grid[component], lat_grid[component])]


def _component_contour_polygons(lon_grid: np.ndarray, lat_grid: np.ndarray, component: np.ndarray) -> list[list[list[float]]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    mask = np.pad(np.asarray(component, dtype=float), 1, mode="constant", constant_values=0.0)
    if np.nanmax(mask) < 0.5:
        return []
    fig, ax = plt.subplots(figsize=(1, 1), dpi=40)
    try:
        contours = ax.contour(mask, levels=[0.5])
        segments = contours.allsegs[0] if contours.allsegs else []
    except Exception:
        segments = []
    finally:
        plt.close(fig)

    rings: list[list[list[float]]] = []
    for segment in segments:
        if len(segment) < 4:
            continue
        cols = np.asarray(segment[:, 0], dtype=float) - 1.0
        rows = np.asarray(segment[:, 1], dtype=float) - 1.0
        lons = _interp_grid(lon_grid, rows, cols)
        lats = _interp_grid(lat_grid, rows, cols)
        coords = [[round(float(lon), 4), round(float(lat), 4)] for lon, lat in zip(lons, lats, strict=False)]
        coords = _smooth_ring(coords, iterations=1)
        coords = _normalize_exterior_ring(coords)
        if len(coords) >= 4 and _ring_extent_ok(coords):
            rings.append(coords)
    rings.sort(key=lambda ring: abs(_signed_ring_area(ring[:-1] if ring[0] == ring[-1] else ring)), reverse=True)
    return rings[:4]


def _interp_grid(grid: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    arr = np.asarray(grid, dtype=float)
    rows = np.clip(rows, 0.0, arr.shape[0] - 1.0)
    cols = np.clip(cols, 0.0, arr.shape[1] - 1.0)
    r0 = np.floor(rows).astype(int)
    c0 = np.floor(cols).astype(int)
    r1 = np.clip(r0 + 1, 0, arr.shape[0] - 1)
    c1 = np.clip(c0 + 1, 0, arr.shape[1] - 1)
    dr = rows - r0
    dc = cols - c0
    return (
        arr[r0, c0] * (1.0 - dr) * (1.0 - dc)
        + arr[r1, c0] * dr * (1.0 - dc)
        + arr[r0, c1] * (1.0 - dr) * dc
        + arr[r1, c1] * dr * dc
    )


def _component_polygon(lons: np.ndarray, lats: np.ndarray) -> list[list[float]]:
    points = np.column_stack([np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)])
    points = points[np.isfinite(points).all(axis=1)]
    return _bbox_polygon(points)


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
    return _normalize_exterior_ring(coords)


def _normalize_exterior_ring(coords: list[list[float]]) -> list[list[float]]:
    if len(coords) < 4:
        return coords
    ring = coords[:-1] if coords[0] == coords[-1] else coords
    if _signed_ring_area(ring) > 0:
        ring = list(reversed(ring))
    out = [list(coord) for coord in ring]
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def _smooth_ring(coords: list[list[float]], iterations: int = 1) -> list[list[float]]:
    if len(coords) < 6:
        return coords
    ring = coords[:-1] if coords[0] == coords[-1] else coords
    for _ in range(max(0, iterations)):
        out: list[list[float]] = []
        for idx, a in enumerate(ring):
            b = ring[(idx + 1) % len(ring)]
            out.append([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]])
            out.append([0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]])
        ring = out
    return [[round(float(lon), 4), round(float(lat), 4)] for lon, lat in ring]


def _ring_extent_ok(coords: list[list[float]]) -> bool:
    if len(coords) < 4:
        return False
    lons = [coord[0] for coord in coords]
    lats = [coord[1] for coord in coords]
    return (max(lons) - min(lons)) <= 35.0 and (max(lats) - min(lats)) <= 20.0


def _signed_ring_area(coords: list[list[float]]) -> float:
    area = 0.0
    for idx, (x0, y0) in enumerate(coords):
        x1, y1 = coords[(idx + 1) % len(coords)]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def _round_nested(values: np.ndarray, digits: int = 3) -> list[list[float]]:
    return np.round(np.asarray(values, dtype=float), digits).tolist()
