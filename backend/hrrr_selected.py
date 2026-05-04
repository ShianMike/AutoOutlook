"""Selected-field HRRR GRIB2 access for deployable outlook artifacts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import numpy as np
import requests

from .grib2 import decode_grib2
from .hrrr_filter import _messages_to_fields

HRRR_BASE_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
EXTENDED_CYCLE_HOURS = (0, 6, 12, 18)

SELECTED_HRRR_TERMS = (
    ":CAPE:surface:",
    ":CIN:surface:",
    ":CAPE:180-0 mb above ground:",
    ":CIN:180-0 mb above ground:",
    ":CAPE:255-0 mb above ground:",
    ":CIN:255-0 mb above ground:",
    ":PWAT:entire atmosphere",
    ":DPT:2 m above ground:",
    ":TMP:2 m above ground:",
    ":UGRD:10 m above ground:",
    ":VGRD:10 m above ground:",
    ":UGRD:500 mb:",
    ":VGRD:500 mb:",
    ":HGT:500 mb:",
    ":HLCY:1000-0 m above ground:",
    ":HLCY:3000-0 m above ground:",
)


@dataclass(frozen=True)
class HrrrCycle:
    run_date: str
    run_cycle: int

    @property
    def cycle_time(self) -> datetime:
        return datetime.strptime(f"{self.run_date}{self.run_cycle:02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)

    @property
    def label(self) -> str:
        return f"HRRR {self.run_cycle:02d}Z {self.run_date}"


@dataclass(frozen=True)
class HrrrHourRef:
    run_date: str
    run_cycle: int
    forecast_hour: int

    @property
    def cycle(self) -> HrrrCycle:
        return HrrrCycle(self.run_date, self.run_cycle)

    @property
    def valid_time(self) -> datetime:
        return self.cycle.cycle_time + timedelta(hours=self.forecast_hour)

    @property
    def grib_url(self) -> str:
        return (
            f"{HRRR_BASE_URL}/hrrr.{self.run_date}/conus/"
            f"hrrr.t{self.run_cycle:02d}z.wrfsfcf{self.forecast_hour:02d}.grib2"
        )

    @property
    def idx_url(self) -> str:
        return f"{self.grib_url}.idx"


def hour_ref(cycle: HrrrCycle, forecast_hour: int) -> HrrrHourRef:
    return HrrrHourRef(cycle.run_date, cycle.run_cycle, int(forecast_hour))


def parse_idx(idx_text: str) -> list[tuple[int, int, str]]:
    records: list[tuple[int, int, str]] = []
    for line in idx_text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        try:
            records.append((int(parts[0]), int(parts[1]), ":" + parts[2]))
        except ValueError:
            continue
    return records


def descriptor_matches_selected(descriptor: str, terms: Iterable[str] = SELECTED_HRRR_TERMS) -> bool:
    return any(term in descriptor for term in terms)


def selected_ranges(
    records: list[tuple[int, int, str]],
    content_length: int | None,
    terms: Iterable[str] = SELECTED_HRRR_TERMS,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for idx, (_, offset, descriptor) in enumerate(records):
        if not descriptor_matches_selected(descriptor, terms):
            continue
        if idx + 1 < len(records):
            end = records[idx + 1][1] - 1
        elif content_length is not None:
            end = content_length - 1
        else:
            continue
        if end > offset:
            ranges.append((offset, end))
    return ranges


def latest_available_hrrr_cycle(
    session: requests.Session | None = None,
    now: datetime | None = None,
    max_lookback_hours: int = 96,
    require_forecast_hour: int = 48,
) -> HrrrCycle:
    """Return the newest extended HRRR cycle with f00 and required f-hour indexes available."""
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook/1.0")
    try:
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cursor = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        # Avoid the just-initialized hour; its GRIB files usually lag.
        cursor -= timedelta(hours=1)
        for _ in range(max_lookback_hours + 1):
            if cursor.hour in EXTENDED_CYCLE_HOURS:
                cycle = HrrrCycle(cursor.strftime("%Y%m%d"), cursor.hour)
                if _idx_available(session, hour_ref(cycle, 0)) and _idx_available(session, hour_ref(cycle, require_forecast_hour)):
                    return cycle
            cursor -= timedelta(hours=1)
        raise FileNotFoundError(f"No HRRR cycle with f00 and f{require_forecast_hour:02d} indexes found")
    finally:
        if own_session:
            session.close()


def fetch_selected_hrrr_hour(
    ref: HrrrHourRef,
    session: requests.Session | None = None,
    max_workers: int = 4,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Download only SELECTED_HRRR_TERMS records for one HRRR forecast hour."""
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-selected-hrrr/1.0")
    try:
        idx_response = session.get(ref.idx_url, timeout=30)
        if idx_response.status_code == 404:
            raise FileNotFoundError(ref.idx_url)
        idx_response.raise_for_status()

        content_length = _content_length(session, ref.grib_url)
        ranges = selected_ranges(parse_idx(idx_response.text), content_length)
        if not ranges:
            raise ValueError(f"No selected HRRR records found in {ref.idx_url}")

        chunks_by_start: dict[int, bytes] = {}
        worker_count = max(1, min(max_workers, len(ranges)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(_fetch_range, session, ref.grib_url, start, end): (start, end) for start, end in ranges}
            for future in as_completed(futures):
                start, chunk = future.result()
                chunks_by_start[start] = chunk

        chunks = [chunks_by_start[start] for start, _ in sorted(ranges)]
        messages = decode_grib2(b"".join(chunks))
        return _messages_to_fields(messages)
    finally:
        if own_session:
            session.close()


def _idx_available(session: requests.Session, ref: HrrrHourRef) -> bool:
    try:
        response = session.get(ref.idx_url, timeout=12)
        return response.ok and "GRIB" not in response.text[:32] and len(response.text.splitlines()) > 5
    except Exception:
        return False


def _content_length(session: requests.Session, url: str) -> int | None:
    try:
        response = session.head(url, timeout=20)
        if response.ok and response.headers.get("content-length"):
            return int(response.headers["content-length"])
    except Exception:
        return None
    return None


def _fetch_range(session: requests.Session, url: str, start: int, end: int) -> tuple[int, bytes]:
    response = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=90)
    response.raise_for_status()
    if not response.content.startswith(b"GRIB"):
        raise ValueError(f"Selected HRRR range {start}-{end} did not start with GRIB")
    return start, response.content
