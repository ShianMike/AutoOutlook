"""siphon-based fetch of HRRR subsets from Unidata's NOMADS THREDDS.

Uses the NetCDF Subset Service (NCSS) to grab only the variables and
forecast hours we need for a small CONUS bbox. Returns xarray Datasets.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import numpy as np
import xarray as xr
from siphon.catalog import TDSCatalog

log = logging.getLogger(__name__)

CATALOG_URL = (
    "https://thredds.ucar.edu/thredds/catalog/grib/NCEP/HRRR/"
    "CONUS_2p5km/catalog.xml"
)
GFS_CATALOG_URL = (
    "https://thredds.ucar.edu/thredds/catalog/grib/NCEP/GFS/"
    "Global_0p25deg/catalog.xml"
)

# CONUS bbox - matches what the frontend expects.
CONUS_BBOX = dict(north=50.0, south=24.0, west=-125.0, east=-66.0)

FORECAST_HOURS = list(range(0, 49))

# Variables we *want*. The HRRR catalog uses long names; we try a list of
# candidates per quantity and use whichever is present.
WANT = {
    "cape":   ["Convective_available_potential_energy_surface"],
    "cin":    ["Convective_inhibition_surface"],
    "td2m":   ["Dewpoint_temperature_height_above_ground"],
    "t2m":    ["Temperature_height_above_ground"],
    "pwat":   ["Precipitable_water_entire_atmosphere_single_layer",
               "Precipitable_water_entire_atmosphere"],
    "u_iso":  ["u-component_of_wind_isobaric"],
    "v_iso":  ["v-component_of_wind_isobaric"],
    "u10":    ["u-component_of_wind_height_above_ground"],
    "v10":    ["v-component_of_wind_height_above_ground"],
    "hgt_iso":["Geopotential_height_isobaric"],
    "srh01":  ["Storm_relative_helicity_height_above_ground_layer"],
}


class NomadsFetchError(RuntimeError):
    pass


def latest_dataset(catalog_url: str = CATALOG_URL, preferred_name: str = "Best HRRR"):
    """Open the THREDDS catalog and return the Best HRRR time-series dataset."""
    try:
        cat = TDSCatalog(catalog_url)
    except Exception as exc:  # network/DNS/SSL/etc.
        raise NomadsFetchError(f"Could not open THREDDS catalog: {exc}") from exc
    if not cat.datasets:
        raise NomadsFetchError("THREDDS catalog has no datasets")
    for name, ds in cat.datasets.items():
        if preferred_name in name:
            return ds
    return list(cat.datasets.values())[0]


def _pick_var(available: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    avail = set(available)
    for c in candidates:
        if c in avail:
            return c
    return None


def _fetch_subset(
    ds_meta,
    model_label: str,
    forecast_hours: list[int],
    spatial_stride: int | None,
) -> xr.Dataset:
    """Fetch a CONUS subset from a THREDDS grid dataset.

    Returns an xarray Dataset indexed by (time, lat, lon) (and isobaric
    for upper-air vars when available). Raises NomadsFetchError on any
    fatal issue so the caller can fall back to mock.
    """
    ncss = ds_meta.subset()
    available = set(ncss.variables)

    chosen: dict[str, str] = {}
    for short, candidates in WANT.items():
        v = _pick_var(available, candidates)
        if v is not None:
            chosen[short] = v

    if "cape" not in chosen:
        raise NomadsFetchError(
            f"{model_label} dataset is missing surface CAPE; cannot build outlook."
        )

    now = datetime.now(timezone.utc)
    start = now
    end = now + timedelta(hours=max(forecast_hours) + 1)

    query = ncss.query()
    query.time_range(start, end)
    query.lonlat_box(**CONUS_BBOX)
    query.variables(*chosen.values())
    if spatial_stride is not None:
        query.strides(spatial=spatial_stride)
    query.accept("netcdf4")

    try:
        nc = ncss.get_data(query)
    except Exception as exc:
        raise NomadsFetchError(f"NCSS request failed: {exc}") from exc

    # siphon returns either a netCDF4.Dataset (when netCDF4 was importable
    # at module load time) or the raw response bytes otherwise. Handle both.
    ds = _open_as_xarray(nc)

    # Attach our short names as data_vars aliases for convenience.
    aliases = {long: short for short, long in chosen.items()}
    ds = ds.rename({k: v for k, v in aliases.items() if k in ds.variables})
    return ds


def fetch_conus_subset(
    forecast_hours: list[int] = FORECAST_HOURS,
    timeout_s: float = 25.0,
) -> xr.Dataset:
    """Fetch a CONUS subset from the latest HRRR dataset."""
    ds_meta = latest_dataset(CATALOG_URL, "Best HRRR")
    # HRRR CONUS 2.5 km over 48h is too large at native spatial resolution for
    # the public THREDDS NCSS cap. Keep hourly valid times, but spatially stride
    # the grid so the backend can ingest each available HRRR hour.
    return _fetch_subset(ds_meta, "HRRR", forecast_hours, spatial_stride=2)


def fetch_gfs_conus_subset(
    forecast_hours: list[int] = FORECAST_HOURS,
    timeout_s: float = 25.0,
) -> xr.Dataset:
    """Fetch a CONUS subset from the latest GFS dataset for extended hours."""
    ds_meta = latest_dataset(GFS_CATALOG_URL, "Best GFS")
    return _fetch_subset(ds_meta, "GFS", forecast_hours, spatial_stride=2)


def _open_as_xarray(nc) -> xr.Dataset:
    """Open whatever siphon returned as an xarray Dataset."""
    # netCDF4.Dataset path
    try:
        from netCDF4 import Dataset as _NCDataset  # noqa: F401
    except Exception:
        _NCDataset = None  # type: ignore[assignment]

    if _NCDataset is not None and isinstance(nc, _NCDataset):
        try:
            store = xr.backends.NetCDF4DataStore(nc)
            return xr.open_dataset(store)
        except Exception as exc:
            raise NomadsFetchError(f"NetCDF4DataStore failed: {exc}") from exc

    # Raw bytes path: write to a temp file and open.
    if isinstance(nc, (bytes, bytearray, memoryview)):
        import os
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".nc", delete=False
            ) as tmp:
                tmp.write(bytes(nc))
                tmp_path = tmp.name
            try:
                return xr.open_dataset(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as exc:
            raise NomadsFetchError(f"Temp-file netCDF open failed: {exc}") from exc

    # Last-resort: try wrapping as if it were a dataset.
    try:
        store = xr.backends.NetCDF4DataStore(nc)
        return xr.open_dataset(store)
    except Exception as exc:
        raise NomadsFetchError(
            f"Could not wrap NCSS response (type={type(nc).__name__}): {exc}"
        ) from exc


def select_hour(ds: xr.Dataset, hour_offset: int, base_time: Optional[datetime] = None) -> xr.Dataset:
    """Slice a Dataset to its single time slot closest to base+hour_offset."""
    if base_time is None:
        base_time = datetime.now(timezone.utc)
    target = np.datetime64(int((base_time + timedelta(hours=hour_offset)).timestamp() * 1e9), "ns")
    # GFS datasets typically use 'time' or 'time1' coordinate.
    tname = next((c for c in ds.coords if str(c).startswith("time")), None)
    if tname is None:
        return ds
    return ds.sel({tname: target}, method="nearest")
