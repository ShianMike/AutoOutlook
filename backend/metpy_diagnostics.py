"""MetPy-based diagnostics from a GFS subset.

Computes per-grid-cell composite indices (bulk shear, surrogate SRH,
STP, SCP, EHI, SHIP). All inputs/outputs are plain numpy arrays - no
Pint quantities leak into the JSON layer.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

KT_PER_MS = 1.9438445


def _safe(arr, default=0.0):
    """Return a numpy array, replacing NaN with `default`."""
    a = np.asarray(arr, dtype=float)
    return np.where(np.isfinite(a), a, default)


def bulk_shear_kt(ds: xr.Dataset) -> np.ndarray:
    """0–6 km approximate bulk shear in knots.

    Uses 10 m wind for the surface and the 500 hPa wind as a proxy for
    the 6 km level. If isobaric winds are unavailable, returns zeros.
    """
    if "u_iso" not in ds.variables or "v_iso" not in ds.variables:
        return np.zeros(_shape_from(ds))

    iso = _isobaric_coord(ds)
    if iso is None:
        return np.zeros(_shape_from(ds))

    try:
        u500 = ds["u_iso"].sel({iso: 50000}, method="nearest").values
        v500 = ds["v_iso"].sel({iso: 50000}, method="nearest").values
    except Exception:
        return np.zeros(_shape_from(ds))

    if "u10" in ds.variables and "v10" in ds.variables:
        u_low = _squeeze_height(ds["u10"]).values
        v_low = _squeeze_height(ds["v10"]).values
    else:
        # Fall back to 1000 hPa for the low-level wind.
        try:
            u_low = ds["u_iso"].sel({iso: 100000}, method="nearest").values
            v_low = ds["v_iso"].sel({iso: 100000}, method="nearest").values
        except Exception:
            return np.zeros(_shape_from(ds))

    du = _safe(u500) - _safe(u_low)
    dv = _safe(v500) - _safe(v_low)
    shear_ms = np.hypot(du, dv)
    return shear_ms * KT_PER_MS


def surrogate_srh(ds: xr.Dataset) -> np.ndarray:
    """A coarse 0–1 km SRH surrogate from 1000 + 850 hPa winds.

    Produces values in m^2/s^2; useful for ranking but not authoritative.
    Real SRH needs a proper hodograph + storm motion.
    """
    iso = _isobaric_coord(ds)
    if iso is None or "u_iso" not in ds.variables:
        return np.zeros(_shape_from(ds))
    try:
        u_lo = ds["u_iso"].sel({iso: 100000}, method="nearest").values
        v_lo = ds["v_iso"].sel({iso: 100000}, method="nearest").values
        u_85 = ds["u_iso"].sel({iso: 85000}, method="nearest").values
        v_85 = ds["v_iso"].sel({iso: 85000}, method="nearest").values
    except Exception:
        return np.zeros(_shape_from(ds))
    du = _safe(u_85) - _safe(u_lo)
    dv = _safe(v_85) - _safe(v_lo)
    shear = np.hypot(du, dv)
    # rough SRH proxy
    return np.clip(shear * 6.0, 0, 1200)


def composites(
    cape: np.ndarray,
    shear_kt: np.ndarray,
    srh01: np.ndarray,
    cin: np.ndarray,
    td2m_K: np.ndarray,
    srh03: np.ndarray | None = None,
    mlcape: np.ndarray | None = None,
    mucape: np.ndarray | None = None,
    lcl_m: np.ndarray | None = None,
    cin_mu: np.ndarray | None = None,
    surface_pressure_pa: np.ndarray | None = None,
    t2m_K: np.ndarray | None = None,
    t850_K: np.ndarray | None = None,
    t700_K: np.ndarray | None = None,
    t500_K: np.ndarray | None = None,
    hgt850_m: np.ndarray | None = None,
    hgt700_m: np.ndarray | None = None,
    hgt500_m: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Compute published severe-weather composite parameters.

    Formulations used here intentionally avoid the old AutoOutlook proxy
    equations:

    * STP is the SPC fixed-layer Significant Tornado Parameter:
      sbCAPE, sbLCL, 0-1 km SRH, 0-6 km bulk wind difference, and sbCIN.
    * SCP follows the SPC/Thompson supercell-composite structure using the
      available fixed-layer 0-3 km SRH and 0-6 km BWD inputs plus the muCIN
      term. A true effective-layer SCP requires full soundings and should be
      added only when those fields are available.
    * SHIP follows the SPC Significant Hail Parameter and is set to 0 when
      mandatory hail-growth-zone fields are missing, rather than falling back
      to a simplified CAPE×shear proxy.
    """
    surface_cape = _safe(cape)
    mlcape_eff = _safe(mlcape if mlcape is not None else surface_cape)
    mucape_eff = _safe(mucape if mucape is not None else surface_cape)
    shear_kt = _safe(shear_kt)
    srh01 = _safe(srh01)
    srh03_eff = _safe(srh03 if srh03 is not None else srh01 * 1.4)
    cin = _safe(cin)
    cin_mu_eff = _safe(cin_mu if cin_mu is not None else cin)
    td2m_K = _safe(td2m_K, np.nan)
    shear_ms = shear_kt / KT_PER_MS

    # SPC fixed-layer STP, Thompson et al. / SPC mesoanalysis help.
    lcl_eff = _safe(
        lcl_m
        if lcl_m is not None
        else _bolton_lcl_m(t2m_K if t2m_K is not None else td2m_K + 8.0, td2m_K),
        2500.0,
    )
    stp_cape_term = np.clip(surface_cape / 1500.0, 0.0, 1.5)
    stp_lcl_term = np.where(
        lcl_eff < 1000.0,
        1.0,
        np.where(lcl_eff > 2000.0, 0.0, (2000.0 - lcl_eff) / 1000.0),
    )
    stp_srh_term = np.clip(np.maximum(srh01, 0.0) / 150.0, 0.0, 1.5)
    stp_shear_term = np.where(shear_ms < 12.5, 0.0, np.clip(shear_ms / 20.0, 0.0, 1.5))
    stp_cin_term = np.where(
        cin > -50.0, 1.0, np.where(cin < -200.0, 0.0, (200.0 + cin) / 150.0)
    )
    stp = np.clip(
        stp_cape_term * stp_lcl_term * stp_srh_term * stp_shear_term * stp_cin_term,
        0.0,
        12.0,
    )

    # SPC SCP structure with fixed-layer substitutes for ESRH/EBWD when full
    # effective-layer soundings are unavailable.
    scp_shear_term = np.where(shear_ms < 10.0, 0.0, np.minimum(shear_ms / 20.0, 1.0))
    scp_cin_term = np.where(
        cin_mu_eff > -40.0,
        1.0,
        np.clip(-40.0 / np.minimum(cin_mu_eff, -1e-6), 0.0, 1.0),
    )
    scp = np.clip(
        (mucape_eff / 1000.0)
        * (np.maximum(srh03_eff, 0.0) / 50.0)
        * scp_shear_term
        * scp_cin_term,
        0.0,
        24.0,
    )

    ehi = (mlcape_eff * np.maximum(srh01, 0.0)) / 160_000.0

    mixing_ratio = _mixing_ratio_gkg(td2m_K, surface_pressure_pa)
    lapse_rate = _lapse_rate_700_500_c_km(t700_K, t500_K, hgt700_m, hgt500_m)
    freezing_level = _freezing_level_m(
        t2m_K, t850_K, t700_K, t500_K, hgt850_m, hgt700_m, hgt500_m
    )
    ship_available = _ship_required_fields_available(
        mucape_eff,
        mixing_ratio,
        lapse_rate,
        t500_K,
        shear_ms,
        freezing_level,
    )
    ship = _significant_hail_parameter(
        mucape_eff,
        mixing_ratio,
        lapse_rate,
        t500_K,
        shear_ms,
        freezing_level,
    )

    tor_comp = stp * 0.6 + np.clip(srh01 / 200.0, 0.0, 1.5)
    return dict(
        stp=stp,
        scp=scp,
        ehi=ehi,
        ship=ship,
        tor_comp=tor_comp,
        lapse_rate_700_500=lapse_rate,
        freezing_level_m=freezing_level,
        mixing_ratio_gkg=mixing_ratio,
        ship_available=ship_available,
    )


