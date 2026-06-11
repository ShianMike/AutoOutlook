"""Shared feature schema for training and live ML inference."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Mapping

FEATURE_SCHEMA_VERSION = "ml-features-v5-location-refc-temporal"
HAZARD_KEYS = ("tornado", "hail", "wind", "thunder")

FEATURE_NAMES = (
    "forecastHour",
    "sampleLat",
    "sampleLon",
    "mlcape",
    "mucape",
    "sbcape",
    "cape3km",
    "cape180",
    "cin",
    "cinSb",
    "cinMl",
    "cinMu",
    "cin180",
    "sfcDewpointF",
    "sfcTempF",
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
    "lapseRate700500CPerKm",
    "freezingLevelM",
    "surfacePressurePa",
    "refc",
    "hgt500",
    "validHourSin",
    "validHourCos",
    "monthSin",
    "monthCos",
    "dayOfYearSin",
    "dayOfYearCos",
)

FEATURE_DEFAULTS: dict[str, float] = {
    "sampleLat": 0.0,
    "sampleLon": 0.0,
    "sfcDewpointF": 50.0,
    "sfcTempF": 58.0,
    "pwatIn": 0.8,
    "lclM": 1500.0,
    "moistureDepthM": 1500.0,
    "surfacePressurePa": 101325.0,
    "refc": 0.0,
    "hgt500": 5700.0,
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _first_value(ingredients: Mapping[str, Any], names: tuple[str, ...], default: float = 0.0) -> float:
    for name in names:
        if name in ingredients:
            return _num(ingredients.get(name), default)
    return default


def _parse_valid_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _valid_time_from_run_parts(run_date: Any, run_cycle: Any, forecast_hour: Any) -> datetime | None:
    if not run_date:
        return None
    text = str(run_date).strip()
    if len(text) < 8:
        return None
    try:
        base = datetime.strptime(text[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return base + timedelta(hours=_num(run_cycle) + _num(forecast_hour))


def temporal_feature_values(
    *,
    valid_time_iso: Any = None,
    run_date: Any = None,
    run_cycle: Any = None,
    forecast_hour: Any = None,
) -> dict[str, float]:
    """Return cyclic valid-hour and seasonal encodings for one forecast row."""
    valid_time = _parse_valid_time(valid_time_iso) or _valid_time_from_run_parts(
        run_date, run_cycle, forecast_hour
    )

    if valid_time is None:
        hour = _num(forecast_hour) % 24.0
        hour_angle = 2.0 * math.pi * hour / 24.0
        return {
            "validHourSin": math.sin(hour_angle),
            "validHourCos": math.cos(hour_angle),
            "monthSin": 0.0,
            "monthCos": 0.0,
            "dayOfYearSin": 0.0,
            "dayOfYearCos": 0.0,
        }

    hour = valid_time.hour + valid_time.minute / 60.0 + valid_time.second / 3600.0
    hour_angle = 2.0 * math.pi * hour / 24.0
    month_angle = 2.0 * math.pi * (valid_time.month - 1) / 12.0
    day_angle = 2.0 * math.pi * (valid_time.timetuple().tm_yday - 1) / 366.0
    return {
        "validHourSin": math.sin(hour_angle),
        "validHourCos": math.cos(hour_angle),
        "monthSin": math.sin(month_angle),
        "monthCos": math.cos(month_angle),
        "dayOfYearSin": math.sin(day_angle),
        "dayOfYearCos": math.cos(day_angle),
    }


def feature_vector(
    ingredients: Mapping[str, Any],
    forecast_hour: int | float,
    *,
    sample_lat: Any = None,
    sample_lon: Any = None,
    valid_time_iso: Any = None,
    run_date: Any = None,
    run_cycle: Any = None,
) -> list[float]:
    """Return the exact ordered feature vector used by every model artifact."""
    temporal = temporal_feature_values(
        valid_time_iso=valid_time_iso or ingredients.get("validTimeISO"),
        run_date=run_date or ingredients.get("runDate"),
        run_cycle=run_cycle if run_cycle is not None else ingredients.get("runCycle"),
        forecast_hour=forecast_hour,
    )
    return [
        _num(forecast_hour),
        _num(sample_lat if sample_lat is not None else ingredients.get("sampleLat")),
        _num(sample_lon if sample_lon is not None else ingredients.get("sampleLon")),
        _num(ingredients.get("mlcape")),
        _num(ingredients.get("mucape")),
        _num(ingredients.get("sbcape")),
        _num(ingredients.get("cape3km")),
        _num(ingredients.get("cape180")),
        _num(ingredients.get("cin")),
        _num(ingredients.get("cinSb")),
        _num(ingredients.get("cinMl")),
        _num(ingredients.get("cinMu")),
        _num(ingredients.get("cin180")),
        _num(ingredients.get("sfcDewpointF"), 50.0),
        _num(ingredients.get("sfcTempF"), 58.0),
        _num(ingredients.get("pwatIn"), 0.8),
        _num(ingredients.get("lclM"), 1500.0),
        _num(ingredients.get("moistureDepthM"), 1500.0),
        _num(ingredients.get("srh01")),
        _num(ingredients.get("srh03")),
        _num(ingredients.get("shear06Kt")),
        _num(ingredients.get("stormRelWindKt")),
        _num(ingredients.get("stp")),
        _num(ingredients.get("scp")),
        _num(ingredients.get("ehi")),
        _num(ingredients.get("ship")),
        _num(ingredients.get("lapseRate700500CPerKm")),
        _num(ingredients.get("freezingLevelM")),
        _num(ingredients.get("surfacePressurePa")),
        _first_value(ingredients, ("refc", "hrrrRefcDbz"), 0.0),
        _num(ingredients.get("hgt500"), 5700.0),
        temporal["validHourSin"],
        temporal["validHourCos"],
        temporal["monthSin"],
        temporal["monthCos"],
        temporal["dayOfYearSin"],
        temporal["dayOfYearCos"],
    ]


def feature_row(
    ingredients: Mapping[str, Any],
    forecast_hour: int | float,
    *,
    sample_lat: Any = None,
    sample_lon: Any = None,
    valid_time_iso: Any = None,
    run_date: Any = None,
    run_cycle: Any = None,
) -> dict[str, float]:
    return dict(zip(
        FEATURE_NAMES,
        feature_vector(
            ingredients,
            forecast_hour,
            sample_lat=sample_lat,
            sample_lon=sample_lon,
            valid_time_iso=valid_time_iso,
            run_date=run_date,
            run_cycle=run_cycle,
        ),
        strict=True,
    ))


def ensure_feature_frame_columns(frame: Any) -> Any:
    """Backfill derived model feature columns on a pandas-like training frame."""
    import numpy as np
    import pandas as pd

    if "refc" not in frame.columns:
        if "hrrrRefcDbz" in frame.columns:
            frame["refc"] = frame["hrrrRefcDbz"]
        elif "hrrrRefd1kmDbz" in frame.columns:
            frame["refc"] = frame["hrrrRefd1kmDbz"]
        else:
            frame["refc"] = FEATURE_DEFAULTS["refc"]

    for name, default in FEATURE_DEFAULTS.items():
        if name not in frame.columns:
            frame[name] = default

    missing_temporal = [name for name in FEATURE_NAMES if name.endswith(("Sin", "Cos")) and name not in frame.columns]
    if missing_temporal:
        valid_time = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
        if "validTimeISO" in frame.columns:
            valid_time = pd.to_datetime(frame["validTimeISO"], utc=True, errors="coerce")
        if "runDate" in frame.columns:
            run_dates = pd.to_datetime(frame["runDate"].astype(str).str.slice(0, 8), format="%Y%m%d", utc=True, errors="coerce")
            run_cycle = pd.to_numeric(frame["runCycle"], errors="coerce").fillna(0.0) if "runCycle" in frame.columns else 0.0
            forecast = pd.to_numeric(frame["forecastHour"], errors="coerce").fillna(0.0) if "forecastHour" in frame.columns else 0.0
            fallback_time = run_dates + pd.to_timedelta(run_cycle + forecast, unit="h")
            valid_time = valid_time.fillna(fallback_time)

        forecast_for_hour = (
            pd.to_numeric(frame["forecastHour"], errors="coerce").fillna(0.0)
            if "forecastHour" in frame.columns
            else pd.Series(0.0, index=frame.index)
        )
        hour = valid_time.dt.hour + valid_time.dt.minute / 60.0 + valid_time.dt.second / 3600.0
        hour = hour.where(valid_time.notna(), forecast_for_hour % 24.0)
        hour_angle = 2.0 * np.pi * hour / 24.0
        frame["validHourSin"] = np.sin(hour_angle)
        frame["validHourCos"] = np.cos(hour_angle)

        month = valid_time.dt.month
        month_angle = 2.0 * np.pi * (month.fillna(1.0) - 1.0) / 12.0
        frame["monthSin"] = np.where(month.notna(), np.sin(month_angle), 0.0)
        frame["monthCos"] = np.where(month.notna(), np.cos(month_angle), 0.0)

        day_of_year = valid_time.dt.dayofyear
        day_angle = 2.0 * np.pi * (day_of_year.fillna(1.0) - 1.0) / 366.0
        frame["dayOfYearSin"] = np.where(day_of_year.notna(), np.sin(day_angle), 0.0)
        frame["dayOfYearCos"] = np.where(day_of_year.notna(), np.cos(day_angle), 0.0)

    for name in FEATURE_NAMES:
        if name not in frame.columns:
            frame[name] = FEATURE_DEFAULTS.get(name, 0.0)
        frame[name] = pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(
            FEATURE_DEFAULTS.get(name, 0.0)
        )
    return frame


def feature_schema_hash() -> str:
    payload = {
        "version": FEATURE_SCHEMA_VERSION,
        "featureNames": FEATURE_NAMES,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
