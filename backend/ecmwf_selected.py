"""Direct ECMWF IFS GRIB2 data access and caching for AutoOutlook."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .grib2 import decode_grib2

log = logging.getLogger(__name__)

DEFAULT_ECMWF_CACHE_DIR = Path(__file__).resolve().parent / "cache" / "ecmwf_selected"
ECMWF_REQUIRED_FIELDS = ("cape", "cin", "td2m", "t2m", "u10", "v10", "u500", "v500", "hgt500")

@dataclass(frozen=True)
class EcmwfCycle:
    run_date: str
    run_cycle: int

    @property
    def cycle_time(self) -> datetime:
        return datetime.strptime(f"{self.run_date}{self.run_cycle:02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)

    @property
    def label(self) -> str:
        return f"ECMWF {self.run_cycle:02d}Z {self.run_date}"


@dataclass(frozen=True)
class EcmwfHourRef:
    run_date: str
    run_cycle: int
    forecast_hour: int

    @property
    def cycle(self) -> EcmwfCycle:
        return EcmwfCycle(self.run_date, self.run_cycle)

    @property
    def valid_time(self) -> datetime:
        return self.cycle.cycle_time + timedelta(hours=self.forecast_hour)


@dataclass(frozen=True)
class SelectedEcmwfHour:
    lats: np.ndarray
    lons: np.ndarray
    fields: dict[str, np.ndarray]
    metadata: dict[str, Any]


def latest_available_ecmwf_cycle() -> EcmwfCycle:
    """Return a cycle representing today's candidate ECMWF run."""
    now = datetime.now(timezone.utc)
    # ECMWF runs are 00, 06, 12, 18 UTC.
    # We select the latest cycle that is likely complete (approx 4 hours old).
    check_time = now - timedelta(hours=4)
    run_cycle = (check_time.hour // 6) * 6
    run_date = check_time.strftime("%Y%m%d")
    return EcmwfCycle(run_date, run_cycle)


def fetch_selected_ecmwf_hour(
    run_date: str,
    run_cycle: int,
    forecast_hour: int,
    cache_dir: Path | str | None = DEFAULT_ECMWF_CACHE_DIR,
) -> SelectedEcmwfHour:
    """Download, parse, and extract the 9 required forecast arrays from ECMWF IFS."""
    if cache_dir is None:
        cache_dir = DEFAULT_ECMWF_CACHE_DIR
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / f"ecmwf_{run_date}_{run_cycle:02d}z_f{forecast_hour:02d}.npz"
    if cache_file.exists():
        log.info(f"ECMWF cache hit: {cache_file.name}")
        try:
            with np.load(cache_file, allow_pickle=False) as data:
                lats = np.asarray(data["lats"])
                lons = np.asarray(data["lons"])
                fields = {key: np.asarray(data[f"field_{key}"]) for key in ECMWF_REQUIRED_FIELDS}
                # Also load optional/derived fields if present
                for opt in ("cape_ml", "cape_mu", "cin_ml", "cin_mu"):
                    if f"field_{opt}" in data:
                        fields[opt] = np.asarray(data[f"field_{opt}"])
                
                metadata = json_loads_compat(str(data["metadata"]))
                return SelectedEcmwfHour(lats, lons, fields, metadata)
        except Exception as exc:
            log.warning(f"Failed to read ECMWF cache file {cache_file.name}: {exc}")

    # Not cached: fetch from ECMWF Open Data
    log.info(f"Downloading raw ECMWF IFS {run_date} {run_cycle:02d}Z F{forecast_hour:02d}...")
    
    # We dynamically load the ecmwf-opendata client here
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")

    temp_sfc = cache_dir / f"temp_sfc_{run_date}_{run_cycle:02d}_{forecast_hour:02d}.grib2"
    temp_pl = cache_dir / f"temp_pl_{run_date}_{run_cycle:02d}_{forecast_hour:02d}.grib2"

    try:
        # 1. Retrieve surface fields
        client.retrieve(
            time=run_cycle,
            step=forecast_hour,
            type="fc",
            param=["mucape", "2t", "2d", "10u", "10v"],
            target=str(temp_sfc)
        )
        
        # 2. Retrieve pressure level fields (500 hPa)
        client.retrieve(
            time=run_cycle,
            step=forecast_hour,
            type="fc",
            param=["u", "v", "z"],
            levelist=[500],
            target=str(temp_pl)
        )

        # Parse the downloaded files
        sfc_bytes = temp_sfc.read_bytes()
        pl_bytes = temp_pl.read_bytes()

        messages = decode_grib2(sfc_bytes) + decode_grib2(pl_bytes)
        lats, lons, fields = _ecmwf_messages_to_fields(messages)

        metadata = {
            "model": "ecmwf",
            "source": "ECMWF Open Data HRES",
            "cycle": f"ECMWF {run_cycle:02d}Z {run_date}",
            "cycleTimeISO": f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:8]}T{run_cycle:02d}:00:00Z",
            "forecastHour": forecast_hour,
            "resolution": "0.25 deg",
        }

        # Cache the results
        payload = {
            "lats": lats,
            "lons": lons,
            "metadata": json_dumps_compat(metadata),
        }
        for key in ECMWF_REQUIRED_FIELDS:
            payload[f"field_{key}"] = fields[key]
        for opt in ("cape_ml", "cape_mu", "cin_ml", "cin_mu"):
            if opt in fields:
                payload[f"field_{opt}"] = fields[opt]

        tmp_cache = cache_file.with_suffix(".tmp")
        with tmp_cache.open("wb") as fh:
            np.savez(fh, **payload)
        tmp_cache.replace(cache_file)

        return SelectedEcmwfHour(lats, lons, fields, metadata)

    finally:
        # Cleanup temp files
        for path in (temp_sfc, temp_pl):
            if path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass


