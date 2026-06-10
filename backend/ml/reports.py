"""SPC severe report matching helpers used by archive gathering and tests."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .features import HAZARD_KEYS

EARTH_RADIUS_KM = 6371.0088
MPH_TO_KT = 0.868976

INTENSITY_LABEL_KEYS = (
    "tornado_ef2_plus",
    "tornado_ef3_plus",
    "hail_2in_plus",
    "hail_3_5in_plus",
    "wind_56kt_plus",
    "wind_65kt_plus",
    "wind_74kt_plus",
    "wind_83kt_plus",
)


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


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.upper() in {"UNK", "UNKNOWN", "NA", "N/A"}:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if not match:
            return None
        value = match.group(0)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def parse_tornado_ef_scale(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip().upper()
        if not text:
            continue
        match = re.search(r"\b(?:EF|F)\s*([0-5])\b", text)
        if match:
            return int(match.group(1))
        number = _float_or_none(text)
        if number is not None and 0 <= number <= 5:
            return int(number)
    return None


def parse_hail_size_in(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None or number < 0:
        return None
    # SPC annual hail MAG is commonly stored in hundredths of an inch.
    if number >= 50:
        return number / 100.0
    return number


def parse_wind_speed_kt(value: Any, unit_hint: Any = None) -> float | None:
    number = _float_or_none(value)
    if number is None or number < 0:
        return None
    hint = str(unit_hint or "").strip().lower()
    if "kt" in hint or "knot" in hint:
        return number
    if "mph" in hint:
        return number * MPH_TO_KT
    # SPC public severe wind reports are usually listed in mph; archive tables
    # can omit the unit, so use the conservative SPC-display assumption.
    return number * MPH_TO_KT


def normalize_report_magnitude(hazard: str, value: Any, unit_hint: Any = None, *extra_values: Any) -> dict[str, Any]:
    hazard = str(hazard).lower()
    raw = None if value is None else str(value).strip()
    normalized: dict[str, Any] = {
        "magnitudeRaw": raw,
        "magnitude": _float_or_none(value),
        "magnitudeUnits": None,
    }
    if hazard == "tornado":
        ef_scale = parse_tornado_ef_scale(value, unit_hint, *extra_values)
        normalized["efScale"] = ef_scale
        normalized["magnitudeUnits"] = "EF" if ef_scale is not None else None
    elif hazard == "hail":
        hail_size_in = parse_hail_size_in(value)
        normalized["hailSizeIn"] = hail_size_in
        normalized["magnitudeUnits"] = "in" if hail_size_in is not None else None
    elif hazard == "wind":
        wind_speed_kt = parse_wind_speed_kt(value, unit_hint)
        normalized["windSpeedKt"] = wind_speed_kt
        normalized["windSpeedMph"] = wind_speed_kt / MPH_TO_KT if wind_speed_kt is not None else None
        normalized["magnitudeUnits"] = "kt"
    return normalized


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


def intensity_labels_for_sample(
    reports: Iterable[Mapping[str, Any]],
    sample_time: datetime,
    lat: float,
    lon: float,
    radius_km: float = 40.0,
    window_hours: float = 1.0,
) -> dict[str, int]:
    labels = {key: 0 for key in INTENSITY_LABEL_KEYS}
    for report in reports:
        hazard = str(report.get("hazard", "")).lower()
        if hazard not in HAZARD_KEYS:
            continue
        if not report_matches_sample(report, sample_time, lat, lon, hazard, radius_km, window_hours):
            continue
        if hazard == "tornado":
            ef_scale = parse_tornado_ef_scale(report.get("efScale"), report.get("magnitudeRaw"), report.get("magnitude"))
            if ef_scale is not None:
                labels["tornado_ef2_plus"] = max(labels["tornado_ef2_plus"], int(ef_scale >= 2))
                labels["tornado_ef3_plus"] = max(labels["tornado_ef3_plus"], int(ef_scale >= 3))
        elif hazard == "hail":
            hail_size_in = report.get("hailSizeIn")
            if hail_size_in is None:
                hail_size_in = parse_hail_size_in(report.get("magnitudeRaw") or report.get("magnitude"))
            if hail_size_in is not None:
                labels["hail_2in_plus"] = max(labels["hail_2in_plus"], int(float(hail_size_in) >= 2.0))
                labels["hail_3_5in_plus"] = max(labels["hail_3_5in_plus"], int(float(hail_size_in) >= 3.5))
        elif hazard == "wind":
            wind_speed_kt = report.get("windSpeedKt")
            if wind_speed_kt is None:
                wind_speed_kt = parse_wind_speed_kt(report.get("magnitudeRaw") or report.get("magnitude"), report.get("magnitudeUnits"))
            if wind_speed_kt is not None:
                speed = float(wind_speed_kt)
                labels["wind_56kt_plus"] = max(labels["wind_56kt_plus"], int(speed >= 56.0))
                labels["wind_65kt_plus"] = max(labels["wind_65kt_plus"], int(speed >= 65.0))
                labels["wind_74kt_plus"] = max(labels["wind_74kt_plus"], int(speed >= 74.0))
                labels["wind_83kt_plus"] = max(labels["wind_83kt_plus"], int(speed >= 83.0))
    return labels
