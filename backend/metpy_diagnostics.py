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
) -> dict[str, np.ndarray]:
    """Compute STP/SCP/EHI/SHIP/TorComp from already-derived fields."""
    surface_cape = _safe(cape)
    mlcape_eff = _safe(mlcape if mlcape is not None else surface_cape)
    mucape_eff = _safe(mucape if mucape is not None else surface_cape)
    shear_kt = _safe(shear_kt)
    srh01 = _safe(srh01)
    srh03_eff = _safe(srh03 if srh03 is not None else srh01 * 1.4)
    cin = _safe(cin)

    cape_term = np.clip(mlcape_eff / 1500.0, 0, 1.5)
    srh_term  = np.clip(srh01 / 150.0, 0, 1.5)
    shr_term  = np.clip((shear_kt - 12.5) / 12.5, 0, 1.5)
    cin_term  = np.where(cin > -50, 1.0,
                np.where(cin > -150, 1.0 - (np.abs(cin) - 50) / 100.0, 0.0))
    # Coarse LCL surrogate from Td near surface. Td > 65F => low LCL.
    td_F = (td2m_K - 273.15) * 9 / 5 + 32
    lcl_term = np.where(td_F >= 67, 1.0, np.clip((td_F - 50) / 17.0, 0, 1.0))
    stp = np.clip(cape_term * srh_term * shr_term * lcl_term * cin_term, 0, 6)

    scp = np.clip((mucape_eff / 1000.0) * (np.clip(srh03_eff / 50, 0, 6)) * np.clip(shear_kt / 20, 0, 1.5), 0, 12)
    ehi = (mlcape_eff * srh01) / 160_000.0
    ship = np.clip((mucape_eff / 2500.0) * (shear_kt / 30.0) * 0.6, 0, 6)
    tor_comp = stp * 0.6 + np.clip(srh01 / 200, 0, 1.5)
    return dict(stp=stp, scp=scp, ehi=ehi, ship=ship, tor_comp=tor_comp)


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