def _ecmwf_messages_to_fields(messages: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    if not messages:
        raise ValueError("No GRIB2 messages decoded from ECMWF GRIB files")

    # The lat/lon grid in ECMWF Open Data is a regular global mesh
    lats = np.asarray(messages[0]["lats"], dtype=float)
    lons = np.asarray(messages[0]["lons"], dtype=float)
    
    # Normalize longitude values to [-180, 180]
    lons = np.where(lons > 180, lons - 360, lons)
    lons = np.where(lons < -180, lons + 360, lons)

    # In regular lat-lon GRIB2, lats/lons are 1D arrays representing the rows and columns.
    # Let's ensure they are 1D vectors:
    if lats.ndim > 1:
        lats = lats[:, 0]
    if lons.ndim > 1:
        lons = lons[0, :]

    # Sort longitudes monotonically to prevent contouring wraps/artifacts across the 180/-180 seam
    sort_idx = np.argsort(lons)
    lons = lons[sort_idx]

    fields: dict[str, np.ndarray] = {}

    for msg in messages:
        cat = msg.get("category")
        param = msg.get("parameter")
        vals = np.asarray(msg.get("values"), dtype=float)
        if vals.ndim == 2:
            vals = vals[:, sort_idx]
        
        # Standard GRIB2 mapping for ECMWF HRES:
        if cat == 7 and param == 6: # CAPE (Surface)
            fields["cape"] = vals
            # Clone surface CAPE to ML and MU for gridded feature compatibility
            fields["cape_ml"] = vals
            fields["cape_mu"] = vals
        elif cat == 7 and param == 7: # CIN (Surface)
            fields["cin"] = -np.abs(vals)
            fields["cin_ml"] = -np.abs(vals)
            fields["cin_mu"] = -np.abs(vals)
        elif cat == 0 and param == 0 and msg.get("level_type") == 103: # 2m Temperature
            fields["t2m"] = vals
        elif cat == 0 and param == 6 and msg.get("level_type") == 103: # 2m Dewpoint
            fields["td2m"] = vals
        elif cat == 2 and param == 2 and msg.get("level_type") == 103: # 10m U-wind
            fields["u10"] = vals
        elif cat == 2 and param == 3 and msg.get("level_type") == 103: # 10m V-wind
            fields["v10"] = vals
        elif cat == 2 and param == 2 and msg.get("level_type") == 100: # 500 hPa U-wind
            fields["u500"] = vals
        elif cat == 2 and param == 3 and msg.get("level_type") == 100: # 500 hPa V-wind
            fields["v500"] = vals
        elif cat == 3 and param == 4 and msg.get("level_type") == 100: # 500 hPa Geopotential (z)
            # Convert Geopotential (m2/s2) to Geopotential Height (gpm) by dividing by g (9.80665)
            fields["hgt500"] = vals / 9.80665
        elif cat == 3 and param == 5 and msg.get("level_type") == 100: # 500 hPa Geopotential Height (gh)
            fields["hgt500"] = vals

    # Add standard derived placeholders to satisfy features extractor if missing
    if "cape" in fields and "cape_ml" not in fields:
        fields["cape_ml"] = fields["cape"]
        fields["cape_mu"] = fields["cape"]
    if "cin" not in fields and "cape" in fields:
        fields["cin"] = np.zeros_like(fields["cape"])
        fields["cin_ml"] = np.zeros_like(fields["cape"])
        fields["cin_mu"] = np.zeros_like(fields["cape"])
    elif "cin" in fields and "cin_ml" not in fields:
        fields["cin_ml"] = fields["cin"]
        fields["cin_mu"] = fields["cin"]

    # Verify that all 9 required fields are present
    missing = [key for key in ECMWF_REQUIRED_FIELDS if key not in fields]
    if missing:
        raise ValueError(f"ECMWF GRIB payload missing required variables: {missing}")

    return lats, lons, fields


def json_dumps_compat(obj: Any) -> str:
    import json
    return json.dumps(obj)


def json_loads_compat(s: str) -> Any:
    import json
    return json.loads(s)