def _bolton_lcl_m(t_K: np.ndarray, td_K: np.ndarray) -> np.ndarray:
    """Bolton-style LCL height approximation in meters AGL."""
    t = _safe(t_K, np.nan)
    td = _safe(td_K, np.nan)
    return np.clip(125.0 * np.maximum(t - td, 0.0), 0.0, 4000.0)


def _mixing_ratio_gkg(td_K: np.ndarray, pressure_pa: np.ndarray | None) -> np.ndarray:
    """Mixing ratio from dewpoint and pressure, in g/kg.

    SHIP requires the MU-parcel mixing ratio. With only near-surface HRRR
    dewpoint available, this is the nearest physically computed parcel
    moisture input; if pressure is missing, return NaN so SHIP is unavailable
    instead of using a hidden climatological pressure proxy.
    """
    td = _safe(td_K, np.nan)
    if pressure_pa is None:
        return np.full_like(td, np.nan, dtype=float)
    pressure_hpa = _safe(pressure_pa, np.nan) / 100.0
    td_c = td - 273.15
    vapor_pressure_hpa = 6.112 * np.exp((17.67 * td_c) / np.maximum(td_c + 243.5, 1e-6))
    valid = (
        np.isfinite(pressure_hpa)
        & np.isfinite(vapor_pressure_hpa)
        & (pressure_hpa > vapor_pressure_hpa)
    )
    ratio = (
        621.97
        * vapor_pressure_hpa
        / np.maximum(pressure_hpa - vapor_pressure_hpa, 1e-6)
    )
    return np.where(valid, ratio, np.nan)


