"""SPC severe report matching helpers used by archive gathering and tests."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .features import HAZARD_KEYS

EARTH_RADIUS_KM = 6371.0088


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lam = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def report_matches_sample(
    report: Mapping[str, Any],
    sample_time: datetime,
    lat: float,
    lon: float,
    hazard: str,
    radius_km: float = 40.0,
    window_hours: float = 1.0,
) -> bool:
    if str(report.get("hazard", "")).lower() != hazard:
        return False
    start = ensure_utc(sample_time)
    end = start + timedelta(hours=window_hours)
    report_time = report.get("time")
    if not isinstance(report_time, datetime):
        return False
    report_time = ensure_utc(report_time)
    if not (start <= report_time < end):
        return False
    try:
        report_lat = float(report["lat"])
        report_lon = float(report["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    return haversine_km(lat, lon, report_lat, report_lon) <= radius_km


def labels_for_sample(
    reports: Iterable[Mapping[str, Any]],
    sample_time: datetime,
    lat: float,
    lon: float,
    radius_km: float = 40.0,
    window_hours: float = 1.0,
) -> dict[str, int]:
    report_list = list(reports)
    return {
        hazard: int(any(
            report_matches_sample(report, sample_time, lat, lon, hazard, radius_km, window_hours)
            for report in report_list
        ))
        for hazard in HAZARD_KEYS
    }
