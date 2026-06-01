"""Gridded HRRR feature engineering and SPC-style category artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Mapping

try:
    from numba import njit
except ImportError:
    def njit(*args, **kwargs):
        def _passthrough(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _passthrough

import numpy as np


from .features import FEATURE_NAMES
from .inference import predict_ml_hazard_matrix

SPC_RISK_LABELS = ("NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH")
_MATPLOTLIB_CONTOUR_LOCK = threading.Lock()
_CATEGORY_VECTORIZATION_METHOD = "marching_squares_cumulative_contours"
_PROBABILITY_VECTORIZATION_METHOD = "marching_squares_probability_contours"
_TORNADO_PROBABILITY_THRESHOLDS = (0.02, 0.05, 0.10, 0.15, 0.30, 0.45, 0.60)
_SEVERE_PROBABILITY_THRESHOLDS = (0.05, 0.15, 0.30, 0.45, 0.60)
_THUNDER_PROBABILITY_THRESHOLDS = (0.10, 0.40, 0.70)
_DISPLAY_BAND_GAP_METERS = 10_000.0
_LOWER_OWNED_BOUNDARY_METERS = 5_000.0
_DISPLAY_BAND_MIN_SUPPORT_METERS = 35_000.0
_DISPLAY_BAND_CRS = "EPSG:5070"
_PROBABILITY_COLORS = {
    "tornado": ("#3b9b3b", "#a87d4f", "#d4ad7c", "#cf2727", "#c43eb1", "#6e0099", "#4b006b"),
    "hail": ("#a87d4f", "#f6c842", "#cf2727", "#c43eb1", "#6e0099"),
    "wind": ("#a87d4f", "#f6c842", "#cf2727", "#c43eb1", "#6e0099"),
    "thunder": ("#c9a279", "#5cdde6", "#ef6055"),
}

# --- Tunable category-calibration constants ---
# Probability ceiling applied to the MRGL-tier environmental cap for hail/wind.
# Cells capped at this value cannot reach SLGT (which requires >= 0.15).
_MRGL_PROBABILITY_CEILING = 0.14
# Minimum 0-6 km bulk shear (kt) required by the severe_kinematic_mask.
# Cells below this threshold are hard-clamped to at most MRGL (ordinal 2).
_SEVERE_KINEMATIC_MIN_SHEAR = 30.0

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
    lats: np.ndarray | None = None,
    lons: np.ndarray | None = None,
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
        "sfcTempF": (t2m - 273.15) * 9.0 / 5.0 + 32.0,
        "u10": u10,
        "v10": v10,
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
    if lons is not None and np.any(lons > 0):
        raw["is_philippines"] = True
    return GriddedFeatures(raw=raw, normalized=normalized, matrix=matrix, shape=shape)


def predict_hazard_grids(features: GriddedFeatures) -> dict[str, np.ndarray] | None:
    matrix_probs = predict_ml_hazard_matrix(features.matrix)
    if matrix_probs is None:
        return None
    return {
        hazard: np.asarray(values, dtype=float).reshape(features.shape)
        for hazard, values in matrix_probs.items()
    }


def _marine_stability_penalty(
    sbcape: np.ndarray,
    mucape: np.ndarray,
    cin: np.ndarray,
    lcl: np.ndarray,
    dewpoint: np.ndarray,
) -> np.ndarray:
    # Stable marine-layer modification: CAPE collapse, theta-e loss, CIN increase, higher LCL.
    penalty = np.ones_like(sbcape, dtype=float)

    # 1. CAPE collapse (undercutting inflow): sbcape is collapsed relative to mucape
    cape_ratio = np.where(mucape > 100.0, sbcape / np.maximum(100.0, mucape), 1.0)
    cape_collapse = (cape_ratio < 0.4) & (sbcape < 300.0)
    penalty = np.where(cape_collapse, penalty * 0.75, penalty)

    # 2. CIN increase (strong cap):
    strong_cap = cin < -100.0
    penalty = np.where(strong_cap, penalty * 0.85, penalty)

    # 3. Higher LCL (dry/cool near-surface inflow / theta-e loss):
    high_lcl = lcl > 1600.0
    penalty = np.where(high_lcl, penalty * 0.85, penalty)

    # 4. Theta-e loss / cool surface:
    cool_surface = dewpoint < 54.0
    penalty = np.where(cool_surface, penalty * 0.90, penalty)

    return np.clip(penalty, 0.65, 1.0)


def _lake_boundary_bonus(
    sbcape: np.ndarray,
    lcl: np.ndarray,
    srh01: np.ndarray,
    dewpoint: np.ndarray,
) -> np.ndarray:
    # Lake-breeze boundary vorticity / low-level shear convergence bonus
    # applies when low-level shear/helicity is enhanced, but boundary layer remains favorable.
    bonus = np.ones_like(sbcape, dtype=float)

    enhanced_vorticity = srh01 >= 175.0
    favorable_lcl = lcl < 1000.0
    strong_instability = sbcape >= 1000.0
    rich_moisture = dewpoint >= 60.0

    bonus_mask = enhanced_vorticity & favorable_lcl & strong_instability & rich_moisture
    bonus = np.where(bonus_mask, bonus * 1.15, bonus)
    return np.clip(bonus, 1.0, 1.15)


def _great_lakes_tornado_modifier(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    sbcape: np.ndarray,
    mucape: np.ndarray,
    cin: np.ndarray,
    lcl: np.ndarray,
    dewpoint: np.ndarray,
    srh01: np.ndarray,
) -> np.ndarray:
    # Great Lakes / Lake Michigan geographic bounding box
    gl_mask = (lon_grid >= -93.0) & (lon_grid <= -75.0) & (lat_grid >= 41.0) & (lat_grid <= 49.0)
    if not np.any(gl_mask):
        return np.ones_like(sbcape, dtype=float)

    penalty = _marine_stability_penalty(sbcape, mucape, cin, lcl, dewpoint)
    bonus = _lake_boundary_bonus(sbcape, lcl, srh01, dewpoint)

    combined = penalty * bonus
    bounded = np.clip(combined, 0.65, 1.15)
    return np.where(gl_mask, bounded, 1.0)


def _detect_plains_regimes(
    lats: np.ndarray | None,
    lons: np.ndarray | None,
    sbcape: np.ndarray,
    mucape: np.ndarray,
    mlcape: np.ndarray,
    cin: np.ndarray,
    dewpoint: np.ndarray,
    t2m_f: np.ndarray,
    u10: np.ndarray,
    v10: np.ndarray,
    srh01: np.ndarray,
    srh03: np.ndarray,
    shear: np.ndarray,
) -> dict[str, np.ndarray]:
    """Detect boundary-focused, EML, and severe convective regimes on the Plains grid."""
    shape = sbcape.shape
    if lats is None or lons is None:
        false_grid = np.zeros(shape, dtype=bool)
        return {
            "plains_mask": false_grid,
            "dryline": false_grid,
            "triple_point": false_grid,
            "warm_front": false_grid,
            "conditional_discrete": false_grid,
            "linear_forcing": false_grid,
            "eml_cap_regime": false_grid,
            "large_hail_setup": false_grid,
        }

    # Southern/Central Plains bounding box (TX/OK/KS/NE)
    plains_mask = (lats >= 25.0) & (lats <= 43.5) & (lons >= -105.0) & (lons <= -93.0)

    # Zonal and horizontal gradients of dewpoint and surface temperature
    if dewpoint.ndim == 2:
        dew_dy, dew_dx = np.gradient(dewpoint)
        grad_dew = np.hypot(dew_dy, dew_dx)
        temp_dy, temp_dx = np.gradient(t2m_f)
        grad_temp = np.hypot(temp_dy, temp_dx)
    else:
        grad_dew = np.zeros(shape, dtype=float)
        grad_temp = np.zeros(shape, dtype=float)

    # 1. Dryline Detection:
    # Significant horizontal dewpoint gradient, dewpoint in transitional range, decent sbcape
    dryline = plains_mask & (grad_dew >= 1.0) & (dewpoint >= 48.0) & (dewpoint <= 68.0) & (sbcape >= 100.0)

    # 2. Triple Point Detection:
    # Intersection of dryline dewpoint gradient and frontal temperature gradient, with moderate low-level helicity
    triple_point = plains_mask & dryline & (grad_temp >= 1.0) & (srh03 >= 100.0)

    # 3. Warm Front Detection:
    # Strong temperature gradient, backed surface winds (easterly component u10 < 0), high low-level SRH, warm moist side
    warm_front = plains_mask & (grad_temp >= 0.8) & (u10 < 0.0) & (srh01 >= 100.0) & (dewpoint >= 55.0)

    # 4. Conditional Discrete Supercell Flag:
    # Upgrades trigger ONLY when ingredients support it:
    # Strong MLCAPE (>=1000 J/kg), deep shear >= 35 kt, strong SRH (>=150), backed winds (u10 < 0), and boundary forcing.
    boundary_forcing = dryline | triple_point | warm_front
    ingredients_ok = (mlcape >= 1000.0) & (shear >= 35.0) & (srh03 >= 150.0) & (u10 < 0.0)
    conditional_discrete = plains_mask & ingredients_ok & boundary_forcing

    # 5. Linear / Cold-Front Dominant forcing regime:
    # Strong temperature gradient, unbacked surface winds (u10 >= 0), moderate MLCAPE and shear
    linear_forcing = plains_mask & (grad_temp >= 1.5) & (u10 >= 0.0) & (mlcape >= 800.0) & (shear >= 30.0)

    # 6. EML Cap Regime:
    # Strong instability and shear with moderate CIN (EML capping inversion)
    eml_cap_regime = plains_mask & (mlcape >= 1500.0) & (shear >= 35.0) & (cin <= -50.0) & (cin >= -250.0)

    # 7. Large Hail supercell setups:
    # Steep midlevel lapse rate proxy (high MLCAPE >= 2000 J/kg) and strong deep-layer shear (>=40 Kt)
    large_hail_setup = plains_mask & (mlcape >= 2000.0) & (shear >= 40.0)

    return {
        "plains_mask": plains_mask,
        "dryline": dryline,
        "triple_point": triple_point,
        "warm_front": warm_front,
        "conditional_discrete": conditional_discrete,
        "linear_forcing": linear_forcing,
        "eml_cap_regime": eml_cap_regime,
        "large_hail_setup": large_hail_setup,
    }


def _detect_dixie_se_regimes(
    lats: np.ndarray | None,
    lons: np.ndarray | None,
    sbcape: np.ndarray,
    mucape: np.ndarray,
    mlcape: np.ndarray,
    cin: np.ndarray,
    dewpoint: np.ndarray,
    t2m_f: np.ndarray,
    u10: np.ndarray,
    v10: np.ndarray,
    srh01: np.ndarray,
    srh03: np.ndarray,
    shear: np.ndarray,
    storm_modes: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Detect HSLC nocturnal, warm-sector discrete, QLCS embedded, and sea-breeze pulse/supercell regimes in Dixie/Southeast."""
    shape = sbcape.shape
    if lats is None or lons is None:
        false_grid = np.zeros(shape, dtype=bool)
        return {
            "dixie_se_mask": false_grid,
            "hslc": false_grid,
            "warm_sector_discrete": false_grid,
            "embedded_qlcs": false_grid,
            "sea_breeze_pulse": false_grid,
            "sea_breeze_supercell": false_grid,
        }

    # Dixie Alley & Southeast Bounding Box
    dixie_se_mask = (lats >= 24.0) & (lats <= 37.0) & (lons >= -95.0) & (lons <= -75.0)

    # Gradients of temperature and dewpoint for boundary detection
    if dewpoint.ndim == 2:
        dew_dy, dew_dx = np.gradient(dewpoint)
        grad_dew = np.hypot(dew_dy, dew_dx)
        temp_dy, temp_dx = np.gradient(t2m_f)
        grad_temp = np.hypot(temp_dy, temp_dx)
    else:
        grad_dew = np.zeros(shape, dtype=float)
        grad_temp = np.zeros(shape, dtype=float)

    # 1. HSLC (High-Shear/Low-CAPE): Dixie Alley specialty
    # Low CAPE but very high wind shear & helicity, rich boundary layer moisture
    hslc = (
        dixie_se_mask
        & (mlcape >= 100.0)
        & (mlcape <= 1000.0)
        & (dewpoint >= 55.0)
        & (shear >= 35.0)
        & ((srh01 >= 150.0) | (srh03 >= 200.0))
    )

    # 2. Warm-Sector Discrete Supercell ahead of QLCS:
    # High shear/helicity warm sector, discrete cells, backed surface winds (easterly component u10 < 0), rich moisture
    warm_sector_discrete = (
        dixie_se_mask
        & storm_modes["discrete_supercell"]
        & (u10 < 0.0)
        & (srh01 >= 150.0)
        & (dewpoint >= 60.0)
    )

    # 3. Embedded QLCS: linear QLCS storms with very strong low-level shear/helicity
    embedded_qlcs = dixie_se_mask & storm_modes["qlcs"] & (srh01 >= 150.0)

    # Florida / Southeast sea-breeze or outflow boundary active:
    boundary_active = dixie_se_mask & ((grad_temp >= 0.8) | (grad_dew >= 0.8))

    # 4. Sea-Breeze/Outflow Pulse Mode:
    # Active boundary, but background kinematics are weak
    sea_breeze_pulse = boundary_active & (shear < 25.0) & (srh01 < 75.0)

    # 5. Sea-Breeze/Outflow Supercellular Mode:
    # Active boundary with strong background kinematics supporting storm organization
    sea_breeze_supercell = boundary_active & (shear >= 30.0) & (srh01 >= 100.0)

    return {
        "dixie_se_mask": dixie_se_mask,
        "hslc": hslc,
        "warm_sector_discrete": warm_sector_discrete,
        "embedded_qlcs": embedded_qlcs,
        "sea_breeze_pulse": sea_breeze_pulse,
        "sea_breeze_supercell": sea_breeze_supercell,
    }