def _lapse_rate_700_500_c_km(
    t700_K: np.ndarray | None,
    t500_K: np.ndarray | None,
    hgt700_m: np.ndarray | None,
    hgt500_m: np.ndarray | None,
) -> np.ndarray:
    if t700_K is None or t500_K is None or hgt700_m is None or hgt500_m is None:
        base = (
            t700_K
            if t700_K is not None
            else t500_K
            if t500_K is not None
            else hgt700_m
            if hgt700_m is not None
            else hgt500_m
        )
        return np.full_like(
            _safe(base if base is not None else np.array([np.nan]), np.nan),
            np.nan,
            dtype=float,
        )
    t700_c = _safe(t700_K, np.nan) - 273.15
    t500_c = _safe(t500_K, np.nan) - 273.15
    h700 = _safe(hgt700_m, np.nan)
    h500 = _safe(hgt500_m, np.nan)
    depth_km = (h500 - h700) / 1000.0
    lapse = (t700_c - t500_c) / np.maximum(depth_km, 1e-6)
    return np.where((depth_km > 0.2) & np.isfinite(lapse), lapse, np.nan)


def _freezing_level_m(
    t2m_K: np.ndarray | None,
    t850_K: np.ndarray | None,
    t700_K: np.ndarray | None,
    t500_K: np.ndarray | None,
    hgt850_m: np.ndarray | None,
    hgt700_m: np.ndarray | None,
    hgt500_m: np.ndarray | None,
) -> np.ndarray:
    levels: list[tuple[np.ndarray, np.ndarray]] = []
    if t2m_K is not None:
        levels.append(
            (
                _safe(t2m_K, np.nan) - 273.15,
                np.zeros_like(_safe(t2m_K, np.nan), dtype=float),
            )
        )
    if t850_K is not None and hgt850_m is not None:
        levels.append((_safe(t850_K, np.nan) - 273.15, _safe(hgt850_m, np.nan)))
    if t700_K is not None and hgt700_m is not None:
        levels.append((_safe(t700_K, np.nan) - 273.15, _safe(hgt700_m, np.nan)))
    if t500_K is not None and hgt500_m is not None:
        levels.append((_safe(t500_K, np.nan) - 273.15, _safe(hgt500_m, np.nan)))
    if len(levels) < 2:
        base = _safe(t2m_K if t2m_K is not None else np.array([np.nan]), np.nan)
        return np.full_like(base, np.nan, dtype=float)

    out = np.full_like(levels[0][0], np.nan, dtype=float)
    for (t_low, z_low), (t_high, z_high) in zip(levels, levels[1:]):
        crosses = (
            np.isnan(out)
            & np.isfinite(t_low)
            & np.isfinite(t_high)
            & np.isfinite(z_low)
            & np.isfinite(z_high)
            & (z_high > z_low)
            & (t_low >= 0.0)
            & (t_high <= 0.0)
        )
        frac = np.clip(t_low / np.maximum(t_low - t_high, 1e-6), 0.0, 1.0)
        out = np.where(crosses, z_low + frac * (z_high - z_low), out)
    all_below = np.isnan(out) & (levels[0][0] < 0.0)
    all_above = np.isnan(out) & (levels[-1][0] > 0.0)
    out = np.where(all_below, 0.0, out)
    out = np.where(all_above, levels[-1][1], out)
    return out


