"""Local historical risk-event verification helpers.

These helpers are intentionally local/static oriented. They define which event
dates are allowed, how the event-day 00Z HRRR cycle maps to the combined-risk
window, and how SPC daily report CSVs are parsed for PNG rendering.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import requests

from backend.ml.gridded_outlook import SPC_RISK_LABELS

MIN_ENH_PLUS_EVENT_DATE = date(2026, 3, 1)
DEFAULT_ENH_PLUS_EVENT_DATES = (
    date(2026, 3, 5),
    date(2026, 3, 6),
    date(2026, 3, 7),
    date(2026, 3, 15),
    date(2026, 3, 16),
    date(2026, 3, 26),
    date(2026, 4, 3),
    date(2026, 4, 4),
    date(2026, 4, 10),
    date(2026, 4, 14),
    date(2026, 4, 15),
    date(2026, 4, 17),
    date(2026, 4, 23),
    date(2026, 4, 24),
    date(2026, 4, 25),
    date(2026, 4, 27),
    date(2026, 4, 28),
    date(2026, 5, 10),
    date(2026, 5, 16),
    date(2026, 5, 17),
    date(2026, 5, 18),
)
EVENT_WINDOW_START_HOUR_UTC = 12
EVENT_WINDOW_END_HOUR_UTC = 12
EVENT_CYCLE_HOUR_UTC = 0
ENH_PLUS_MIN_ORDINAL = SPC_RISK_LABELS.index("ENH")
MODEL_IDENTITY_KEYS = (
    "version",
    "artifactType",
    "featureSchemaVersion",
    "featureSchemaHash",
)


@dataclass(frozen=True)
class HistoricalEventWindow:
    event_date: date
    cycle_time: datetime
    start_time: datetime
    end_time: datetime
    forecast_hours: tuple[int, ...]

    @property
    def start_iso(self) -> str:
        return _iso_z(self.start_time)

    @property
    def end_iso(self) -> str:
        return _iso_z(self.end_time)

    @property
    def cycle_iso(self) -> str:
        return _iso_z(self.cycle_time)


def parse_event_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def validate_event_date(
    event_date: date,
    *,
    today: date | None = None,
    min_date: date = MIN_ENH_PLUS_EVENT_DATE,
) -> date:
    max_date = today or today_utc()
    if event_date < min_date:
        raise ValueError(
            f"Historical ENH+ fallback events cannot be earlier than {min_date.isoformat()}: "
            f"{event_date.isoformat()}"
        )
    if event_date > max_date:
        raise ValueError(
            f"Historical ENH+ fallback events cannot be later than the current date "
            f"({max_date.isoformat()}): {event_date.isoformat()}"
        )
    return event_date


def resolve_event_dates(
    values: Sequence[str] | None,
    *,
    today: date | None = None,
) -> list[date]:
    if values:
        raw_dates = [parse_event_date(value) for value in values]
    else:
        raw_dates = list(DEFAULT_ENH_PLUS_EVENT_DATES)
    return [validate_event_date(item, today=today) for item in raw_dates]


def event_window_for_date(event_date: date) -> HistoricalEventWindow:
    cycle_time = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        EVENT_CYCLE_HOUR_UTC,
        tzinfo=timezone.utc,
    )
    start_time = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        EVENT_WINDOW_START_HOUR_UTC,
        tzinfo=timezone.utc,
    )
    end_time = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        EVENT_WINDOW_END_HOUR_UTC,
        tzinfo=timezone.utc,
    )
    if end_time <= start_time:
        end_time += timedelta(days=1)

    first_hour = int((start_time - cycle_time).total_seconds() // 3600)
    last_hour = int((end_time - cycle_time).total_seconds() // 3600)
    if first_hour < 0 or last_hour > 48:
        raise ValueError(
            f"Event window {start_time.isoformat()} to {end_time.isoformat()} "
            "does not fit inside HRRR f00..f48 from the 00Z event cycle."
        )
    return HistoricalEventWindow(
        event_date=event_date,
        cycle_time=cycle_time,
        start_time=start_time,
        end_time=end_time,
        forecast_hours=tuple(range(first_hour, last_hour + 1)),
    )


def event_slug(event_date: date) -> str:
    window = event_window_for_date(event_date)
    first_hour = window.forecast_hours[0]
    last_hour = window.forecast_hours[-1]
    return f"{event_date.isoformat()}-hrrr{EVENT_CYCLE_HOUR_UTC:02d}z-f{first_hour:02d}-f{last_hour:02d}"


def artifact_uses_model(
    artifact_index: Mapping[str, Any],
    expected_model: Mapping[str, Any],
) -> bool:
    artifact_model = artifact_index.get("model")
    if not isinstance(artifact_model, Mapping):
        return False
    return all(
        bool(expected_model.get(key))
        and artifact_model.get(key) == expected_model.get(key)
        for key in MODEL_IDENTITY_KEYS
    )


def risk_ordinal(label: Any) -> int:
    normalized = str(label or "").upper().strip()
    if normalized == "MOD":
        normalized = "MDT"
    try:
        return SPC_RISK_LABELS.index(normalized)
    except ValueError:
        return 0


def risk_label_for_ordinal(ordinal: int) -> str:
    if 0 <= ordinal < len(SPC_RISK_LABELS):
        return SPC_RISK_LABELS[ordinal]
    return "NONE"


def max_spc_category(spc_geojson: Mapping[str, Any]) -> tuple[str, int]:
    max_ordinal = 0
    for feature in spc_geojson.get("features", []):
        props = feature.get("properties", {}) if isinstance(feature, Mapping) else {}
        max_ordinal = max(max_ordinal, risk_ordinal(props.get("LABEL") or props.get("LABEL2")))
    return risk_label_for_ordinal(max_ordinal), max_ordinal


def ensure_enh_plus_spc_event(spc_geojson: Mapping[str, Any], event_date: date) -> tuple[str, int]:
    label, ordinal = max_spc_category(spc_geojson)
    if ordinal < ENH_PLUS_MIN_ORDINAL:
        raise ValueError(
            f"{event_date.isoformat()} is not an SPC ENH+ Day 1 event; max category is {label}."
        )
    return label, ordinal


def fetch_spc_daily_storm_reports(
    target_date: date,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-historical-event-verifier/1.0")
    try:
        reports: list[dict[str, Any]] = []
        date_token = target_date.strftime("%y%m%d")
        for hazard_token, hazard_name in (("torn", "tornado"), ("hail", "hail"), ("wind", "wind")):
            url = f"https://www.spc.noaa.gov/climo/reports/{date_token}_rpts_{hazard_token}.csv"
            response = session.get(url, timeout=20)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            reports.extend(_parse_spc_report_csv(response.text, hazard_name, url))
        return reports
    finally:
        if own_session:
            session.close()


def report_counts(reports: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"tornado": 0, "hail": 0, "wind": 0, "total": 0}
    for report in reports:
        kind = str(report.get("type") or "")
        if kind in counts:
            counts[kind] += 1
            counts["total"] += 1
    return counts


def filter_spc_reports_for_event_window(
    reports: Iterable[Mapping[str, Any]],
    window: HistoricalEventWindow,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for report in reports:
        report_time = _report_datetime_utc(window.event_date, report.get("time"))
        if report_time is None:
            continue
        if window.start_time <= report_time <= window.end_time:
            item = dict(report)
            item["timeISO"] = _iso_z(report_time)
            filtered.append(item)
    return filtered


def _parse_spc_report_csv(text: str, hazard_name: str, source_url: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    reports: list[dict[str, Any]] = []
    for row in reader:
        normalized = {_normalize_header(key): value for key, value in row.items() if key is not None}
        try:
            lat = float(_row_value(normalized, "lat"))
            lon = float(_row_value(normalized, "lon"))
        except (TypeError, ValueError):
            continue
        if not (20.0 <= lat <= 55.0 and -130.0 <= lon <= -60.0):
            continue
        reports.append(
            {
                "type": hazard_name,
                "time": _row_value(normalized, "time"),
                "value": _row_value(normalized, "f_scale", "size", "speed", "sz", "spd"),
                "location": _row_value(normalized, "location", "loc"),
                "county": _row_value(normalized, "county"),
                "state": _row_value(normalized, "state"),
                "lat": lat,
                "lon": lon,
                "comment": _row_value(normalized, "comments", "comment"),
                "sourceUrl": source_url,
            }
        )
    return reports


def _report_datetime_utc(event_date: date, time_value: Any) -> datetime | None:
    raw = "".join(ch for ch in str(time_value or "").strip() if ch.isdigit())
    if len(raw) < 3:
        return None
    raw = raw[-4:].zfill(4)
    hour = int(raw[:2])
    minute = int(raw[2:])
    if hour > 23 or minute > 59:
        return None
    report_date = event_date if hour >= 12 else event_date + timedelta(days=1)
    return datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        hour,
        minute,
        tzinfo=timezone.utc,
    )


def _row_value(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(_normalize_header(key))
        if value is not None:
            return str(value).strip()
    return ""


def _normalize_header(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