def _detect_northern_regimes(
    lats: np.ndarray | None,
    lons: np.ndarray | None,
    sbcape: np.ndarray,
    mucape: np.ndarray,
    mlcape: np.ndarray,
    cin: np.ndarray,
    dewpoint: np.ndarray,
    lcl: np.ndarray,
    t2m_f: np.ndarray,
    pwat: np.ndarray,
    u10: np.ndarray,
    srh01: np.ndarray,
    srh03: np.ndarray,
    shear: np.ndarray,
    storm_modes: dict[str, np.ndarray],
    forecast_hour: np.ndarray,
) -> dict[str, np.ndarray]:
    """Detect stabilized prior-convection, boundary tornado bonus, High Plains dry high-based, landspout convergence, and Northern elevated/nocturnal MCS modes."""
    shape = sbcape.shape
    if lats is None or lons is None:
        false_grid = np.zeros(shape, dtype=bool)
        return {
            "midwest_mask": false_grid,
            "high_plains_mask": false_grid,
            "northern_plains_mask": false_grid,
            "midwest_stabilized": false_grid,
            "midwest_boundary_enhanced": false_grid,
            "high_plains_high_based": false_grid,
            "steep_lapse_rate_landspout": false_grid,
            "northern_elevated_hail": false_grid,
            "northern_nocturnal_mcs": false_grid,
        }

    # Bounding Boxes
    midwest_mask = (lats >= 38.0) & (lats <= 49.0) & (lons >= -98.0) & (lons <= -80.0)
    high_plains_mask = (lats >= 35.0) & (lats <= 49.0) & (lons >= -108.0) & (lons <= -100.0)
    northern_plains_mask = (lats >= 40.0) & (lats <= 49.0) & (lons >= -115.0) & (lons <= -96.0)

    # Gradients of temperature and dewpoint for boundary detection
    if dewpoint.ndim == 2:
        dew_dy, dew_dx = np.gradient(dewpoint)
        grad_dew = np.hypot(dew_dy, dew_dx)
        temp_dy, temp_dx = np.gradient(t2m_f)
        grad_temp = np.hypot(temp_dy, temp_dx)
    else:
        grad_dew = np.zeros(shape, dtype=float)
        grad_temp = np.zeros(shape, dtype=float)

    # 1. Midwest Prior Convection Stabilization Penalty:
    # collapsed sbcape relative to mucape (sbcape/mucape < 0.3) OR highly capped (cin <= -100), combined with high moisture (indicative of prior storms)
    # Exclude the Great Lakes region which has its own dedicated stability modifier
    gl_mask = (lats >= 41.0) & (lats <= 49.0) & (lons >= -93.0) & (lons <= -75.0)
    stable_cape = sbcape / np.maximum(100.0, mucape) < 0.3
    stable_cap = cin <= -100.0
    moist_convection = (pwat >= 1.2) | (pwat * 1500.0 >= 1500.0)
    midwest_stabilized = midwest_mask & ~gl_mask & (stable_cape | stable_cap) & moist_convection

    # 2. Midwest Boundary-Enhanced Tornado Bonus:
    # Active warm front/outflow boundary, surface based inflow intact (sbcape >= 1000, cin >= -50, lcl <= 1200), strong helicity
    boundary_active = (grad_temp >= 0.8) | (grad_dew >= 0.8)
    inflow_ok = (sbcape >= 1000.0) & (cin >= -50.0) & (lcl <= 1200.0)
    midwest_boundary_enhanced = midwest_mask & boundary_active & inflow_ok & (srh01 >= 150.0)

    # 3. High Plains High-Based storm mode:
    # LCL >= 1800m, subcloud dryness (lcl - moistureDepth >= 300 or dewpoint < 55)
    moisture_depth = np.maximum(800.0, pwat * 1500.0)
    hp_subcloud_dry = (lcl - moisture_depth >= 300.0) | (dewpoint < 55.0)
    high_plains_high_based = high_plains_mask & (lcl >= 1800.0) & hp_subcloud_dry

    # 4. Steep Lapse-Rate Landspout Mode:
    # Weak deep-layer shear, strong convergence (gradient >= 1.0), strong instability (sbcape >= 800), low LCL (<=1000), minimal cap (cin >= -20)
    steep_lapse_rate_landspout = (
        (shear < 20.0)
        & ((grad_temp >= 1.0) | (grad_dew >= 1.0))
        & (sbcape >= 800.0)
        & (lcl <= 1000.0)
        & (cin >= -20.0)
    )

    # 5. Northern Plains Elevated Hail:
    # Elevated convection, strong deep shear
    northern_elevated_hail = northern_plains_mask & storm_modes["elevated"] & (shear >= 35.0)

    # 6. Northern Plains Nocturnal MCS Wind Mode:
    # MCS mode, timing is nocturnal (forecastHour >= 12 or night cycles), decent MLCAPE, moist profile
    nocturnal_timing = forecast_hour >= 12.0
    northern_nocturnal_mcs = northern_plains_mask & storm_modes["mcs"] & nocturnal_timing & (mlcape >= 500.0) & (pwat >= 1.2)

    return {
        "midwest_mask": midwest_mask,
        "high_plains_mask": high_plains_mask,
        "northern_plains_mask": northern_plains_mask,
        "midwest_stabilized": midwest_stabilized,
        "midwest_boundary_enhanced": midwest_boundary_enhanced,
        "high_plains_high_based": high_plains_high_based,
        "steep_lapse_rate_landspout": steep_lapse_rate_landspout,
        "northern_elevated_hail": northern_elevated_hail,
        "northern_nocturnal_mcs": northern_nocturnal_mcs,
    }