def _significant_hail_parameter(
    mucape: np.ndarray,
    mixing_ratio_gkg: np.ndarray,
    lapse_rate_700_500: np.ndarray,
    t500_K: np.ndarray | None,
    shear_ms: np.ndarray,
    freezing_level_m: np.ndarray,
) -> np.ndarray:
    if t500_K is None:
        return np.zeros_like(mucape, dtype=float)
    t500_c = _safe(t500_K, np.nan) - 273.15
    valid = (
        np.isfinite(mucape)
        & np.isfinite(mixing_ratio_gkg)
        & np.isfinite(lapse_rate_700_500)
        & np.isfinite(t500_c)
        & np.isfinite(shear_ms)
        & np.isfinite(freezing_level_m)
        & (mucape > 0.0)
    )
    mr_term = np.clip(mixing_ratio_gkg, 11.0, 13.6)
    shear_term = np.clip(shear_ms, 7.0, 27.0)
    temp_term = np.maximum(-t500_c, 5.5)
    base = (
        mucape * mr_term * lapse_rate_700_500 * temp_term * shear_term
    ) / 42_000_000.0
    cape_modifier = np.where(mucape < 1300.0, np.clip(mucape / 1300.0, 0.0, 1.0), 1.0)
    lapse_modifier = np.where(
        lapse_rate_700_500 < 5.8, np.clip(lapse_rate_700_500 / 5.8, 0.0, 1.0), 1.0
    )
    freezing_modifier = np.where(
        freezing_level_m < 2400.0, np.clip(freezing_level_m / 2400.0, 0.0, 1.0), 1.0
    )
    ship = base * cape_modifier * lapse_modifier * freezing_modifier
    return np.where(valid, np.clip(ship, 0.0, 8.0), 0.0)


def _ship_required_fields_available(
    mucape: np.ndarray,
    mixing_ratio_gkg: np.ndarray,
    lapse_rate_700_500: np.ndarray,
    t500_K: np.ndarray | None,
    shear_ms: np.ndarray,
    freezing_level_m: np.ndarray,
) -> np.ndarray:
    if t500_K is None:
        return np.zeros_like(mucape, dtype=float)
    t500_c = _safe(t500_K, np.nan) - 273.15
    return (
        np.isfinite(mucape)
        & np.isfinite(mixing_ratio_gkg)
        & np.isfinite(lapse_rate_700_500)
        & np.isfinite(t500_c)
        & np.isfinite(shear_ms)
        & np.isfinite(freezing_level_m)
    ).astype(float)


def _isobaric_coord(ds: xr.Dataset) -> str | None:
    for c in ds.coords:
        n = str(c).lower()
        if "isobar" in n or n.startswith("p") or "press" in n:
            return c
    return None


def _shape_from(ds: xr.Dataset) -> tuple[int, ...]:
    if "cape" in ds.variables:
        return ds["cape"].shape
    return tuple()


def _squeeze_height(da: xr.DataArray) -> xr.DataArray:
    # Drop singleton height_above_ground dims so we get a (lat, lon) array.
    for c in list(da.coords):
        if "height_above_ground" in str(c) and da[c].size == 1:
            da = da.squeeze(c, drop=True)
    return da