def _detect_northeast_west_southwest_regimes(
    lats: np.ndarray | None,
    lons: np.ndarray | None,
    sbcape: np.ndarray,
    mucape: np.ndarray,
    mlcape: np.ndarray,
    cin: np.ndarray,
    dewpoint: np.ndarray,
    lcl: np.ndarray,
    t2m_f: np.ndarray,
    pwat: np.ndarray,
    u10: np.ndarray,
    v10: np.ndarray,
    srh01: np.ndarray,
    srh03: np.ndarray,
    shear: np.ndarray,
    hgt500: np.ndarray,
    storm_modes: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Detect low-CAPE/high-shear, CAD stable wedges, wedge front boundaries, dry microbursts, monsoon flash flood suppressors, and cold-core low-topped convection modes."""
    shape = sbcape.shape
    if lats is None or lons is None:
        false_grid = np.zeros(shape, dtype=bool)
        return {
            "ne_ma_app_mask": false_grid,
            "dsw_imw_mask": false_grid,
            "pnw_ca_gb_mask": false_grid,
            "ne_low_cape_high_shear": false_grid,
            "ne_cad_stable": false_grid,
            "ne_wedge_front": false_grid,
            "dsw_dry_microburst": false_grid,
            "dsw_monsoon_suppressed": false_grid,
            "pnw_cold_core": false_grid,
            "pnw_terrain_forced_clip": false_grid,
        }

    # Bounding Boxes
    ne_ma_app_mask = (lats >= 37.0) & (lats <= 47.5) & (lons >= -83.0) & (lons <= -67.0)
    dsw_imw_mask = (lats >= 31.0) & (lats <= 42.0) & (lons >= -115.0) & (lons <= -103.0)
    pnw_ca_gb_mask = (lats >= 32.0) & (lats <= 49.0) & (lons >= -125.0) & (lons <= -114.0)

    # Gradients of temperature and dewpoint for boundary detection
    if dewpoint.ndim == 2:
        dew_dy, dew_dx = np.gradient(dewpoint)
        grad_dew = np.hypot(dew_dy, dew_dx)
        temp_dy, temp_dx = np.gradient(t2m_f)
        grad_temp = np.hypot(temp_dy, temp_dx)
    else:
        grad_dew = np.zeros(shape, dtype=float)
        grad_temp = np.zeros(shape, dtype=float)

    # 1. Northeast Low-CAPE / High-Shear Severe Mode:
    # Low CAPE but strong shear and helicity, and sufficient moisture
    ne_low_cape_high_shear = (
        ne_ma_app_mask
        & (mlcape >= 100.0)
        & (mlcape <= 800.0)
        & (shear >= 35.0)
        & ((srh01 >= 125.0) | (srh03 >= 180.0))
        & (dewpoint >= 55.0)
    )

    # 2. Northeast Cold-Air Damming (CAD) Stable Wedge:
    # Cool surface temp, easterly wedge component (u10 < 0, v10 < 0), collapsed surface instability relative to elevated
    ne_cad_stable = (
        ne_ma_app_mask
        & (t2m_f <= 65.0)
        & (u10 < 0.0)
        & (v10 < 0.0)
        & (sbcape < 100.0)
        & (mucape >= 300.0)
    )

    # 3. Northeast Wedge Front Boundary Bonus:
    # Strong local gradient near a wedge front boundary, backed winds, strong low-level shear, but surface-based inflow intact
    ne_wedge_front = (
        ne_ma_app_mask
        & ((grad_temp >= 0.8) | (grad_dew >= 0.8))
        & (u10 < 0.0)
        & (srh01 >= 150.0)
        & (sbcape >= 800.0)
        & (cin >= -40.0)
    )

    # 4. Desert Southwest Dry Microburst & Outflow Mode:
    # Extremely high bases, dry subcloud layer, with minimal elevated instability
    moisture_depth = np.maximum(800.0, pwat * 1500.0)
    dsw_dry_subcloud = (lcl - moisture_depth >= 500.0) | (dewpoint <= 52.0)
    dsw_dry_microburst = (
        dsw_imw_mask
        & (lcl >= 2000.0)
        & dsw_dry_subcloud
        & (mucape >= 200.0)
    )

    # 5. Desert Southwest Monsoon Heavy-Rain (Non-Severe Suppressor):
    # Deep monsoonal moisture (high dewpoint, high PWAT) but weak kinematics -> favor TSTM/MRGL, suppress high-end severe
    dsw_monsoon_suppressed = (
        dsw_imw_mask
        & (pwat >= 1.4)
        & (dewpoint >= 60.0)
        & (shear < 20.0)
        & (srh01 < 50.0)
    )

    # 6. Pacific Northwest Cold-Core Low-Topped Convection Mode:
    # Low 500mb heights, cool surface dewpoint, low cloud bases, modest instability, decent low-level helicity/shear
    pnw_cold_core = (
        pnw_ca_gb_mask
        & (hgt500 <= 5550.0)
        & (dewpoint >= 45.0)
        & (dewpoint <= 55.0)
        & (lcl <= 1000.0)
        & (sbcape >= 100.0)
        & (sbcape <= 1000.0)
        & ((srh01 >= 75.0) | (shear >= 25.0))
    )

    # 7. Western Terrain-Forced / Weak Convection Guardrail:
    # Bounded to pnw_ca_gb_mask. Unless thermodynamic instability and vertical wind shear strongly overlap, we cap all hazards.
    pnw_terrain_forced_clip = (
        pnw_ca_gb_mask
        & ~((mlcape >= 500.0) & (shear >= 30.0))
        & ~pnw_cold_core
    )


    return {
        "ne_ma_app_mask": ne_ma_app_mask,
        "dsw_imw_mask": dsw_imw_mask,
        "pnw_ca_gb_mask": pnw_ca_gb_mask,
        "ne_low_cape_high_shear": ne_low_cape_high_shear,
        "ne_cad_stable": ne_cad_stable,
        "ne_wedge_front": ne_wedge_front,
        "dsw_dry_microburst": dsw_dry_microburst,
        "dsw_monsoon_suppressed": dsw_monsoon_suppressed,
        "pnw_cold_core": pnw_cold_core,
        "pnw_terrain_forced_clip": pnw_terrain_forced_clip,
    }


def _classify_storm_modes(
    sbcape: np.ndarray,
    mucape: np.ndarray,
    mlcape: np.ndarray,
    cin: np.ndarray,
    dewpoint: np.ndarray,
    lcl: np.ndarray,
    srh01: np.ndarray,
    srh03: np.ndarray,
    shear: np.ndarray,
    storm_rel: np.ndarray,
    hgt500: np.ndarray,
    pwat: np.ndarray,
) -> dict[str, np.ndarray]:
    """Diagnose storm modes / environments dynamically from grid features."""
    # 1. Discrete Supercell: strong deep shear, clean mlcape, strong storm relative inflow
    discrete_supercell = (shear >= 35.0) & (mlcape >= 800.0) & (storm_rel >= 20.0)

    # 2. Mixed Mode: intermediate shear and instability with moderate inflow
    mixed_mode = (shear >= 25.0) & (shear < 35.0) & (mlcape >= 500.0) & (storm_rel >= 15.0)

    # 3. QLCS: strong low-level shear/helicity, linear deep shear, moderate instability (excluding clean discrete)
    qlcs = (shear >= 30.0) & (srh01 >= 125.0) & (mlcape >= 500.0) & (~discrete_supercell)

    # 4. MCS: highly moist, moderate low-level and deep shear, elevated instability
    mcs = (pwat >= 1.4) & (mucape >= 1000.0) & (shear >= 25.0) & (srh01 >= 50.0)

    # 5. Pulse / Multicell: high instability but weak shear
    pulse = (mucape >= 1000.0) & (shear < 20.0)

    # 6. Elevated Convection: strong instability aloft but stable/capping at surface
    elevated = (mucape >= 500.0) & ((sbcape < 100.0) | (np.where(mucape > 100.0, sbcape / np.maximum(100.0, mucape), 1.0) < 0.2))

    # 7. High-Based Convection: warm-cloud base is very high (LCL >= 1600m)
    high_based = lcl >= 1600.0

    # 8. Landspout (Non-Supercell Tornado): high surface CAPE, low LCL, weak kinematics, minimal capping
    landspout = (sbcape >= 500.0) & (lcl <= 1000.0) & (shear < 25.0) & (srh01 < 75.0) & (cin > -30.0)

    # 9. Tropical Mini-Supercell: extremely moist tropical storm environment with modest CAPE, strong low-level shear & inflow
    tropical = (pwat >= 1.8) & (mlcape >= 100.0) & (mlcape < 1000.0) & (srh01 >= 100.0) & (storm_rel >= 15.0)

    # 10. Cold-Core Convection: low 500mb heights, cool surface, low LCL, modest CAPE
    cold_core = (hgt500 <= 5500.0) & (dewpoint < 55.0) & (lcl <= 800.0) & (mucape >= 100.0) & (mucape < 800.0)

    return {
        "discrete_supercell": discrete_supercell,
        "mixed_mode": mixed_mode,
        "qlcs": qlcs,
        "mcs": mcs,
        "pulse": pulse,
        "elevated": elevated,
        "high_based": high_based,
        "landspout": landspout,
        "tropical": tropical,
        "cold_core": cold_core,
    }


def apply_environmental_probability_caps(
    probabilities: Mapping[str, np.ndarray],
    features: GriddedFeatures,
    model_metadata: Mapping[str, Any] | None = None,
    lats: np.ndarray | None = None,
    lons: np.ndarray | None = None,
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
    hail_cap_cond = (
        ((shear < 30.0) & (mucape < 2000.0))
        | ((mucape < 800.0) & (shear < 40.0))
        | ((mucape < 1200.0) & (shear < 34.0))
    )
    hail_cap = _cap_where(hail_cap, hail_cap_cond, _MRGL_PROBABILITY_CEILING, reason_counts, "weakKinematics")
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
    wind_cap_cond = (
        ((shear < 30.0) & (mlcape < 2000.0))
        | ((mlcape < 800.0) & (shear < 40.0))
        | ((mlcape < 1200.0) & (shear < 34.0))
    )
    wind_cap = _cap_where(wind_cap, wind_cap_cond, _MRGL_PROBABILITY_CEILING, reason_counts, "weakKinematics")
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

    # Set up coordinates for Plains and Great Lakes logic
    if lats is not None and lons is not None:
        lat_grid, lon_grid = _lat_lon_grid(lats, lons, shape)
    else:
        lat_grid, lon_grid = None, None

    # Diagnose Plains severe weather regimes
    plains = _detect_plains_regimes(
        lats=lat_grid,
        lons=lon_grid,
        sbcape=features.raw["sbcape"],
        mucape=mucape,
        mlcape=mlcape,
        cin=cin,
        dewpoint=dewpoint,
        t2m_f=features.raw["sfcTempF"],
        u10=features.raw["u10"],
        v10=features.raw["v10"],
        srh01=srh01,
        srh03=srh03,
        shear=shear,
    )

    # Diagnose storm modes
    modes = _classify_storm_modes(
        sbcape=features.raw["sbcape"],
        mucape=mucape,
        mlcape=mlcape,
        cin=cin,
        dewpoint=dewpoint,
        lcl=lcl,
        srh01=srh01,
        srh03=srh03,
        shear=shear,
        storm_rel=storm_rel,
        hgt500=features.raw["hgt500"],
        pwat=features.raw["pwatIn"],
    )

    # Diagnose Dixie Alley & Southeast severe weather regimes
    dixie_se = _detect_dixie_se_regimes(
        lats=lat_grid,
        lons=lon_grid,
        sbcape=features.raw["sbcape"],
        mucape=mucape,
        mlcape=mlcape,
        cin=cin,
        dewpoint=dewpoint,
        t2m_f=features.raw["sfcTempF"],
        u10=features.raw["u10"],
        v10=features.raw["v10"],
        srh01=srh01,
        srh03=srh03,
        shear=shear,
        storm_modes=modes,
    )

    # Diagnose Northern severe weather regimes
    northern = _detect_northern_regimes(
        lats=lat_grid,
        lons=lon_grid,
        sbcape=features.raw["sbcape"],
        mucape=mucape,
        mlcape=mlcape,
        cin=cin,
        dewpoint=dewpoint,
        lcl=lcl,
        t2m_f=features.raw["sfcTempF"],
        pwat=features.raw["pwatIn"],
        u10=features.raw["u10"],
        srh01=srh01,
        srh03=srh03,
        shear=shear,
        storm_modes=modes,
        forecast_hour=features.raw["forecastHour"],
    )

    # Diagnose Northeast, West, and Desert Southwest severe weather regimes
    new_regions = _detect_northeast_west_southwest_regimes(
        lats=lat_grid,
        lons=lon_grid,
        sbcape=features.raw["sbcape"],
        mucape=mucape,
        mlcape=mlcape,
        cin=cin,
        dewpoint=dewpoint,
        lcl=lcl,
        t2m_f=features.raw["sfcTempF"],
        pwat=features.raw["pwatIn"],
        u10=features.raw["u10"],
        v10=features.raw["v10"],
        srh01=srh01,
        srh03=srh03,
        shear=shear,
        hgt500=features.raw["hgt500"],
        storm_modes=modes,
    )

    philippines_land = np.zeros(shape, dtype=bool)
    philippines_gust_buffer = np.zeros(shape, dtype=bool)
    philippines_hail_buffer = np.zeros(shape, dtype=bool)
    philippines_tornado_buffer = np.zeros(shape, dtype=bool)
    philippines_domain = lat_grid is not None and lon_grid is not None and _is_philippines_grid(lat_grid, lon_grid)
    if philippines_domain and lat_grid is not None and lon_grid is not None:
        features.raw["is_philippines"] = True
        philippines_land = _philippines_activity_land_mask(lat_grid, lon_grid)
        cape_peak = np.maximum(mucape, mlcape)
        tropical_moisture = (dewpoint >= 70.0) & (features.raw["pwatIn"] >= 1.35)
        weak_tropical_pulse = (
            philippines_land
            & tropical_moisture
            & (cape_peak >= 250.0)
            & (cin > -175.0)
            & ((shear < 25.0) | (storm_rel < 18.0))
        )
        organized_tropical = (
            philippines_land
            & tropical_moisture
            & (cape_peak >= 750.0)
            & (shear >= 25.0)
            & (cin > -175.0)
            & ((storm_rel >= 18.0) | (srh03 >= 100.0))
        )
        strong_organized_tropical = (
            organized_tropical
            & (cape_peak >= 1250.0)
            & (shear >= 35.0)
            & ((storm_rel >= 24.0) | (srh03 >= 150.0))
            & (cin > -150.0)
        )
        exceptional_tropical = (
            strong_organized_tropical
            & (cape_peak >= 2200.0)
            & (shear >= 45.0)
            & ((storm_rel >= 30.0) | (srh03 >= 220.0))
            & (cin > -125.0)
        )
        rotating_tropical = (
            strong_organized_tropical
            & (mlcape >= 500.0)
            & (srh01 >= 100.0)
            & (srh03 >= 150.0)
            & (lcl <= 1500.0)
        )

        before_caps = {"tornado": tornado_cap.copy(), "hail": hail_cap.copy(), "wind": wind_cap.copy()}

        tornado_cap = np.where(philippines_land, np.minimum(tornado_cap, 0.019), tornado_cap)
        tornado_cap = np.where(rotating_tropical, np.maximum(tornado_cap, 0.049), tornado_cap)
        tornado_cap = np.where(rotating_tropical & (srh01 >= 150.0) & (lcl <= 1250.0), np.maximum(tornado_cap, 0.099), tornado_cap)

        hail_cap = np.where(philippines_land, np.minimum(hail_cap, 1.0), hail_cap)
        hail_cap = np.where(organized_tropical & (mucape >= 1000.0) & (features.raw["hgt500"] <= 5900.0), np.maximum(hail_cap, 0.149), hail_cap)
        hail_cap = np.where(strong_organized_tropical & (mucape >= 2000.0) & (features.raw["hgt500"] <= 5850.0), np.maximum(hail_cap, 0.299), hail_cap)
        hail_cap = np.where(exceptional_tropical & (mucape >= 2500.0), np.maximum(hail_cap, 0.449), hail_cap)

        wind_cap = np.where(philippines_land, np.minimum(wind_cap, 1.0), wind_cap)
        wind_cap = np.where(weak_tropical_pulse, np.maximum(wind_cap, 0.099), wind_cap)
        wind_cap = np.where(organized_tropical, np.maximum(wind_cap, 0.149), wind_cap)
        wind_cap = np.where(strong_organized_tropical, np.maximum(wind_cap, 0.299), wind_cap)
        wind_cap = np.where(exceptional_tropical, np.maximum(wind_cap, 0.449), wind_cap)

        philippines_gust_buffer = (
            philippines_land
            & (before_caps["wind"] > wind_cap)
        )
        philippines_hail_buffer = (
            philippines_land
            & (before_caps["hail"] > hail_cap)
        )
        philippines_tornado_buffer = (
            philippines_land
            & (before_caps["tornado"] > tornado_cap)
        )



    # Apply storm-mode-aware cap adjustments
    # Landspout: relax tornado cap to 0.049 if active
    tornado_cap = np.where(modes["landspout"] & (tornado_cap < 0.049), 0.049, tornado_cap)

    # Tropical Mini-Supercell: bypass standard instability caps and allow up to 0.099
    tornado_cap = np.where(modes["tropical"] & (tornado_cap < 0.099), 0.099, tornado_cap)

    # Cold-Core Convection: allow up to 0.049 tornado cap, relax wind/hail caps to 0.14
    tornado_cap = np.where(modes["cold_core"] & (tornado_cap < 0.049), 0.049, tornado_cap)
    hail_cap = np.where(modes["cold_core"] & (hail_cap < 0.14), 0.14, hail_cap)
    wind_cap = np.where(modes["cold_core"] & (wind_cap < 0.14), 0.14, wind_cap)

    # Pulse Convection: relax weak-kinematic caps for wind/hail to 0.14, cap tornado to 0.019
    hail_cap = np.where(modes["pulse"] & (hail_cap < 0.14), 0.14, hail_cap)
    wind_cap = np.where(modes["pulse"] & (wind_cap < 0.14), 0.14, wind_cap)
    tornado_cap = np.where(modes["pulse"], np.minimum(tornado_cap, 0.019), tornado_cap)
    hail_cap = np.where(modes["pulse"], np.minimum(hail_cap, 0.14), hail_cap)
    wind_cap = np.where(modes["pulse"], np.minimum(wind_cap, 0.14), wind_cap)

    # Elevated Convection: cap tornado to 0.019 (hail untouched)
    tornado_cap = np.where(modes["elevated"], np.minimum(tornado_cap, 0.019), tornado_cap)

    # High-Based Convection: cap tornado to 0.019
    tornado_cap = np.where(modes["high_based"], np.minimum(tornado_cap, 0.019), tornado_cap)

    # Cold-Core high-end conservation: cap severe hazards to conservative levels
    tornado_cap = np.where(modes["cold_core"], np.minimum(tornado_cap, 0.049), tornado_cap)
    hail_cap = np.where(modes["cold_core"], np.minimum(hail_cap, 0.14), hail_cap)
    wind_cap = np.where(modes["cold_core"], np.minimum(wind_cap, 0.14), wind_cap)

    # Plains Cap Relaxations: EML and moderate CIN boundary setups are not over-penalized
    hail_cap = np.where(plains["conditional_discrete"] & (cin >= -250.0), np.maximum(hail_cap, 0.44), hail_cap)
    tornado_cap = np.where(plains["conditional_discrete"] & (cin >= -250.0) & (mlcape >= 1000.0) & (srh01 >= 125.0), np.maximum(tornado_cap, 0.099), tornado_cap)
    wind_cap = np.where(plains["conditional_discrete"] & (cin >= -250.0), np.maximum(wind_cap, 0.29), wind_cap)

    # Plains Large Hail Setup cap relaxation
    hail_cap = np.where(plains["large_hail_setup"], np.maximum(hail_cap, 0.59), hail_cap)

    # Plains Linear / Cold-Front Dominant forcing cap handling
    tornado_cap = np.where(plains["linear_forcing"], np.minimum(tornado_cap, 0.049), tornado_cap)
    hail_cap = np.where(plains["linear_forcing"] & (hail_cap < 0.29), 0.29, hail_cap)
    wind_cap = np.where(plains["linear_forcing"] & (wind_cap < 0.29), 0.29, wind_cap)

    # Dixie Alley HSLC Cap Relaxation (allow up to 0.099 max tornado in HSLC environments)
    tornado_cap = np.where(dixie_se["hslc"] & (tornado_cap < 0.099), 0.099, tornado_cap)

    # Warm-Sector Discrete Supercell ahead of QLCS cap relaxation (allow up to 0.14 max tornado)
    tornado_cap = np.where(dixie_se["warm_sector_discrete"] & (tornado_cap < 0.14), 0.14, tornado_cap)

    # Florida / Southeast Sea-Breeze Pulse Cap (cap supercellular to 0.019, landspout/waterspout up to 0.049, wind/hail to 0.14)
    sea_breeze_spout = dixie_se["sea_breeze_pulse"] & (features.raw["sbcape"] >= 500.0) & (lcl <= 1000.0) & (cin > -30.0)
    tornado_cap = np.where(dixie_se["sea_breeze_pulse"], np.minimum(tornado_cap, 0.019), tornado_cap)
    tornado_cap = np.where(sea_breeze_spout, np.maximum(tornado_cap, 0.049), tornado_cap)
    hail_cap = np.where(dixie_se["sea_breeze_pulse"], np.minimum(hail_cap, 0.14), hail_cap)
    wind_cap = np.where(dixie_se["sea_breeze_pulse"], np.minimum(wind_cap, 0.14), wind_cap)

    # Midwest Prior Convection Stabilization Cap (cap tornado to 0.019)
    tornado_cap = np.where(northern["midwest_stabilized"], np.minimum(tornado_cap, 0.019), tornado_cap)

    # Midwest Boundary Tornado Bonus (relax tornado cap to 0.14)
    tornado_cap = np.where(northern["midwest_boundary_enhanced"] & (tornado_cap < 0.14), 0.14, tornado_cap)

    # High Plains High-Based Storms Cap (cap tornado to 0.019, relax wind to 0.29, hail to 0.44 if MLCAPE/shear support)
    tornado_cap = np.where(northern["high_plains_high_based"], np.minimum(tornado_cap, 0.019), tornado_cap)
    hp_relax = northern["high_plains_high_based"] & (mlcape >= 1000.0) & (shear >= 30.0)
    hail_cap = np.where(hp_relax & (hail_cap < 0.44), 0.44, hail_cap)
    wind_cap = np.where(hp_relax & (wind_cap < 0.29), 0.29, wind_cap)

    # Steep Lapse-Rate Landspout Mode Cap (relax tornado cap to 0.049)
    tornado_cap = np.where(northern["steep_lapse_rate_landspout"] & (tornado_cap < 0.049), 0.049, tornado_cap)

    # Northern Elevated Convective Hail Cap (relax hail cap to 0.29)
    hail_cap = np.where(northern["northern_elevated_hail"] & (hail_cap < 0.29), 0.29, hail_cap)

    # Northern Nocturnal MCS Wind Cap (relax wind cap to 0.29)
    wind_cap = np.where(northern["northern_nocturnal_mcs"] & (wind_cap < 0.29), 0.29, wind_cap)

    # Northeast Low-CAPE/High-Shear Cap Relaxation
    tornado_cap = np.where(new_regions["ne_low_cape_high_shear"] & (tornado_cap < 0.049), 0.049, tornado_cap)
    favorable_ne_discrete = new_regions["ne_low_cape_high_shear"] & modes["discrete_supercell"]
    tornado_cap = np.where(favorable_ne_discrete & (tornado_cap < 0.099), 0.099, tornado_cap)
    hail_cap = np.where(new_regions["ne_low_cape_high_shear"] & (hail_cap < 0.14), 0.14, hail_cap)
    wind_cap = np.where(new_regions["ne_low_cape_high_shear"] & (wind_cap < 0.29), 0.29, wind_cap)

    # Northeast Cold-Air Damming Stable Wedge Cap (strictly cap tornado, relax elevated hail under strong shear)
    tornado_cap = np.where(new_regions["ne_cad_stable"], np.minimum(tornado_cap, 0.019), tornado_cap)
    hail_cap = np.where(new_regions["ne_cad_stable"] & (shear >= 35.0) & (hail_cap < 0.29), 0.29, hail_cap)

    # Northeast Wedge Front Boundary Bonus (relax tornado cap)
    tornado_cap = np.where(new_regions["ne_wedge_front"] & (tornado_cap < 0.099), 0.099, tornado_cap)

    # Desert Southwest Dry Microburst Cap (cap tornado, relax wind cap)
    tornado_cap = np.where(new_regions["dsw_dry_microburst"], np.minimum(tornado_cap, 0.019), tornado_cap)
    wind_cap = np.where(new_regions["dsw_dry_microburst"] & (wind_cap < 0.29), 0.29, wind_cap)

    # Desert Southwest Monsoon Heavy-Rain Suppressor (strictly cap all severe hazards to prevent moisture false alarms)
    tornado_cap = np.where(new_regions["dsw_monsoon_suppressed"], np.minimum(tornado_cap, 0.01), tornado_cap)
    wind_cap = np.where(new_regions["dsw_monsoon_suppressed"], np.minimum(wind_cap, 0.04), wind_cap)
    hail_cap = np.where(new_regions["dsw_monsoon_suppressed"], np.minimum(hail_cap, 0.04), hail_cap)

    # Pacific Northwest Cold-Core Mode Cap Relaxation
    tornado_cap = np.where(new_regions["pnw_cold_core"] & (tornado_cap < 0.049), 0.049, tornado_cap)
    hail_cap = np.where(new_regions["pnw_cold_core"] & (hail_cap < 0.14), 0.14, hail_cap)
    wind_cap = np.where(new_regions["pnw_cold_core"] & (wind_cap < 0.14), 0.14, wind_cap)

    # Western Terrain-Forced Convection Clip (strictly cap hazards to TSTM/MRGL unless organized)
    tornado_cap = np.where(new_regions["pnw_terrain_forced_clip"], np.minimum(tornado_cap, 0.019), tornado_cap)
    hail_cap = np.where(new_regions["pnw_terrain_forced_clip"], np.minimum(hail_cap, 0.14), hail_cap)
    wind_cap = np.where(new_regions["pnw_terrain_forced_clip"], np.minimum(wind_cap, 0.14), wind_cap)

    # Enforce caps on raw probabilities
    capped["hail"] = np.minimum(capped["hail"], hail_cap)
    capped["tornado"] = np.minimum(capped["tornado"], tornado_cap)
    capped["wind"] = np.minimum(capped["wind"], wind_cap)

    # Apply continuous hazard upgrades directly to calibrated probabilities
    # Discrete Supercell: enhance tornado by +15% if environment is highly sheared and unstable
    favorable_supercell = modes["discrete_supercell"] & (srh01 >= 150.0) & (lcl <= 1200.0) & (features.raw["sbcape"] >= 1000.0)
    capped["tornado"] = np.where(favorable_supercell, np.clip(capped["tornado"] * 1.15, 0.0, 1.0), capped["tornado"])

    # QLCS: enhance wind by +10%, enhance tornado by +5% (with hard ceiling at 0.099)
    capped["wind"] = np.where(modes["qlcs"], np.clip(capped["wind"] * 1.10, 0.0, 1.0), capped["wind"])
    capped["tornado"] = np.where(modes["qlcs"], np.minimum(np.clip(capped["tornado"] * 1.05, 0.0, 1.0), 0.099), capped["tornado"])

    # MCS: enhance wind by +10%
    capped["wind"] = np.where(modes["mcs"], np.clip(capped["wind"] * 1.10, 0.0, 1.0), capped["wind"])

    # Elevated: reduce wind by scale 0.70
    capped["wind"] = np.where(modes["elevated"], np.clip(capped["wind"] * 0.70, 0.0, 1.0), capped["wind"])

    # High-based dry microburst wind enhancement
    dry_microburst = modes["high_based"] & (dewpoint < 58.0)
    capped["wind"] = np.where(dry_microburst, np.clip(capped["wind"] * 1.05, 0.0, 1.0), capped["wind"])

    # Plains Conditional Discrete Supercell Upgrades (+15% tornado, +15% hail)
    capped["tornado"] = np.where(plains["conditional_discrete"], np.clip(capped["tornado"] * 1.15, 0.0, 1.0), capped["tornado"])
    capped["hail"] = np.where(plains["conditional_discrete"], np.clip(capped["hail"] * 1.15, 0.0, 1.0), capped["hail"])

    # Plains Large Hail Setup Boost (+20% hail)
    capped["hail"] = np.where(plains["large_hail_setup"], np.clip(capped["hail"] * 1.20, 0.0, 1.0), capped["hail"])

    # Plains Linear / Cold-Front Dominant Wind & Hail Boost (+10% wind, +10% hail)
    capped["wind"] = np.where(plains["linear_forcing"], np.clip(capped["wind"] * 1.10, 0.0, 1.0), capped["wind"])
    capped["hail"] = np.where(plains["linear_forcing"], np.clip(capped["hail"] * 1.10, 0.0, 1.0), capped["hail"])

    # Dixie Alley HSLC LLJ Boost (+15% tornado if srh01 >= 200)
    capped["tornado"] = np.where(dixie_se["hslc"] & (srh01 >= 200.0), np.clip(capped["tornado"] * 1.15, 0.0, 1.0), capped["tornado"])

    # Dixie Alley Warm-Sector Discrete Supercell Boost (+20% tornado)
    capped["tornado"] = np.where(dixie_se["warm_sector_discrete"], np.clip(capped["tornado"] * 1.20, 0.0, 1.0), capped["tornado"])

    # Midwest Prior Convection Stabilization Penalty (-30% all hazards)
    capped["tornado"] = np.where(northern["midwest_stabilized"], np.clip(capped["tornado"] * 0.70, 0.0, 1.0), capped["tornado"])
    capped["hail"] = np.where(northern["midwest_stabilized"], np.clip(capped["hail"] * 0.70, 0.0, 1.0), capped["hail"])
    capped["wind"] = np.where(northern["midwest_stabilized"], np.clip(capped["wind"] * 0.70, 0.0, 1.0), capped["wind"])

    # Midwest Boundary-Enhanced Tornado Bonus (+20% tornado)
    capped["tornado"] = np.where(northern["midwest_boundary_enhanced"], np.clip(capped["tornado"] * 1.20, 0.0, 1.0), capped["tornado"])

    # High Plains High-Based Wind & Hail Boost (+15% wind, +10% hail)
    capped["wind"] = np.where(northern["high_plains_high_based"], np.clip(capped["wind"] * 1.15, 0.0, 1.0), capped["wind"])
    capped["hail"] = np.where(northern["high_plains_high_based"], np.clip(capped["hail"] * 1.10, 0.0, 1.0), capped["hail"])

    # Northern Nocturnal MCS Wind Boost (+10% wind)
    capped["wind"] = np.where(northern["northern_nocturnal_mcs"], np.clip(capped["wind"] * 1.10, 0.0, 1.0), capped["wind"])

    # Northeast CAD Stable Wedge Penalty (-30% wind and tornado)
    capped["tornado"] = np.where(new_regions["ne_cad_stable"], np.clip(capped["tornado"] * 0.70, 0.0, 1.0), capped["tornado"])
    capped["wind"] = np.where(new_regions["ne_cad_stable"], np.clip(capped["wind"] * 0.70, 0.0, 1.0), capped["wind"])

    # Northeast Wedge Front Boundary Bonus (+20% tornado)
    capped["tornado"] = np.where(new_regions["ne_wedge_front"], np.clip(capped["tornado"] * 1.20, 0.0, 1.0), capped["tornado"])

    # Desert Southwest Dry Microburst / Outflow Wind Boost (+15% wind)
    capped["wind"] = np.where(new_regions["dsw_dry_microburst"], np.clip(capped["wind"] * 1.15, 0.0, 1.0), capped["wind"])


    # Great Lakes / Lake Michigan tornado modifier
    if lat_grid is not None and lon_grid is not None:
        sbcape_gl = features.raw["sbcape"]
        gl_mod = _great_lakes_tornado_modifier(
            lat_grid,
            lon_grid,
            sbcape_gl,
            mucape,
            cin,
            lcl,
            dewpoint,
            srh01,
        )
        capped["tornado"] = np.clip(capped["tornado"] * gl_mod, 0.0, 1.0)
        gl_penalized_cells = int(np.sum(gl_mod < 1.0))
        gl_enhanced_cells = int(np.sum(gl_mod > 1.0))
    else:
        gl_penalized_cells = 0
        gl_enhanced_cells = 0

    model_cap = _model_category_cap(model_metadata)
    model_probability_caps = _model_probability_caps(model_cap)
    if any(max_probability < 1.0 for max_probability in model_probability_caps.values()):
        for hazard, max_probability in model_probability_caps.items():
            before = capped[hazard].copy()
            capped[hazard] = np.minimum(capped[hazard], max_probability)
            reason_counts["experimentalModel"] += int(np.sum(before != capped[hazard]))

    report = {
        "environmentalCapsApplied": True,
        "philippinesRegionalCalibrationApplied": philippines_domain,
        "philippinesGustBufferCells": int(np.sum(philippines_gust_buffer)) if philippines_domain else 0,
        "modelCategoryCap": SPC_RISK_LABELS[model_cap],
        "rawProbabilityMax": _probability_max(raw),
        "cappedProbabilityMax": _probability_max(capped),
        "cappedCellCounts": {
            hazard: int(np.sum(np.asarray(raw[hazard]) > np.asarray(capped[hazard])))
            for hazard in raw
        },
        "downgradedCells": reason_counts,
        "greatLakesTornadoModifierApplied": lats is not None and lons is not None,
        "greatLakesPenalizedCells": gl_penalized_cells,
        "greatLakesEnhancedCells": gl_enhanced_cells,
        "discreteSupercellCells": int(np.sum(modes["discrete_supercell"])),
        "qlcsCells": int(np.sum(modes["qlcs"])),
        "mcsCells": int(np.sum(modes["mcs"])),
        "pulseCells": int(np.sum(modes["pulse"])),
        "elevatedCells": int(np.sum(modes["elevated"])),
        "highBasedCells": int(np.sum(modes["high_based"])),
        "landspoutCells": int(np.sum(modes["landspout"])),
        "tropicalMiniSupercellCells": int(np.sum(modes["tropical"])),
        "coldCoreCells": int(np.sum(modes["cold_core"])),
        "plainsDrylineCells": int(np.sum(plains["dryline"])),
        "plainsTriplePointCells": int(np.sum(plains["triple_point"])),
        "plainsWarmFrontCells": int(np.sum(plains["warm_front"])),
        "plainsDiscreteSupercellCells": int(np.sum(plains["conditional_discrete"])),
        "plainsLargeHailSetups": int(np.sum(plains["large_hail_setup"])),
        "plainsLinearForcingCells": int(np.sum(plains["linear_forcing"])),
        "dixieSeHslcCells": int(np.sum(dixie_se["hslc"])),
        "dixieSeWarmSectorDiscreteCells": int(np.sum(dixie_se["warm_sector_discrete"])),
        "dixieSeSeaBreezePulseCells": int(np.sum(dixie_se["sea_breeze_pulse"])),
        "dixieSeSeaBreezeSupercellCells": int(np.sum(dixie_se["sea_breeze_supercell"])),
        "dixieSeEmbeddedQlcsCells": int(np.sum(dixie_se["embedded_qlcs"])),
        "midwestStabilizedCells": int(np.sum(northern["midwest_stabilized"])),
        "midwestBoundaryEnhancedCells": int(np.sum(northern["midwest_boundary_enhanced"])),
        "highPlainsHighBasedCells": int(np.sum(northern["high_plains_high_based"])),
        "steepLapseRateLandspoutCells": int(np.sum(northern["steep_lapse_rate_landspout"])),
        "northernElevatedHailCells": int(np.sum(northern["northern_elevated_hail"])),
        "northernNocturnalMcsCells": int(np.sum(northern["northern_nocturnal_mcs"])),
        "northeastLowCapeHighShearCells": int(np.sum(new_regions["ne_low_cape_high_shear"])),
        "northeastCadStableCells": int(np.sum(new_regions["ne_cad_stable"])),
        "northeastWedgeFrontCells": int(np.sum(new_regions["ne_wedge_front"])),
        "desertSouthwestDryMicrobursts": int(np.sum(new_regions["dsw_dry_microburst"])),
        "desertSouthwestMonsoonHeavyRainCells": int(np.sum(new_regions["dsw_monsoon_suppressed"])),
        "pacificNorthwestColdCoreCells": int(np.sum(new_regions["pnw_cold_core"])),
        "pacificNorthwestTerrainForcedClippedCells": int(np.sum(new_regions["pnw_terrain_forced_clip"])),
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


def apply_regional_strict_category_caps(
    category_grid: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
) -> np.ndarray:
    """Apply hard regional caps used by generated artifact vector layers."""
    grid = np.asarray(category_grid, dtype=np.int16).copy()
    lat_grid, lon_grid = _lat_lon_grid(lats, lons, grid.shape)
    max_category_grid = _regional_strict_max_category_grid(lat_grid, lon_grid)
    return np.minimum(grid, max_category_grid).astype(np.int16)


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
        & (features.raw["sfcDewpointF"] >= 52.0)
        & (features.raw["shear06Kt"] >= 25.0)
        & (features.raw["cin"] > -225.0)
    )
    severe_kinematic_mask = (
        (features.raw["shear06Kt"] >= _SEVERE_KINEMATIC_MIN_SHEAR)
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
    kinematic_clamp = np.where(features.raw["mucape"] >= 1500.0, 2, 1)
    severe_ord = np.where(severe_kinematic_mask, severe_ord, np.minimum(severe_ord, kinematic_clamp))
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
    is_ph_grid = False
    if features.raw.get("is_philippines") or features.shape == (73, 57):
        is_ph_grid = True
    elif model_metadata and isinstance(model_metadata, dict):
        model_version = str(model_metadata.get("version", "")).lower()
        if "ecmwf" in model_version or "philippines" in model_version:
            is_ph_grid = True

    if is_ph_grid:
        tstm_mask = (
            (np.maximum(features.raw["sbcape"], features.raw["mucape"]) >= 1000.0)
            & (features.raw["sfcDewpointF"] >= 65.0)
            & (features.raw["cin"] >= -150.0)
        )
    else:
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
    texas_mexico_border_mask = None
    if lats is not None and lons is not None:
        lat_grid, lon_grid = _lat_lon_grid(lats, lons, grid.shape)
        land_mask = _rough_conus_land_mask(lat_grid, lon_grid)
        offshore_masks = _strict_offshore_masks(lat_grid, lon_grid)
        gulf_offshore_mask = offshore_masks["gulfOfMexico"]
        florida_gulf_mask = offshore_masks["floridaGulf"]
        atlantic_offshore_mask = offshore_masks["atlanticOcean"]
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

        max_category_grid = np.full(grid.shape, len(SPC_RISK_LABELS) - 1, dtype=np.int16)
        if gulf_offshore_mask is not None:
            _force_offshore_none(grid, gulf_offshore_mask, downgraded, "gulfOfMexico")
            max_category_grid[gulf_offshore_mask] = 0
        if florida_gulf_mask is not None:
            _force_offshore_none(grid, florida_gulf_mask, downgraded, "floridaGulf")
            max_category_grid[florida_gulf_mask] = 0
        if atlantic_offshore_mask is not None:
            _force_offshore_none(grid, atlantic_offshore_mask, downgraded, "atlanticOcean")
            max_category_grid[atlantic_offshore_mask] = 0
        if texas_mexico_border_mask is not None:
            _cap_category_at_most(
                grid,
                texas_mexico_border_mask,
                SPC_RISK_LABELS.index("MRGL"),
                downgraded,
                "texasMexicoBorder",
            )
            max_category_grid[texas_mexico_border_mask] = np.minimum(
                max_category_grid[texas_mexico_border_mask],
                SPC_RISK_LABELS.index("MRGL"),
            )

        hierarchy_report = _enforce_category_hierarchy(
            grid,
            organized,
            ndimage,
            max_category_grid=max_category_grid,
        )
        downgraded["missingCategoryBuffer"] += int(hierarchy_report.get("totalAddedCells", 0))
    else:
        hierarchy_report = {
            "applied": False,
            "totalAddedCells": 0,
            "addedCellsByCategory": {},
            "passes": 0,
        }

    report = {
        "morphologicalSmoothingApplied": ndimage is not None,
        "exactBandsGenerated": True,
        "categoryHierarchyEnforced": bool(hierarchy_report.get("applied")),
        "hierarchyBuffers": hierarchy_report,
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
    grid = np.asarray(category_grid, dtype=np.int16)
    regional_max_grid = _regional_strict_max_category_grid(lat_grid, lon_grid)
    features: list[dict[str, Any]] = []
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None

    masks_by_ordinal: dict[int, tuple[np.ndarray, dict[str, int | float | bool]]] = {}
    for ordinal in range(1, len(SPC_RISK_LABELS)):
        # SPC-style categorical outlooks are drawn cumulatively: TSTM is the
        # full thunder area, MRGL is MRGL-or-higher, then higher risks paint on
        # top. This avoids visible annular grid seams and preserves hierarchy.
        mask = _clip_mask_to_regional_strictness(grid >= ordinal, ordinal, regional_max_grid)
        if not np.any(mask):
            continue
        settings = _category_generalization_settings(mask, ordinal, min_cells)
        limit_base = _clip_mask_to_regional_strictness(grid >= max(1, ordinal - 1), ordinal, regional_max_grid)
        limit_mask = _cartographic_limit_mask(limit_base, ndimage, int(settings["closeIterations"]) + 1)
        if limit_mask is not None:
            limit_mask = _clip_mask_to_regional_strictness(limit_mask, ordinal, regional_max_grid)
        mask = _generalize_mask(
            mask,
            ndimage,
            min_cells=int(settings["minimumComponentCells"]),
            close_iterations=int(settings["closeIterations"]),
            prune_iterations=int(settings["tendrilPruneIterations"]),
            max_hole_cells=int(settings["maximumHoleCells"]),
            limit_mask=limit_mask,
        )
        mask = _clip_mask_to_regional_strictness(mask, ordinal, regional_max_grid)
        if np.any(mask):
            masks_by_ordinal[ordinal] = (mask, settings)
    _enforce_cumulative_mask_hierarchy(masks_by_ordinal)
    for ordinal, (mask, _settings) in masks_by_ordinal.items():
        mask &= regional_max_grid >= ordinal

    for ordinal in range(1, len(SPC_RISK_LABELS)):
        item = masks_by_ordinal.get(ordinal)
        if item is None:
            continue
        mask, settings = item
        rings, component_count, cell_count = _mask_polygons(
            lon_grid,
            lat_grid,
            mask,
            min_cells=1 if ndimage is not None else int(settings["minimumComponentCells"]),
            ndimage=ndimage,
            smoothing_iterations=int(settings["smoothingIterations"]),
            simplify_tolerance=float(settings["simplifyTolerance"]),
        )
        if not rings:
            continue
        exact_cell_count = int(np.sum(grid == ordinal))
        features.append({
            "type": "Feature",
            "geometry": _rings_geometry(rings),
            "properties": {
                "category": SPC_RISK_LABELS[ordinal],
                "ordinal": ordinal,
                "forecastHour": forecast_hour,
                "validTimeISO": valid_time_iso,
                "cellCount": cell_count,
                "sourceCellCount": exact_cell_count,
                "cumulativeCellCount": cell_count,
                "componentCount": component_count,
                "vectorization": {
                    "method": _CATEGORY_VECTORIZATION_METHOD,
                    "cumulativeMask": True,
                    "cartographicGeneralization": True,
                    "smoothingIterations": int(settings["smoothingIterations"]),
                    "simplifyTolerance": float(settings["simplifyTolerance"]),
                    "closeIterations": int(settings["closeIterations"]),
                    "tendrilPruneIterations": int(settings["tendrilPruneIterations"]),
                    "maximumHoleCells": int(settings["maximumHoleCells"]),
                    "minimumComponentCells": int(settings["minimumComponentCells"]),
                },
            },
        })
    features = _display_gap_features(features, order_property="ordinal")
    return {"type": "FeatureCollection", "features": features}


def hazard_probability_shapes_from_grids(
    lats: np.ndarray,
    lons: np.ndarray,
    probabilities: Mapping[str, np.ndarray],
    category_grid: np.ndarray,
    forecast_hour: int,
    valid_time_iso: str,
    min_cells: int = 8,
) -> dict[str, Any]:
    """Build smooth cumulative probability contour polygons for hazard maps."""
    lat_grid, lon_grid = _lat_lon_grid(lats, lons, category_grid.shape)
    grid = np.asarray(category_grid, dtype=np.int16)
    regional_max_grid = _regional_strict_max_category_grid(lat_grid, lon_grid)
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None

    features: list[dict[str, Any]] = []
    hazard_inputs: dict[str, tuple[np.ndarray, tuple[float, ...], np.ndarray]] = {
        "tornado": (
            np.asarray(probabilities.get("tornado", np.zeros(grid.shape)), dtype=float),
            _TORNADO_PROBABILITY_THRESHOLDS,
            grid >= SPC_RISK_LABELS.index("MRGL"),
        ),
        "hail": (
            np.asarray(probabilities.get("hail", np.zeros(grid.shape)), dtype=float),
            _SEVERE_PROBABILITY_THRESHOLDS,
            grid >= SPC_RISK_LABELS.index("MRGL"),
        ),
        "wind": (
            np.asarray(probabilities.get("wind", np.zeros(grid.shape)), dtype=float),
            _SEVERE_PROBABILITY_THRESHOLDS,
            grid >= SPC_RISK_LABELS.index("MRGL"),
        ),
        "thunder": (
            _thunder_probability_from_category_grid(grid, lat_grid, lon_grid),
            (0.30, 0.60, 0.90) if _is_philippines_grid(lat_grid, lon_grid) else _THUNDER_PROBABILITY_THRESHOLDS,
            grid >= SPC_RISK_LABELS.index("TSTM"),
        ),
    }
    for hazard, (probability_grid, thresholds, support_mask) in hazard_inputs.items():
        masks = _threshold_masks_with_hierarchy(probability_grid, thresholds, support_mask, ndimage)
        settings_by_bucket: list[dict[str, int | float | bool]] = []
        generalized_masks: list[np.ndarray] = []
        for bucket, mask in enumerate(masks):
            threshold = thresholds[bucket]
            regional_allowed_mask = _regional_probability_allowed_mask(hazard, threshold, regional_max_grid)
            mask = np.asarray(mask, dtype=bool) & regional_allowed_mask
            settings = _probability_generalization_settings(mask, bucket, min_cells)
            limit_mask = _cartographic_limit_mask(
                np.asarray(support_mask, dtype=bool) & regional_allowed_mask,
                ndimage,
                int(settings["closeIterations"]) + 1,
            )
            if limit_mask is not None:
                limit_mask &= regional_allowed_mask
            generalized = _generalize_mask(
                mask,
                ndimage,
                min_cells=int(settings["minimumComponentCells"]),
                close_iterations=int(settings["closeIterations"]),
                prune_iterations=int(settings["tendrilPruneIterations"]),
                max_hole_cells=int(settings["maximumHoleCells"]),
                limit_mask=limit_mask,
            )
            generalized &= regional_allowed_mask
            generalized_masks.append(generalized)
            settings_by_bucket.append(settings)
        _enforce_probability_mask_hierarchy(generalized_masks)
        for bucket, threshold in enumerate(thresholds):
            generalized_masks[bucket] &= _regional_probability_allowed_mask(hazard, threshold, regional_max_grid)
        colors = _PROBABILITY_COLORS[hazard]
        for bucket, (threshold, mask) in enumerate(zip(thresholds, generalized_masks, strict=False)):
            if not np.any(mask):
                continue
            settings = settings_by_bucket[bucket]
            rings, component_count, cell_count = _mask_polygons(
                lon_grid,
                lat_grid,
                mask,
                min_cells=1 if ndimage is not None else int(settings["minimumComponentCells"]),
                ndimage=ndimage,
                smoothing_iterations=int(settings["smoothingIterations"]),
                simplify_tolerance=float(settings["simplifyTolerance"]),
            )
            if not rings:
                continue
            source_cell_count = int(np.sum(probability_grid >= threshold))
            features.append({
                "type": "Feature",
                "geometry": _rings_geometry(rings),
                "properties": {
                    "hazard": hazard,
                    "hazardLabel": "thunderstorm" if hazard == "thunder" else hazard,
                    "probability": float(threshold),
                    "threshold": float(threshold),
                    "thresholdPercent": int(round(threshold * 100)),
                    "bucket": bucket,
                    "label": f"{int(round(threshold * 100))}%",
                    "color": colors[min(bucket, len(colors) - 1)],
                    "forecastHour": forecast_hour,
                    "validTimeISO": valid_time_iso,
                    "cellCount": cell_count,
                    "sourceCellCount": source_cell_count,
                    "componentCount": component_count,
                    "vectorization": {
                        "method": _PROBABILITY_VECTORIZATION_METHOD,
                        "cumulativeMask": True,
                        "cartographicGeneralization": True,
                        "hierarchyBuffersApplied": bool(cell_count > source_cell_count),
                        "smoothingIterations": int(settings["smoothingIterations"]),
                        "simplifyTolerance": float(settings["simplifyTolerance"]),
                        "closeIterations": int(settings["closeIterations"]),
                        "tendrilPruneIterations": int(settings["tendrilPruneIterations"]),
                        "maximumHoleCells": int(settings["maximumHoleCells"]),
                        "minimumComponentCells": int(settings["minimumComponentCells"]),
                    },
                },
            })
    features = _display_gap_features(features, order_property="bucket", group_property="hazard")
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


@njit(cache=True)
def _block_probability_tile_arrays_numba_helper(
    lat_grid,
    lon_grid,
    category_grid,
    prob_stacked,
    stride
):
    H, W = category_grid.shape
    num_hazards = prob_stacked.shape[0]

    out_h = (H + stride - 1) // stride
    out_w = (W + stride - 1) // stride

    cats = np.zeros((out_h, out_w), dtype=np.int16)
    tile_lats = np.zeros((out_h, out_w), dtype=np.float64)
    tile_lons = np.zeros((out_h, out_w), dtype=np.float64)
    tile_prob = np.zeros((num_hazards, out_h, out_w), dtype=np.float64)

    for r in range(out_h):
        r_start = r * stride
        r_end = min(H, r_start + stride)
        for c in range(out_w):
            c_start = c * stride
            c_end = min(W, c_start + stride)

            c_max = category_grid[r_start, c_start]
            lat_sum = 0.0
            lon_sum = 0.0
            count = 0

            h_maxes = np.zeros(num_hazards)
            for h in range(num_hazards):
                h_maxes[h] = prob_stacked[h, r_start, c_start]

            for i in range(r_start, r_end):
                for j in range(c_start, c_end):
                    val = category_grid[i, j]
                    if val > c_max:
                        c_max = val

                    lat_val = lat_grid[i, j]
                    lon_val = lon_grid[i, j]
                    if not np.isnan(lat_val):
                        lat_sum += lat_val
                        lon_sum += lon_val
                        count += 1

                    for h in range(num_hazards):
                        h_val = prob_stacked[h, i, j]
                        if not np.isnan(h_val) and h_val > h_maxes[h]:
                            h_maxes[h] = h_val

            cats[r, c] = c_max
            if count > 0:
                tile_lats[r, c] = lat_sum / count
                tile_lons[r, c] = lon_sum / count
            else:
                tile_lats[r, c] = 0.0
                tile_lons[r, c] = 0.0

            for h in range(num_hazards):
                tile_prob[h, r, c] = h_maxes[h]

    return cats, tile_lats, tile_lons, tile_prob


def _block_probability_tile_arrays(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    category_grid: np.ndarray,
    probabilities: Mapping[str, np.ndarray],
    stride: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    if stride == 1:
        cats = category_grid.copy()
        tile_lats = lat_grid.copy()
        tile_lons = lon_grid.copy()
        tile_probabilities = {hazard: grid.copy() for hazard, grid in probabilities.items()}
        return cats, tile_lats, tile_lons, tile_probabilities

    hazards = list(probabilities.keys())
    prob_stacked = np.stack([probabilities[h] for h in hazards], axis=0)

    cats, tile_lats, tile_lons, tile_prob = _block_probability_tile_arrays_numba_helper(
        lat_grid,
        lon_grid,
        category_grid,
        prob_stacked,
        stride
    )

    tile_probabilities = {
        hazards[i]: tile_prob[i] for i in range(len(hazards))
    }
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


def _enforce_category_hierarchy(
    grid: np.ndarray,
    support_mask: np.ndarray,
    ndimage: Any,
    max_category_grid: np.ndarray | None = None,
) -> dict[str, Any]:
    """Add conservative intermediate rings so categories do not skip levels."""
    if ndimage is None:
        return {
            "applied": False,
            "totalAddedCells": 0,
            "addedCellsByCategory": {},
            "passes": 0,
        }
    max_grid = (
        np.asarray(max_category_grid, dtype=np.int16)
        if max_category_grid is not None
        else np.full(grid.shape, len(SPC_RISK_LABELS) - 1, dtype=np.int16)
    )
    eligible = np.asarray(support_mask, dtype=bool) & (max_grid > 0)
    structure = np.ones((3, 3), dtype=bool)
    added_by_ordinal = {ordinal: 0 for ordinal in range(1, len(SPC_RISK_LABELS))}
    passes = 0

    # First create complete nested rings around every higher category:
    # HIGH gets MDT/ENH/SLGT/MRGL support, MDT gets ENH/SLGT/MRGL, etc.
    for ordinal in range(len(SPC_RISK_LABELS) - 1, SPC_RISK_LABELS.index("MRGL"), -1):
        seed = np.asarray(grid >= ordinal, dtype=bool)
        if not np.any(seed):
            continue
        expanded = seed.copy()
        for target in range(ordinal - 1, SPC_RISK_LABELS.index("MRGL") - 1, -1):
            expanded = ndimage.binary_dilation(expanded, structure=structure, iterations=1)
            candidate = expanded & eligible & (grid < target) & (max_grid >= target)
            if np.any(candidate):
                added_by_ordinal[target] += int(np.sum(candidate))
                grid[candidate] = target

    # Add a general thunder ring around severe areas where the environmental
    # support exists. This keeps MRGL from appearing as a free-floating island.
    severe_seed = np.asarray(grid >= SPC_RISK_LABELS.index("MRGL"), dtype=bool)
    if np.any(severe_seed):
        candidate = (
            ndimage.binary_dilation(severe_seed, structure=structure, iterations=1)
            & eligible
            & (grid < SPC_RISK_LABELS.index("TSTM"))
            & (max_grid >= SPC_RISK_LABELS.index("TSTM"))
        )
        if np.any(candidate):
            added_by_ordinal[SPC_RISK_LABELS.index("TSTM")] += int(np.sum(candidate))
            grid[candidate] = SPC_RISK_LABELS.index("TSTM")

    # Repair any remaining direct adjacency skips after regional clipping/caps.
    for _ in range(4):
        passes += 1
        changed = False
        for ordinal in range(len(SPC_RISK_LABELS) - 1, SPC_RISK_LABELS.index("SLGT") - 1, -1):
            seed = np.asarray(grid >= ordinal, dtype=bool)
            if not np.any(seed):
                continue
            target = ordinal - 1
            candidate = (
                ndimage.binary_dilation(seed, structure=structure, iterations=1)
                & eligible
                & (grid > 0)
                & (grid < target)
                & (max_grid >= target)
            )
            if np.any(candidate):
                added_by_ordinal[target] += int(np.sum(candidate))
                grid[candidate] = target
                changed = True
        severe_seed = np.asarray(grid >= SPC_RISK_LABELS.index("MRGL"), dtype=bool)
        candidate = (
            ndimage.binary_dilation(severe_seed, structure=structure, iterations=1)
            & eligible
            & (grid < SPC_RISK_LABELS.index("TSTM"))
            & (max_grid >= SPC_RISK_LABELS.index("TSTM"))
        )
        if np.any(candidate):
            added_by_ordinal[SPC_RISK_LABELS.index("TSTM")] += int(np.sum(candidate))
            grid[candidate] = SPC_RISK_LABELS.index("TSTM")
            changed = True
        if not changed:
            break

    added_by_category = {
        SPC_RISK_LABELS[ordinal]: count
        for ordinal, count in added_by_ordinal.items()
        if count
    }
    return {
        "applied": True,
        "totalAddedCells": int(sum(added_by_ordinal.values())),
        "addedCellsByCategory": added_by_category,
        "passes": passes,
    }


def _threshold_masks_with_hierarchy(
    probability_grid: np.ndarray,
    thresholds: tuple[float, ...],
    support_mask: np.ndarray,
    ndimage: Any,
) -> list[np.ndarray]:
    masks = [np.asarray(probability_grid >= threshold, dtype=bool) for threshold in thresholds]
    if ndimage is None:
        return masks
    support = np.asarray(support_mask, dtype=bool)
    structure = np.ones((3, 3), dtype=bool)
    for high_idx in range(len(thresholds) - 1, 0, -1):
        expanded = masks[high_idx].copy()
        if not np.any(expanded):
            continue
        for target_idx in range(high_idx - 1, -1, -1):
            expanded = ndimage.binary_dilation(expanded, structure=structure, iterations=1) & support
            masks[target_idx] |= expanded
    return masks


def _thunder_probability_from_category_grid(
    category_grid: np.ndarray,
    lat_grid: np.ndarray = None,
    lon_grid: np.ndarray = None,
) -> np.ndarray:
    grid = np.asarray(category_grid, dtype=np.int16)
    is_phil = lat_grid is not None and lon_grid is not None and _is_philippines_grid(lat_grid, lon_grid)
    if is_phil:
        return np.where(
            grid >= SPC_RISK_LABELS.index("ENH"),
            0.90,
            np.where(
                grid >= SPC_RISK_LABELS.index("MRGL"),
                0.60,
                np.where(grid >= SPC_RISK_LABELS.index("TSTM"), 0.30, 0.0),
            ),
        )
    return np.where(
        grid >= SPC_RISK_LABELS.index("ENH"),
        0.70,
        np.where(
            grid >= SPC_RISK_LABELS.index("MRGL"),
            0.40,
            np.where(grid >= SPC_RISK_LABELS.index("TSTM"), 0.10, 0.0),
        ),
    )


def _category_generalization_settings(
    mask: np.ndarray,
    ordinal: int,
    requested_min_cells: int,
) -> dict[str, int | float | bool]:
    active = int(np.sum(mask))
    base_min = {
        1: 360,
        2: 300,
        3: 220,
        4: 140,
        5: 95,
        6: 75,
    }.get(int(ordinal), 120)
    close_iterations = {
        1: 7,
        2: 6,
        3: 5,
        4: 4,
        5: 3,
        6: 2,
    }.get(int(ordinal), 4)
    prune_iterations = {
        1: 2,
        2: 2,
        3: 2,
        4: 1,
        5: 1,
        6: 1,
    }.get(int(ordinal), 1)
    max_hole = {
        1: 760,
        2: 620,
        3: 460,
        4: 320,
        5: 210,
        6: 160,
    }.get(int(ordinal), 300)
    smoothing_iterations = {
        1: 6,
        2: 6,
        3: 5,
        4: 5,
        5: 4,
        6: 4,
    }.get(int(ordinal), 5)
    simplify_tolerance = {
        1: 0.145,
        2: 0.135,
        3: 0.120,
        4: 0.105,
        5: 0.090,
        6: 0.080,
    }.get(int(ordinal), 0.105)
    dynamic_floor = max(int(requested_min_cells), 8, int(round(active * 0.12)))
    return {
        "minimumComponentCells": max(int(requested_min_cells), min(base_min, dynamic_floor)),
        "closeIterations": _bounded_close_iterations(mask, close_iterations),
        "tendrilPruneIterations": _bounded_close_iterations(mask, prune_iterations),
        "maximumHoleCells": min(max_hole, max(12, int(round(active * 0.18)))),
        "smoothingIterations": smoothing_iterations,
        "simplifyTolerance": simplify_tolerance,
    }


def _probability_generalization_settings(
    mask: np.ndarray,
    bucket: int,
    requested_min_cells: int,
) -> dict[str, int | float | bool]:
    active = int(np.sum(mask))
    base_min_by_bucket = (340, 280, 210, 155, 115, 85, 70)
    close_by_bucket = (7, 6, 5, 4, 3, 2, 2)
    prune_by_bucket = (2, 2, 2, 1, 1, 1, 1)
    hole_by_bucket = (720, 580, 440, 320, 230, 170, 130)
    smooth_by_bucket = (6, 6, 5, 5, 4, 4, 4)
    tolerance_by_bucket = (0.145, 0.135, 0.120, 0.105, 0.090, 0.080, 0.075)
    idx = min(max(0, int(bucket)), len(base_min_by_bucket) - 1)
    dynamic_floor = max(int(requested_min_cells), 8, int(round(active * 0.12)))
    return {
        "minimumComponentCells": max(int(requested_min_cells), min(base_min_by_bucket[idx], dynamic_floor)),
        "closeIterations": _bounded_close_iterations(mask, close_by_bucket[idx]),
        "tendrilPruneIterations": _bounded_close_iterations(mask, prune_by_bucket[idx]),
        "maximumHoleCells": min(hole_by_bucket[idx], max(12, int(round(active * 0.18)))),
        "smoothingIterations": smooth_by_bucket[idx],
        "simplifyTolerance": tolerance_by_bucket[idx],
    }


def _bounded_close_iterations(mask: np.ndarray, desired_iterations: int) -> int:
    desired = max(0, int(desired_iterations))
    if desired <= 0:
        return 0
    arr = np.asarray(mask, dtype=bool)
    active = int(np.sum(arr))
    if active <= 0:
        return 0
    rows, cols = np.where(arr)
    min_span = min(int(rows.max() - rows.min() + 1), int(cols.max() - cols.min() + 1))
    if min_span <= 3:
        return 0
    if min_span <= 6 or active < 50:
        return min(desired, 1)
    if min_span <= 12 or active < 160:
        return min(desired, 2)
    return desired


def _cartographic_limit_mask(mask: np.ndarray, ndimage: Any, iterations: int) -> np.ndarray | None:
    if ndimage is None:
        return None
    base = np.asarray(mask, dtype=bool)
    if not np.any(base):
        return base
    return ndimage.binary_dilation(
        base,
        structure=np.ones((3, 3), dtype=bool),
        iterations=max(1, int(iterations)),
    )


def _generalize_mask(
    mask: np.ndarray,
    ndimage: Any,
    min_cells: int,
    close_iterations: int,
    prune_iterations: int,
    max_hole_cells: int,
    limit_mask: np.ndarray | None = None,
) -> np.ndarray:
    out = np.asarray(mask, dtype=bool).copy()
    if ndimage is None or not np.any(out):
        return out
    structure = np.ones((3, 3), dtype=bool)
    close_iterations = max(0, int(close_iterations))
    if close_iterations:
        out = ndimage.binary_closing(out, structure=structure, iterations=close_iterations)
    if limit_mask is not None:
        out &= np.asarray(limit_mask, dtype=bool)
    out = _fill_small_mask_holes(out, ndimage, max_hole_cells)
    if limit_mask is not None:
        out &= np.asarray(limit_mask, dtype=bool)
    out = _remove_small_mask_components(out, ndimage, min_cells)
    prune_iterations = max(0, int(prune_iterations))
    if prune_iterations and np.any(out):
        out = _prune_thin_mask(out, ndimage, prune_iterations)
        if limit_mask is not None:
            out &= np.asarray(limit_mask, dtype=bool)
        out = _fill_small_mask_holes(out, ndimage, max_hole_cells)
        out = _remove_small_mask_components(out, ndimage, min_cells)
    if close_iterations > 1 and np.any(out):
        out = ndimage.binary_closing(out, structure=structure, iterations=max(1, close_iterations // 2))
        if limit_mask is not None:
            out &= np.asarray(limit_mask, dtype=bool)
        out = _fill_small_mask_holes(out, ndimage, max_hole_cells)
        out = _remove_small_mask_components(out, ndimage, min_cells)
    return out


def _prune_thin_mask(mask: np.ndarray, ndimage: Any, iterations: int) -> np.ndarray:
    out = np.asarray(mask, dtype=bool)
    if iterations <= 0 or not np.any(out):
        return out
    structure = np.ones((3, 3), dtype=bool)
    original_cells = int(np.sum(out))
    for count in range(int(iterations), 0, -1):
        opened = ndimage.binary_opening(out, structure=structure, iterations=count)
        opened_cells = int(np.sum(opened))
        if opened_cells >= max(1, int(round(original_cells * 0.35))):
            return opened
    return out


def _fill_small_mask_holes(mask: np.ndarray, ndimage: Any, max_hole_cells: int) -> np.ndarray:
    out = np.asarray(mask, dtype=bool).copy()
    if not np.any(out) or max_hole_cells <= 0:
        return out
    filled = ndimage.binary_fill_holes(out)
    holes = filled & ~out
    if not np.any(holes):
        return out
    labels, count = ndimage.label(holes, structure=np.ones((3, 3), dtype=int))
    for component_id in range(1, count + 1):
        component = labels == component_id
        if int(np.sum(component)) <= int(max_hole_cells):
            out[component] = True
    return out


def _remove_small_mask_components(mask: np.ndarray, ndimage: Any, min_cells: int) -> np.ndarray:
    out = np.asarray(mask, dtype=bool)
    if not np.any(out):
        return out
    labels, count = ndimage.label(out, structure=np.ones((3, 3), dtype=int))
    keep = np.zeros(out.shape, dtype=bool)
    largest_component: np.ndarray | None = None
    largest_count = 0
    for component_id in range(1, count + 1):
        component = labels == component_id
        cell_count = int(np.sum(component))
        if cell_count > largest_count:
            largest_component = component
            largest_count = cell_count
        if cell_count >= int(min_cells):
            keep |= component
    if not np.any(keep) and largest_component is not None:
        keep |= largest_component
    return keep


def _enforce_cumulative_mask_hierarchy(
    masks_by_ordinal: dict[int, tuple[np.ndarray, dict[str, int | float | bool]]],
) -> None:
    for ordinal in range(len(SPC_RISK_LABELS) - 1, 1, -1):
        current = masks_by_ordinal.get(ordinal)
        lower = masks_by_ordinal.get(ordinal - 1)
        if current is None:
            continue
        if lower is None:
            masks_by_ordinal[ordinal - 1] = (current[0].copy(), current[1])
        else:
            np.logical_or(lower[0], current[0], out=lower[0])


def _enforce_probability_mask_hierarchy(masks: list[np.ndarray]) -> None:
    for idx in range(len(masks) - 1, 0, -1):
        masks[idx - 1] |= masks[idx]


def _mask_polygons(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    mask: np.ndarray,
    min_cells: int,
    ndimage: Any,
    smoothing_iterations: int,
    simplify_tolerance: float = 0.0,
) -> tuple[list[list[list[float]]], int, int]:
    mask = np.asarray(mask, dtype=bool)
    if ndimage is None:
        components = [(mask, int(mask.sum()))]
    else:
        labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
        components = [(labels == idx, int(np.sum(labels == idx))) for idx in range(1, count + 1)]
    rings: list[list[list[float]]] = []
    component_count = 0
    cell_count = 0
    for component, component_cells in components:
        if component_cells < int(min_cells):
            continue
        component_rings = _component_polygons(
            lon_grid,
            lat_grid,
            component,
            smoothing_iterations=smoothing_iterations,
            simplify_tolerance=simplify_tolerance,
        )
        component_rings = [ring for ring in component_rings if len(ring) >= 4]
        if not component_rings:
            continue
        rings.extend(component_rings)
        component_count += 1
        cell_count += int(component_cells)
    rings.sort(key=lambda ring: abs(_signed_ring_area(ring[:-1] if ring[0] == ring[-1] else ring)), reverse=True)
    return rings, component_count, cell_count


def _rings_geometry(rings: list[list[list[float]]]) -> dict[str, Any]:
    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": [rings[0]]}
    return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}


def _display_gap_features(
    features: list[dict[str, Any]],
    order_property: str,
    group_property: str | None = None,
) -> list[dict[str, Any]]:
    if not features:
        return features
    try:
        from pyproj import Transformer
        from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
        from shapely.ops import transform as shapely_transform
        from shapely.ops import unary_union
    except Exception:
        return features

    to_projected = Transformer.from_crs("EPSG:4326", _DISPLAY_BAND_CRS, always_xy=True).transform
    to_lonlat = Transformer.from_crs(_DISPLAY_BAND_CRS, "EPSG:4326", always_xy=True).transform
    indexed = list(enumerate(features))
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, feature in indexed:
        group = str(feature.get("properties", {}).get(group_property, "__all__")) if group_property else "__all__"
        groups.setdefault(group, []).append((idx, feature))

    output_by_index: dict[int, dict[str, Any]] = {}
    consumed_indices: set[int] = set()
    for group_features in groups.values():
        ordered = sorted(
            group_features,
            key=lambda item: int(item[1].get("properties", {}).get(order_property, 0)),
        )
        group_indices = {idx for idx, _feature in ordered}
        projected_by_index: dict[int, Any] = {}
        settings_by_index: dict[int, dict[str, float]] = {}
        for idx, feature in ordered:
            props = feature.get("properties", {})
            order_value = int(props.get(order_property, 0))
            settings = (
                _probability_display_geometry_settings(order_value)
                if group_property == "hazard"
                else _risk_display_geometry_settings(order_value)
            )
            try:
                projected = shapely_transform(to_projected, shape(feature.get("geometry")))
            except Exception:
                continue
            projected = _clean_projected_geometry(projected)
            if projected.is_empty:
                continue
            projected = _smooth_display_projected_geometry(
                projected,
                smooth_m=settings["smoothMeters"],
                simplify_m=settings["simplifyMeters"],
            )
            projected = _drop_small_projected_parts(projected, settings["minimumAreaKm2"] * 1_000_000.0)
            if not projected.is_empty:
                projected_by_index[idx] = projected
                settings_by_index[idx] = settings

        if not projected_by_index:
            continue
        consumed_indices.update(group_indices)

        higher_display_union = None
        for position in range(len(ordered) - 1, -1, -1):
            idx, feature = ordered[position]
            projected = projected_by_index.get(idx)
            if projected is None or projected.is_empty:
                continue
            immediate_lower_projected = (
                projected_by_index.get(ordered[position - 1][0])
                if position > 0
                else None
            )
            display_geom = projected
            knockout = None
            applied_gap_m = 0.0
            applied_higher_expansion_m = 0.0
            applied_support_m = 0.0
            target_owned_boundary_m = (
                _LOWER_OWNED_BOUNDARY_METERS
                if position > 0
                else 0.0
            )
            applied_owned_boundary_m = target_owned_boundary_m
            if _DISPLAY_BAND_GAP_METERS > 0 and position > 0:
                applied_owned_boundary_m = 0.0
                display_geom, applied_higher_expansion_m = _expand_display_geometry_into_lower_space(
                    display_geom,
                    immediate_lower_projected,
                    _DISPLAY_BAND_GAP_METERS,
                )
            if _DISPLAY_BAND_GAP_METERS > 0 and higher_display_union is not None and not higher_display_union.is_empty:
                applied_owned_boundary_m = 0.0
                applied_gap_m = _DISPLAY_BAND_GAP_METERS
                display_geom, knockout, applied_support_m = _subtract_occupied_higher_display_space(
                    display_geom,
                    higher_display_union,
                    support_m=settings_by_index[idx]["supportMeters"],
                )
            settings = settings_by_index[idx]
            display_geom = _clean_projected_geometry(display_geom)
            display_geom = _drop_small_projected_parts(display_geom, settings["minimumAreaKm2"] * 1_000_000.0)
            display_geom = _smooth_display_projected_geometry(
                display_geom,
                smooth_m=max(2_000.0, settings["smoothMeters"] * 0.35),
                simplify_m=max(1_500.0, settings["simplifyMeters"] * 0.45),
            )
            if knockout is not None:
                reclipped = _clean_projected_geometry(display_geom.difference(knockout))
                if not reclipped.is_empty:
                    display_geom = reclipped
            display_geom = _clean_projected_geometry(display_geom)
            if display_geom.is_empty:
                continue
            display_geom = _drop_small_projected_parts(display_geom, settings["minimumAreaKm2"] * 0.35 * 1_000_000.0)
            if display_geom.is_empty:
                continue
            props = dict(feature.get("properties", {}))
            vectorization = dict(props.get("vectorization") or {})
            if _DISPLAY_BAND_GAP_METERS > 0 and (applied_gap_m > 0 or applied_higher_expansion_m > 0):
                display_geometry_label = "band_with_higher_owned_gap"
            elif applied_owned_boundary_m > 0:
                display_geometry_label = "band_with_lower_owned_boundary"
            else:
                display_geometry_label = "smoothed_cumulative_band"
            vectorization.update({
                "displayGeometry": display_geometry_label,
                "displayBandGapKm": round(applied_gap_m / 1000.0, 1),
                "targetDisplayBandGapKm": round(_DISPLAY_BAND_GAP_METERS / 1000.0, 1),
                "displayHigherRiskExpansionKm": round(applied_higher_expansion_m / 1000.0, 1),
                "targetDisplayHigherRiskExpansionKm": round((_DISPLAY_BAND_GAP_METERS if position > 0 else 0.0) / 1000.0, 1),
                "displayLowerOwnedBoundaryKm": round(applied_owned_boundary_m / 1000.0, 1),
                "targetDisplayLowerOwnedBoundaryKm": round(target_owned_boundary_m / 1000.0, 1),
                "displayMinimumSupportKm": round(applied_support_m / 1000.0, 1),
                "targetDisplayMinimumSupportKm": round(settings["supportMeters"] / 1000.0, 1),
                "displayProjection": _DISPLAY_BAND_CRS,
                "displaySmoothKm": round(settings["smoothMeters"] / 1000.0, 2),
                "displaySimplifyKm": round(settings["simplifyMeters"] / 1000.0, 2),
                "displayMinimumAreaKm2": round(settings["minimumAreaKm2"], 1),
            })
            props["vectorization"] = vectorization
            visible_display_geom = display_geom
            lonlat_geom = shapely_transform(to_lonlat, visible_display_geom)
            geometry = _geojson_geometry_from_shapely(lonlat_geom, Polygon, MultiPolygon, GeometryCollection)
            if geometry is None:
                continue
            props["componentCount"] = _projected_component_count(visible_display_geom)
            props["displayAreaKm2"] = round(float(visible_display_geom.area) / 1_000_000.0, 1)
            output_by_index[idx] = {
                **feature,
                "geometry": geometry,
                "properties": props,
                "_projectedDisplayGeometry": visible_display_geom,
            }
            higher_display_union = (
                visible_display_geom
                if higher_display_union is None or higher_display_union.is_empty
                else _clean_projected_geometry(unary_union([higher_display_union, visible_display_geom]))
            )

    out: list[dict[str, Any]] = []
    for idx, feature in indexed:
        item = output_by_index.get(idx)
        if item is not None:
            item = dict(item)
            item.pop("_projectedDisplayGeometry", None)
            out.append(item)
        elif idx not in consumed_indices:
            out.append(feature)
    return out


def _expand_display_geometry_into_lower_space(
    display_geometry: Any,
    lower_display_geometry: Any,
    expansion_m: float,
) -> tuple[Any, float]:
    if expansion_m <= 0 or lower_display_geometry is None or lower_display_geometry.is_empty:
        return display_geometry, 0.0

    from shapely.ops import unary_union

    expanded = _clean_projected_geometry(display_geometry.buffer(expansion_m, quad_segs=10, join_style=1))
    if expanded.is_empty:
        return display_geometry, 0.0
    clipped_expansion = _clean_projected_geometry(expanded.intersection(lower_display_geometry))
    if clipped_expansion.is_empty:
        return display_geometry, 0.0
    occupied = _clean_projected_geometry(unary_union([display_geometry, clipped_expansion]))
    if occupied.is_empty:
        return display_geometry, 0.0
    return occupied, float(expansion_m)


def _subtract_occupied_higher_display_space(
    base_geometry: Any,
    higher_occupied_union: Any,
    support_m: float,
) -> tuple[Any, Any | None, float]:
    if higher_occupied_union is None or higher_occupied_union.is_empty:
        return base_geometry, None, 0.0

    from shapely.ops import unary_union

    for factor in (1.0, 0.75, 0.50, 0.25, 0.10):
        support_width_m = max(0.0, float(support_m)) * factor
        supported_base = _clean_projected_geometry(
            unary_union([
                base_geometry,
                higher_occupied_union.buffer(support_width_m, quad_segs=10, join_style=1),
            ]),
        )
        candidate = _clean_projected_geometry(supported_base.difference(higher_occupied_union))
        if not candidate.is_empty and float(candidate.area) >= max(1.0, float(base_geometry.area) * 0.005):
            return candidate, higher_occupied_union, support_width_m
    candidate = _clean_projected_geometry(base_geometry.difference(higher_occupied_union))
    return candidate, higher_occupied_union, 0.0


def _subtract_next_higher_display_gap(
    base_geometry: Any,
    higher_display_union: Any,
    immediate_higher_display: Any,
    support_m: float,
) -> tuple[Any, Any | None, float, float]:
    if _DISPLAY_BAND_GAP_METERS <= 0:
        return base_geometry, None, 0.0, 0.0

    from shapely.ops import unary_union

    for factor in (1.0, 0.75, 0.50, 0.25, 0.10):
        gap_m = _DISPLAY_BAND_GAP_METERS * factor
        support_width_m = max(0.0, float(support_m)) * factor
        support_anchor = immediate_higher_display if immediate_higher_display is not None and not immediate_higher_display.is_empty else higher_display_union
        supported_base = _clean_projected_geometry(
            unary_union([
                base_geometry,
                support_anchor.buffer(gap_m + support_width_m, quad_segs=10, join_style=1),
            ]),
        )
        knockout = higher_display_union.buffer(gap_m, quad_segs=10, join_style=1)
        candidate = _clean_projected_geometry(supported_base.difference(knockout))
        if not candidate.is_empty and float(candidate.area) >= max(1.0, float(base_geometry.area) * 0.005):
            return candidate, knockout, gap_m, support_width_m
    return base_geometry, None, 0.0, 0.0


def _risk_display_geometry_settings(ordinal: int) -> dict[str, float]:
    return {
        "smoothMeters": {
            1: 12_000.0,
            2: 11_000.0,
            3: 9_000.0,
            4: 7_500.0,
            5: 6_000.0,
            6: 5_000.0,
        }.get(int(ordinal), 7_500.0),
        "simplifyMeters": {
            1: 9_000.0,
            2: 8_000.0,
            3: 7_000.0,
            4: 5_500.0,
            5: 4_500.0,
            6: 4_000.0,
        }.get(int(ordinal), 5_500.0),
        "supportMeters": {
            1: 45_000.0,
            2: 40_000.0,
            3: 35_000.0,
            4: 30_000.0,
            5: 25_000.0,
            6: 20_000.0,
        }.get(int(ordinal), _DISPLAY_BAND_MIN_SUPPORT_METERS),
        "minimumAreaKm2": {
            1: 1_500.0,
            2: 1_100.0,
            3: 700.0,
            4: 420.0,
            5: 260.0,
            6: 180.0,
        }.get(int(ordinal), 420.0),
    }


def _probability_display_geometry_settings(bucket: int) -> dict[str, float]:
    idx = max(0, int(bucket))
    smooth = (12_000.0, 11_000.0, 9_000.0, 7_500.0, 6_000.0, 5_000.0, 4_500.0)
    simplify = (9_000.0, 8_000.0, 7_000.0, 5_500.0, 4_500.0, 4_000.0, 3_500.0)
    support = (45_000.0, 40_000.0, 35_000.0, 30_000.0, 25_000.0, 22_000.0, 20_000.0)
    area = (1_400.0, 1_000.0, 650.0, 420.0, 280.0, 200.0, 160.0)
    capped = min(idx, len(smooth) - 1)
    return {
        "smoothMeters": smooth[capped],
        "simplifyMeters": simplify[capped],
        "supportMeters": support[capped],
        "minimumAreaKm2": area[capped],
    }


def _smooth_display_projected_geometry(geometry: Any, smooth_m: float, simplify_m: float) -> Any:
    if geometry.is_empty:
        return geometry
    out = _clean_projected_geometry(geometry)
    if out.is_empty:
        return out
    smooth_m = max(0.0, float(smooth_m))
    simplify_m = max(0.0, float(simplify_m))
    if smooth_m > 0.0:
        rounded = out.buffer(smooth_m, quad_segs=10, join_style=1).buffer(-smooth_m, quad_segs=10, join_style=1)
        if not rounded.is_empty:
            out = rounded
        shave_m = smooth_m * 0.45
        rounded = out.buffer(-shave_m, quad_segs=10, join_style=1).buffer(shave_m, quad_segs=10, join_style=1)
        if not rounded.is_empty:
            out = rounded
    if simplify_m > 0.0 and not out.is_empty:
        simplified = out.simplify(simplify_m, preserve_topology=True)
        if not simplified.is_empty:
            out = simplified
    if smooth_m > 0.0 and not out.is_empty:
        round_m = smooth_m * 0.60
        rounded = out.buffer(-round_m, quad_segs=10, join_style=1).buffer(round_m, quad_segs=10, join_style=1)
        if not rounded.is_empty:
            out = rounded
    return _clean_projected_geometry(out)


def _clean_projected_geometry(geometry: Any) -> Any:
    if geometry.is_empty:
        return geometry
    try:
        from shapely.validation import make_valid

        return make_valid(geometry)
    except Exception:
        try:
            return geometry.buffer(0)
        except Exception:
            return geometry


def _drop_small_projected_parts(geometry: Any, minimum_area_m2: float) -> Any:
    if geometry.is_empty:
        return geometry
    from shapely.geometry import GeometryCollection, MultiPolygon
    from shapely.ops import unary_union

    polygons = _projected_polygons(geometry)
    if not polygons:
        return GeometryCollection()
    kept = [poly for poly in polygons if float(poly.area) >= float(minimum_area_m2)]
    if not kept:
        kept = [max(polygons, key=lambda poly: float(poly.area))]
    if len(kept) == 1:
        return kept[0]
    return unary_union(MultiPolygon(kept))


def _projected_polygons(geometry: Any) -> list[Any]:
    if geometry.is_empty:
        return []
    geom_type = getattr(geometry, "geom_type", "")
    if geom_type == "Polygon":
        return [geometry]
    if geom_type == "MultiPolygon":
        return list(geometry.geoms)
    if geom_type == "GeometryCollection":
        polygons: list[Any] = []
        for item in geometry.geoms:
            polygons.extend(_projected_polygons(item))
        return polygons
    return []


def _projected_component_count(geometry: Any) -> int:
    return len(_projected_polygons(geometry))


def _geojson_geometry_from_shapely(
    geometry: Any,
    polygon_type: Any,
    multipolygon_type: Any,
    geometry_collection_type: Any,
) -> dict[str, Any] | None:
    if geometry.is_empty:
        return None
    geom_type = getattr(geometry, "geom_type", "")
    if geom_type == "Polygon":
        rings = _geojson_polygon_rings(geometry)
        return {"type": "Polygon", "coordinates": rings} if rings else None
    if geom_type == "MultiPolygon":
        polygons = [_geojson_polygon_rings(poly) for poly in geometry.geoms]
        polygons = [rings for rings in polygons if rings]
        if not polygons:
            return None
        if len(polygons) == 1:
            return {"type": "Polygon", "coordinates": polygons[0]}
        return {"type": "MultiPolygon", "coordinates": polygons}
    if geom_type == "GeometryCollection":
        polys = [geom for geom in geometry.geoms if isinstance(geom, (polygon_type, multipolygon_type, geometry_collection_type))]
        merged = []
        for geom in polys:
            mapped = _geojson_geometry_from_shapely(geom, polygon_type, multipolygon_type, geometry_collection_type)
            if not mapped:
                continue
            if mapped["type"] == "Polygon":
                merged.append(mapped["coordinates"])
            else:
                merged.extend(mapped["coordinates"])
        if not merged:
            return None
        if len(merged) == 1:
            return {"type": "Polygon", "coordinates": merged[0]}
        return {"type": "MultiPolygon", "coordinates": merged}
    return None


def _geojson_polygon_rings(polygon: Any) -> list[list[list[float]]]:
    exterior = _normalize_exterior_ring(_round_ring_coords(list(polygon.exterior.coords)))
    if len(exterior) < 4:
        return []
    rings = [exterior]
    for interior in polygon.interiors:
        hole = _normalize_interior_ring(_round_ring_coords(list(interior.coords)))
        if len(hole) >= 4:
            rings.append(hole)
    return rings


def _round_ring_coords(coords: list[tuple[float, float]]) -> list[list[float]]:
    return [[round(float(lon), 4), round(float(lat), 4)] for lon, lat in coords]


def _normalize_interior_ring(coords: list[list[float]]) -> list[list[float]]:
    if len(coords) < 4:
        return coords
    ring = coords[:-1] if coords[0] == coords[-1] else coords
    if _signed_ring_area(ring) < 0:
        ring = list(reversed(ring))
    out = [list(coord) for coord in ring]
    if out[0] != out[-1]:
        out.append(out[0])
    return out


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
        "sfcTempF": 58.0,
        "u10": 0.0,
        "v10": 0.0,
        "pwatIn": 0.8,
        "lclM": 1500.0,
        "moistureDepthM": 1500.0,
        "hgt500": 5700.0,
    }
    return defaults.get(name, 0.0)


def _hazard_ord(hazard: str, probability: np.ndarray) -> np.ndarray:
    # NWSI 10-512 Table 3 has separate rows for "with Significant Severe".
    # The gridded model currently predicts unconditional tornado, hail, and
    # wind probabilities only, so use the non-significant-severe Day 1/2
    # conversion rows. This keeps MDT/HIGH from being inferred without a
    # separate significant-severe signal.
    if hazard == "tornado":
        thresholds = ((0.45, 6), (0.30, 5), (0.10, 4), (0.05, 3), (0.02, 2))
    else:
        thresholds = ((0.60, 5), (0.30, 4), (0.15, 3), (0.05, 2))
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
        return {"tornado": 0.299, "hail": 0.59, "wind": 0.59}
    if category_cap <= SPC_RISK_LABELS.index("MDT"):
        return {"tornado": 0.449, "hail": 1.0, "wind": 1.0}
    return {"tornado": 1.0, "hail": 1.0, "wind": 1.0}


def _category_probability_cap_grid(hazard: str, category_grid: np.ndarray) -> np.ndarray:
    """Return per-cell ceilings just below the next higher category threshold."""
    caps_by_ordinal = {
        0: 0.019 if hazard == "tornado" else 0.049,
        1: 0.019 if hazard == "tornado" else 0.049,
        2: 0.049 if hazard == "tornado" else 0.149,
        3: 0.099 if hazard == "tornado" else 0.299,
        4: 0.299 if hazard == "tornado" else 0.599,
        5: 0.449 if hazard == "tornado" else 1.0,
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
    if _is_philippines_grid(lat_grid, lon_grid):
        return _philippines_land_mask(lat_grid, lon_grid)
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


def _southern_us_border_lat(lon_grid: np.ndarray) -> np.ndarray:
    anchors = [
        (-124.0, 32.5),
        (-117.0, 32.5),
        (-114.5, 32.0),
        (-111.0, 31.3),
        (-108.2, 31.3),
        (-106.5, 31.8),
        (-104.5, 29.6),
        (-103.0, 29.0),
        (-100.9, 29.3),
        (-99.5, 27.5),
        (-97.2, 26.0),
    ]
    return _interp_anchor(lon_grid, anchors)


def _texas_mexico_border_mrgl_cap_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    # Cap to MRGL south of the US-Mexico border (in Mexico)
    # and in a very narrow 0.05 degree border confidence degradation band.
    border_lat = _southern_us_border_lat(lon_grid)
    return lat_grid < (border_lat - 0.05)


def _strict_offshore_masks(lat_grid: np.ndarray, lon_grid: np.ndarray) -> dict[str, np.ndarray]:
    if _is_philippines_grid(lat_grid, lon_grid):
        offshore = ~_philippines_land_mask(lat_grid, lon_grid, buffer_deg=0.3)
        return {
            "philippinesOffshore": offshore,
            "gulfOfMexico": offshore,
            "floridaGulf": np.zeros_like(lat_grid, dtype=bool),
            "atlanticOcean": np.zeros_like(lat_grid, dtype=bool),
        }
    return {
        "gulfOfMexico": _gulf_of_mexico_offshore_mask(lat_grid, lon_grid),
        "floridaGulf": _florida_gulf_offshore_mask(lat_grid, lon_grid),
        "atlanticOcean": _atlantic_ocean_offshore_mask(lat_grid, lon_grid),
    }


def _strict_category_cap_masks(lat_grid: np.ndarray, lon_grid: np.ndarray) -> dict[str, np.ndarray]:
    if _is_philippines_grid(lat_grid, lon_grid):
        return {
            "texasMexicoBorder": np.zeros_like(lat_grid, dtype=bool),
        }
    return {
        "texasMexicoBorder": _texas_mexico_border_mrgl_cap_mask(lat_grid, lon_grid),
    }


def _regional_strict_max_category_grid(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    max_category = np.full(np.asarray(lat_grid).shape, len(SPC_RISK_LABELS) - 1, dtype=np.int16)
    for mask in _strict_offshore_masks(lat_grid, lon_grid).values():
        max_category[np.asarray(mask, dtype=bool)] = SPC_RISK_LABELS.index("NONE")
    for mask in _strict_category_cap_masks(lat_grid, lon_grid).values():
        target = np.asarray(mask, dtype=bool)
        max_category[target] = np.minimum(max_category[target], SPC_RISK_LABELS.index("MRGL"))
    return max_category


def _clip_mask_to_regional_strictness(
    mask: np.ndarray,
    ordinal: int,
    regional_max_grid: np.ndarray,
) -> np.ndarray:
    return np.asarray(mask, dtype=bool) & (np.asarray(regional_max_grid, dtype=np.int16) >= int(ordinal))


def _regional_probability_allowed_mask(
    hazard: str,
    threshold: float,
    regional_max_grid: np.ndarray,
) -> np.ndarray:
    max_grid = np.asarray(regional_max_grid, dtype=np.int16)
    if hazard == "thunder":
        if threshold >= 0.70:
            required_ordinal = SPC_RISK_LABELS.index("ENH")
        elif threshold >= 0.40:
            required_ordinal = SPC_RISK_LABELS.index("MRGL")
        else:
            required_ordinal = SPC_RISK_LABELS.index("TSTM")
        return max_grid >= required_ordinal
    return _category_probability_cap_grid(hazard, max_grid) >= float(threshold)


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


def _component_polygons(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    component: np.ndarray,
    smoothing_iterations: int = 1,
    simplify_tolerance: float = 0.0,
) -> list[list[list[float]]]:
    rings = _component_contour_polygons(
        lon_grid,
        lat_grid,
        component,
        smoothing_iterations=smoothing_iterations,
        simplify_tolerance=simplify_tolerance,
    )
    if rings:
        return rings
    fallback_rings = _component_cell_union_polygons(lon_grid, lat_grid, component, simplify_tolerance)
    if fallback_rings:
        return fallback_rings
    compact_ring = _compact_component_bbox_polygon(lon_grid[component], lat_grid[component])
    return [compact_ring] if compact_ring else []


def _component_contour_polygons(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    component: np.ndarray,
    smoothing_iterations: int = 1,
    simplify_tolerance: float = 0.0,
) -> list[list[list[float]]]:
    mask = np.pad(np.asarray(component, dtype=float), 1, mode="constant", constant_values=0.0)
    if np.nanmax(mask) < 0.5:
        return []
    with _MATPLOTLIB_CONTOUR_LOCK:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
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
        coords = _simplify_ring(coords, tolerance=simplify_tolerance)
        coords = _smooth_ring(coords, iterations=smoothing_iterations)
        coords = _simplify_ring(coords, tolerance=simplify_tolerance * 0.90)
        coords = _normalize_exterior_ring(coords)
        if len(coords) >= 4 and _ring_extent_ok(coords):
            rings.append(coords)
    rings.sort(key=lambda ring: abs(_signed_ring_area(ring[:-1] if ring[0] == ring[-1] else ring)), reverse=True)
    return rings[:8]


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


def _compact_component_bbox_polygon(lons: np.ndarray, lats: np.ndarray) -> list[list[float]]:
    points = np.column_stack([np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)])
    points = points[np.isfinite(points).all(axis=1)]
    if points.size == 0:
        return []
    lon_span = float(np.nanmax(points[:, 0]) - np.nanmin(points[:, 0]))
    lat_span = float(np.nanmax(points[:, 1]) - np.nanmin(points[:, 1]))
    # BBox is only a safe last-resort for compact blobs. For broad/low-risk
    # components it creates fake rectangular outlook sheets.
    if lon_span > 5.0 or lat_span > 4.0:
        return []
    return _bbox_polygon(points)


def _component_cell_union_polygons(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    component: np.ndarray,
    simplify_tolerance: float = 0.0,
) -> list[list[list[float]]]:
    try:
        from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
        from shapely.ops import unary_union
    except Exception:
        return []

    rows, cols = np.where(np.asarray(component, dtype=bool))
    if rows.size == 0:
        return []
    lon_step = _median_positive_spacing(np.diff(np.asarray(lon_grid, dtype=float), axis=1), fallback=0.10)
    lat_step = _median_positive_spacing(np.diff(np.asarray(lat_grid, dtype=float), axis=0), fallback=0.10)
    lon_pad = max(0.025, lon_step * 0.52)
    lat_pad = max(0.025, lat_step * 0.52)
    row_runs: list[Any] = []
    for row in np.unique(rows):
        run_cols = np.sort(cols[rows == row])
        if run_cols.size == 0:
            continue
        start = int(run_cols[0])
        prev = int(run_cols[0])
        for value in run_cols[1:]:
            col = int(value)
            if col == prev + 1:
                prev = col
                continue
            row_runs.append(_row_run_box(lon_grid, lat_grid, int(row), start, prev, lon_pad, lat_pad, box))
            start = prev = col
        row_runs.append(_row_run_box(lon_grid, lat_grid, int(row), start, prev, lon_pad, lat_pad, box))
    row_runs = [geom for geom in row_runs if geom is not None and not geom.is_empty]
    if not row_runs:
        return []
    try:
        unioned = unary_union(row_runs)
        if simplify_tolerance > 0.0:
            simplified = unioned.simplify(max(0.0, float(simplify_tolerance) * 0.35), preserve_topology=True)
            if not simplified.is_empty:
                unioned = simplified
    except Exception:
        return []

    polygons: list[Any] = []
    if isinstance(unioned, Polygon):
        polygons = [unioned]
    elif isinstance(unioned, MultiPolygon):
        polygons = list(unioned.geoms)
    elif isinstance(unioned, GeometryCollection):
        polygons = [geom for geom in unioned.geoms if isinstance(geom, Polygon)]
    rings: list[list[list[float]]] = []
    for polygon in polygons:
        if polygon.is_empty or polygon.area <= 0:
            continue
        coords = [[round(float(x), 4), round(float(y), 4)] for x, y in polygon.exterior.coords]
        coords = _normalize_exterior_ring(coords)
        if len(coords) >= 4:
            rings.append(coords)
    rings.sort(key=lambda ring: abs(_signed_ring_area(ring[:-1] if ring[0] == ring[-1] else ring)), reverse=True)
    return rings[:24]


def _row_run_box(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    row: int,
    start_col: int,
    end_col: int,
    lon_pad: float,
    lat_pad: float,
    box_factory: Any,
) -> Any:
    lons = np.asarray(lon_grid[row, start_col:end_col + 1], dtype=float)
    lats = np.asarray(lat_grid[row, start_col:end_col + 1], dtype=float)
    valid = np.isfinite(lons) & np.isfinite(lats)
    if not np.any(valid):
        return None
    min_lon = float(np.nanmin(lons[valid]) - lon_pad)
    max_lon = float(np.nanmax(lons[valid]) + lon_pad)
    min_lat = float(np.nanmin(lats[valid]) - lat_pad)
    max_lat = float(np.nanmax(lats[valid]) + lat_pad)
    if min_lon >= max_lon or min_lat >= max_lat:
        return None
    return box_factory(min_lon, min_lat, max_lon, max_lat)


def _median_positive_spacing(values: np.ndarray, fallback: float) -> float:
    arr = np.abs(np.asarray(values, dtype=float))
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size == 0:
        return float(fallback)
    return float(np.nanmedian(arr))


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


def _simplify_ring(coords: list[list[float]], tolerance: float = 0.0) -> list[list[float]]:
    if tolerance <= 0.0 or len(coords) < 8:
        return coords
    ring = coords[:-1] if coords[0] == coords[-1] else coords
    simplified = _rdp_closed_ring(ring, float(tolerance))
    if len(simplified) < 4:
        return coords
    return [[round(float(lon), 4), round(float(lat), 4)] for lon, lat in simplified]


def _rdp_closed_ring(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if len(points) < 4:
        return points
    anchor = min(range(len(points)), key=lambda idx: (points[idx][0], points[idx][1]))
    open_points = points[anchor:] + points[:anchor] + [points[anchor]]
    simplified = _rdp_line(open_points, tolerance)
    if simplified and simplified[0] == simplified[-1]:
        simplified = simplified[:-1]
    return simplified


def _rdp_line(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if len(points) <= 2:
        return points
    first = points[0]
    last = points[-1]
    max_distance = -1.0
    max_index = 0
    for idx in range(1, len(points) - 1):
        distance = _point_line_distance(points[idx], first, last)
        if distance > max_distance:
            max_distance = distance
            max_index = idx
    if max_distance > tolerance:
        left = _rdp_line(points[: max_index + 1], tolerance)
        right = _rdp_line(points[max_index:], tolerance)
        return left[:-1] + right
    return [first, last]


def _point_line_distance(point: list[float], start: list[float], end: list[float]) -> float:
    x, y = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return float(np.hypot(x - x1, y - y1))
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denom))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return float(np.hypot(x - proj_x, y - proj_y))


def _ring_extent_ok(coords: list[list[float]]) -> bool:
    if len(coords) < 4:
        return False
    lons = [coord[0] for coord in coords]
    lats = [coord[1] for coord in coords]
    return (max(lons) - min(lons)) <= 50.0 and (max(lats) - min(lats)) <= 30.0


def _signed_ring_area(coords: list[list[float]]) -> float:
    area = 0.0
    for idx, (x0, y0) in enumerate(coords):
        x1, y1 = coords[(idx + 1) % len(coords)]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def _round_nested(values: np.ndarray, digits: int = 3) -> list[list[float]]:
    return np.round(np.asarray(values, dtype=float), digits).tolist()


_philippines_land_polygon = None
_philippines_land_polygon_lock = threading.Lock()


def _load_philippines_land_polygon() -> Any:
    global _philippines_land_polygon
    if _philippines_land_polygon is not None:
        return _philippines_land_polygon
    with _philippines_land_polygon_lock:
        if _philippines_land_polygon is not None:
            return _philippines_land_polygon
        import json
        import os
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(base_dir, "public", "philippines-provinces.json")
        if not os.path.exists(path):
            path = os.path.join(os.getcwd(), "public", "philippines-provinces.json")
            
        with open(path, "r") as f:
            topo = json.load(f)
            
        transform = topo["transform"]
        scale = transform["scale"]
        translate = transform["translate"]
        arcs = topo["arcs"]
        
        decoded_arcs = []
        for arc in arcs:
            x, y = 0, 0
            decoded_arc = []
            for pt in arc:
                x += pt[0]
                y += pt[1]
                real_x = x * scale[0] + translate[0]
                real_y = y * scale[1] + translate[1]
                decoded_arc.append((real_x, real_y))
            decoded_arcs.append(decoded_arc)
            
        polygons = []
        for geom in topo["objects"]["default"]["geometries"]:
            g_type = geom["type"]
            g_arcs = geom["arcs"]
            
            if g_type == "Polygon":
                poly_rings = []
                for ring in g_arcs:
                    coords = []
                    for arc_idx in ring:
                        if arc_idx < 0:
                            arc_coords = decoded_arcs[~arc_idx][::-1]
                        else:
                            arc_coords = decoded_arcs[arc_idx]
                        
                        if not coords:
                            coords.extend(arc_coords)
                        else:
                            coords.extend(arc_coords[1:])
                    if len(coords) >= 4:
                        poly_rings.append(coords)
                if poly_rings:
                    polygons.append(Polygon(poly_rings[0], poly_rings[1:]))
                    
            elif g_type == "MultiPolygon":
                for poly in g_arcs:
                    poly_rings = []
                    for ring in poly:
                        coords = []
                        for arc_idx in ring:
                            if arc_idx < 0:
                                arc_coords = decoded_arcs[~arc_idx][::-1]
                            else:
                                arc_coords = decoded_arcs[arc_idx]
                            
                            if not coords:
                                coords.extend(arc_coords)
                            else:
                                coords.extend(arc_coords[1:])
                        if len(coords) >= 4:
                            poly_rings.append(coords)
                    if poly_rings:
                        polygons.append(Polygon(poly_rings[0], poly_rings[1:]))
                        
        _philippines_land_polygon = unary_union(polygons)
        return _philippines_land_polygon


def _is_philippines_grid(lat_grid: np.ndarray, lon_grid: np.ndarray) -> bool:
    return lat_grid is not None and lon_grid is not None and np.any(lon_grid > 0)


def _philippines_land_mask(lat_grid: np.ndarray, lon_grid: np.ndarray, buffer_deg: float = 0.0) -> np.ndarray:
    poly = _load_philippines_land_polygon()
    if buffer_deg > 0:
        poly = poly.buffer(buffer_deg)
    import shapely.vectorized
    return shapely.vectorized.contains(poly, lon_grid, lat_grid)


def _philippines_activity_land_mask(lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
    return _philippines_land_mask(lat_grid, lon_grid, buffer_deg=0.0)
