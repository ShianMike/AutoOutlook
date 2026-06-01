"""Selected-field HRRR GRIB2 access for deployable outlook artifacts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import requests

from .grib2 import decode_grib2

HRRR_BASE_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
EXTENDED_CYCLE_HOURS = (0, 6, 12, 18)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_SELECTED_CACHE_DIR = Path(__file__).resolve().parent / "cache" / "hrrr_selected"
DEFAULT_CACHE_TTL_HOURS = 12.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return int(default)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)


def _env_nonnegative_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return int(default)
    try:
        return max(0, int(raw))
    except ValueError:
        return int(default)


DEFAULT_RANGE_WORKERS = _env_int("AUTOOUTLOOK_RANGE_WORKERS", 6)
DEFAULT_RANGE_COALESCE_GAP_BYTES = _env_nonnegative_int("AUTOOUTLOOK_RANGE_COALESCE_GAP_BYTES", 2 * 1024 * 1024)

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

REQUIRED_HRRR_TERMS = (
    ":CAPE:surface:",
    ":CIN:surface:",
    ":DPT:2 m above ground:",
    ":TMP:2 m above ground:",
    ":UGRD:10 m above ground:",
    ":VGRD:10 m above ground:",
    ":UGRD:500 mb:",
    ":VGRD:500 mb:",
    ":HGT:500 mb:",
)

OPTIONAL_HRRR_TERMS = tuple(term for term in SELECTED_HRRR_TERMS if term not in REQUIRED_HRRR_TERMS)

REQUIRED_FIELD_KEYS = ("cape", "cin", "td2m", "t2m", "u10", "v10", "u500", "v500", "hgt500")


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


@dataclass(frozen=True)
class SelectedRange:
    start: int
    end: int
    term: str
    descriptor: str


@dataclass(frozen=True)
class SelectedHrrrHour:
    lats: np.ndarray
    lons: np.ndarray
    fields: dict[str, np.ndarray]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class HrrrCycleDetection:
    selected: HrrrCycle
    metadata: dict[str, Any]


class SelectedHrrrError(RuntimeError):
    """Base error for selected HRRR fetch failures."""


class SelectedHrrrFieldError(SelectedHrrrError):
    """Raised when an HRRR .idx file does not contain required fields."""


class SelectedHrrrValidationError(SelectedHrrrError):
    """Raised when decoded HRRR arrays are not safe to use."""


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


def selected_term_report(
    records: list[tuple[int, int, str]],
    selected_terms: Iterable[str] = SELECTED_HRRR_TERMS,
    required_terms: Iterable[str] = REQUIRED_HRRR_TERMS,
    optional_terms: Iterable[str] = OPTIONAL_HRRR_TERMS,
) -> dict[str, Any]:
    """Return matched/missing selected-field terms for one HRRR .idx file."""
    selected_terms_tuple = tuple(selected_terms)
    matched = {
        term
        for _, _, descriptor in records
        for term in selected_terms_tuple
        if term in descriptor
    }
    required_tuple = tuple(required_terms)
    optional_tuple = tuple(optional_terms)
    return {
        "recordCount": len(records),
        "matchedTerms": [term for term in selected_terms_tuple if term in matched],
        "missingRequiredTerms": [term for term in required_tuple if term not in matched],
        "missingOptionalTerms": [term for term in optional_tuple if term not in matched],
    }


def validate_idx_records(
    records: list[tuple[int, int, str]],
    selected_terms: Iterable[str] = SELECTED_HRRR_TERMS,
    required_terms: Iterable[str] = REQUIRED_HRRR_TERMS,
    optional_terms: Iterable[str] = OPTIONAL_HRRR_TERMS,
) -> dict[str, Any]:
    """Validate that an HRRR .idx file contains all core fields before GRIB fetch."""
    report = selected_term_report(records, selected_terms, required_terms, optional_terms)
    if report["missingRequiredTerms"]:
        raise SelectedHrrrFieldError(
            "HRRR .idx missing required selected fields: "
            + ", ".join(report["missingRequiredTerms"])
        )
    return report


def selected_record_ranges(
    records: list[tuple[int, int, str]],
    content_length: int | None,
    terms: Iterable[str] = SELECTED_HRRR_TERMS,
) -> list[SelectedRange]:
    terms_tuple = tuple(terms)
    ranges: list[SelectedRange] = []
    for idx, (_, offset, descriptor) in enumerate(records):
        matched_term = next((term for term in terms_tuple if term in descriptor), None)
        if matched_term is None:
            continue
        if idx + 1 < len(records):
            end = records[idx + 1][1] - 1
        elif content_length is not None:
            end = content_length - 1
        else:
            # Without object length, fetching the final record would require a full-file
            # fallback. Skip it and let decoded-field validation decide if the hour is usable.
            continue
        if end > offset:
            ranges.append(SelectedRange(offset, end, matched_term, descriptor))
    return ranges


def selected_ranges(
    records: list[tuple[int, int, str]],
    content_length: int | None,
    terms: Iterable[str] = SELECTED_HRRR_TERMS,
) -> list[tuple[int, int]]:
    return [(item.start, item.end) for item in selected_record_ranges(records, content_length, terms)]


def coalesced_fetch_ranges(
    ranges: Iterable[SelectedRange],
    max_gap_bytes: int = DEFAULT_RANGE_COALESCE_GAP_BYTES,
) -> list[tuple[int, int]]:
    """Merge nearby selected records into fewer S3 byte-range requests."""
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    if not ordered:
        return []

    max_gap_bytes = max(0, int(max_gap_bytes))
    merged: list[tuple[int, int]] = []
    current_start = ordered[0].start
    current_end = ordered[0].end

    for item in ordered[1:]:
        gap = item.start - current_end - 1
        if gap <= max_gap_bytes:
            current_end = max(current_end, item.end)
            continue
        merged.append((current_start, current_end))
        current_start = item.start
        current_end = item.end

    merged.append((current_start, current_end))
    return merged


def latest_available_hrrr_cycle(
    session: requests.Session | None = None,
    now: datetime | None = None,
    max_lookback_hours: int = 96,
    require_forecast_hour: int = 48,
) -> HrrrCycle:
    """Return the newest extended HRRR cycle with f00 and required f-hour indexes available."""
    return latest_available_hrrr_cycle_with_metadata(
        session=session,
        now=now,
        max_lookback_hours=max_lookback_hours,
        require_forecast_hour=require_forecast_hour,
    ).selected


def latest_available_hrrr_cycle_with_metadata(
    session: requests.Session | None = None,
    now: datetime | None = None,
    max_lookback_hours: int = 96,
    require_forecast_hour: int = 48,
) -> HrrrCycleDetection:
    """Return newest complete extended HRRR cycle plus checked-cycle metadata."""
    require_forecast_hour = int(require_forecast_hour)
    if require_forecast_hour < 0 or require_forecast_hour > 48:
        raise ValueError(f"Required HRRR forecast hour must be in 0..48: {require_forecast_hour}")
    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook/1.0")
    checked: list[dict[str, Any]] = []
    required_hours = tuple(sorted({0, require_forecast_hour}))
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
                candidate = _cycle_completeness(session, cycle, required_hours)
                checked.append(candidate)
                if candidate["complete"]:
                    selected_was_fallback = len(checked) > 1
                    metadata = {
                        "selected": _cycle_metadata(cycle),
                        "latestExtendedCandidate": checked[0] if checked else _cycle_metadata(cycle),
                        "checkedCycles": checked,
                        "preferredCyclesUTC": list(EXTENDED_CYCLE_HOURS),
                        "requiredForecastHours": list(required_hours),
                        "requiredForecastHourForCycle": require_forecast_hour,
                        "requiredForecastHoursChecked": list(required_hours),
                        "selectedCycleWasFallback": selected_was_fallback,
                        "cyclePolicy": _cycle_policy_metadata(require_forecast_hour),
                        "fallbackReason": _fallback_reason(checked, require_forecast_hour) if selected_was_fallback else None,
                        "maxLookbackHours": max_lookback_hours,
                    }
                    return HrrrCycleDetection(selected=cycle, metadata=metadata)
            cursor -= timedelta(hours=1)
        raise FileNotFoundError(f"No complete HRRR extended cycle with f00 and f{require_forecast_hour:02d} indexes found")
    finally:
        if own_session:
            session.close()


def fetch_selected_hrrr_hour(
    ref: HrrrHourRef,
    session: requests.Session | None = None,
    max_workers: int = DEFAULT_RANGE_WORKERS,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Download only SELECTED_HRRR_TERMS records for one HRRR forecast hour."""
    result = fetch_selected_hrrr_hour_with_metadata(ref, session=session, max_workers=max_workers)
    return result.lats, result.lons, result.fields


def fetch_selected_hrrr_hour_with_metadata(
    ref: HrrrHourRef,
    session: requests.Session | None = None,
    max_workers: int = DEFAULT_RANGE_WORKERS,
    cache_dir: Path | str | None = DEFAULT_SELECTED_CACHE_DIR,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    no_cache: bool = False,
    grid_stride: int = 1,
    range_coalesce_gap_bytes: int | None = None,
    selected_terms: Iterable[str] = SELECTED_HRRR_TERMS,
    required_terms: Iterable[str] = REQUIRED_HRRR_TERMS,
    optional_terms: Iterable[str] = OPTIONAL_HRRR_TERMS,
    required_field_keys: Iterable[str] = REQUIRED_FIELD_KEYS,
) -> SelectedHrrrHour:
    """Download, decode, validate, downsample, and optionally cache one HRRR hour."""
    started = time.perf_counter()
    selected_terms_tuple = tuple(selected_terms)
    required_terms_tuple = tuple(required_terms)
    optional_terms_tuple = tuple(optional_terms)
    required_field_keys_tuple = tuple(required_field_keys)
    cache_path = cache_path_for(ref, cache_dir) if cache_dir is not None else None
    base_metadata = {
        "forecastHour": ref.forecast_hour,
        "runDate": ref.run_date,
        "runCycle": ref.run_cycle,
        "validTimeISO": ref.valid_time.isoformat().replace("+00:00", "Z"),
        "idxUrl": ref.idx_url,
        "gribUrl": ref.grib_url,
        "gridStride": max(1, int(grid_stride)),
        "cacheHit": False,
        "cachePath": str(cache_path) if cache_path else None,
        "source": "hrrr_s3_byte_ranges",
    }

    if not no_cache and cache_path is not None:
        cached = _load_cache(cache_path, cache_ttl_hours)
        if cached is not None:
            lats, lons, fields, cache_metadata = cached
            try:
                validate_decoded_hrrr_fields(lats, lons, fields, required_field_keys_tuple)
            except SelectedHrrrValidationError:
                cached = None
            else:
                metadata = {
                    **base_metadata,
                    **cache_metadata,
                    "cacheHit": True,
                    "cachePath": str(cache_path),
                    "decodedFieldNames": sorted(fields),
                    "gridShape": list(_grid_shape(lats, lons, fields)),
                    "fetchLatencyMs": int((time.perf_counter() - started) * 1000),
                }
                return SelectedHrrrHour(lats=lats, lons=lons, fields=fields, metadata=metadata)

    own_session = session is None
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "AutoOutlook-selected-hrrr/1.0")
    try:
        idx_response = _request_with_backoff(session, "GET", ref.idx_url, timeout=30)
        if idx_response.status_code == 404:
            raise FileNotFoundError(ref.idx_url)
        idx_response.raise_for_status()

        records = parse_idx(idx_response.text)
        term_report = validate_idx_records(
            records,
            selected_terms_tuple,
            required_terms_tuple,
            optional_terms_tuple,
        )
        content_length = _content_length(session, ref.grib_url)
        range_items = selected_record_ranges(records, content_length, selected_terms_tuple)
        ranges = [(item.start, item.end) for item in range_items]
        if not ranges:
            raise ValueError(f"No selected HRRR records found in {ref.idx_url}")
        coalesce_gap = (
            DEFAULT_RANGE_COALESCE_GAP_BYTES
            if range_coalesce_gap_bytes is None
            else max(0, int(range_coalesce_gap_bytes))
        )
        fetch_ranges = coalesced_fetch_ranges(range_items, max_gap_bytes=coalesce_gap)

        chunks_by_start: dict[int, bytes] = {}
        worker_count = max(1, min(max_workers, len(fetch_ranges)))
        print(
            f"[hrrr selected] F{ref.forecast_hour:02d} "
            f"records={len(records)} selectedRanges={len(ranges)} "
            f"fetchRanges={len(fetch_ranges)} workers={worker_count} "
            f"selectedBytes={int(sum(end - start + 1 for start, end in ranges))}",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(_fetch_range, session, ref.grib_url, start, end): (start, end) for start, end in fetch_ranges}
            for future in as_completed(futures):
                start, chunk = future.result()
                chunks_by_start[start] = chunk

        chunks = [chunks_by_start[start] for start, _ in sorted(fetch_ranges)]
        print(
            f"[hrrr selected] F{ref.forecast_hour:02d} "
            f"fetchedRanges={len(chunks_by_start)}/{len(fetch_ranges)} "
            f"fetchedBytes={sum(len(chunk) for chunk in chunks)}",
            flush=True,
        )
        messages = decode_grib2(b"".join(chunks))
        from .hrrr_filter import _messages_to_fields

        lats, lons, fields = _messages_to_fields(messages, require_cape="cape" in required_field_keys_tuple)
        lats, lons, fields = downsample_hrrr_grid(lats, lons, fields, grid_stride)
        validate_decoded_hrrr_fields(lats, lons, fields, required_field_keys_tuple)

        metadata = {
            **base_metadata,
            **term_report,
            "contentLength": content_length,
            "selectedRangeCount": len(ranges),
            "fetchRangeCount": len(fetch_ranges),
            "rangeCoalesceGapBytes": coalesce_gap,
            "selectedByteCount": int(sum(end - start + 1 for start, end in ranges)),
            "fetchedByteCount": int(sum(end - start + 1 for start, end in fetch_ranges)),
            "decodedFieldNames": sorted(fields),
            "gridShape": list(_grid_shape(lats, lons, fields)),
            "fetchLatencyMs": int((time.perf_counter() - started) * 1000),
        }
        if cache_path is not None and not no_cache:
            _save_cache(cache_path, lats, lons, fields, metadata)
        return SelectedHrrrHour(lats=lats, lons=lons, fields=fields, metadata=metadata)
    finally:
        if own_session:
            session.close()


def _idx_available(session: requests.Session, ref: HrrrHourRef) -> bool:
    try:
        response = _request_with_backoff(session, "GET", ref.idx_url, timeout=12, retries=1)
        if not response.ok or "GRIB" in response.text[:32] or len(response.text.splitlines()) <= 5:
            return False
        records = parse_idx(response.text)
        return not selected_term_report(records)["missingRequiredTerms"]
    except Exception:
        return False


def _content_length(session: requests.Session, url: str) -> int | None:
    try:
        response = _request_with_backoff(session, "HEAD", url, timeout=20, retries=2)
        if response.ok and response.headers.get("content-length"):
            return int(response.headers["content-length"])
    except Exception:
        return None
    return None


def _fetch_range(session: requests.Session, url: str, start: int, end: int) -> tuple[int, bytes]:
    if isinstance(session, requests.Session):
        request_session = requests.Session()
        request_session.headers.update(session.headers)
        close_session = True
    else:
        request_session = session
        close_session = False
    try:
        response = _request_with_backoff(
            request_session,
            "GET",
            url,
            headers={"Range": f"bytes={start}-{end}"},
            timeout=90,
            retries=3,
        )
    finally:
        if close_session:
            request_session.close()
    response.raise_for_status()
    expected_length = end - start + 1
    if response.status_code == 200 and len(response.content) > expected_length + 1024:
        raise ValueError(f"Range request {start}-{end} returned a full GRIB-like payload")
    if len(response.content) > expected_length + 1024:
        raise ValueError(f"Selected HRRR range {start}-{end} exceeded expected byte length")
    if not response.content.startswith(b"GRIB"):
        raise ValueError(f"Selected HRRR range {start}-{end} did not start with GRIB")
    return start, response.content


def _request_with_backoff(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff_seconds: float = 0.75,
    timeout: float = 30.0,
    **kwargs: Any,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < retries:
                time.sleep(backoff_seconds * (2 ** attempt))
                continue
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= retries:
                raise
            time.sleep(backoff_seconds * (2 ** attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{method} {url} failed without a response")


def cache_path_for(ref: HrrrHourRef, cache_dir: Path | str | None = DEFAULT_SELECTED_CACHE_DIR) -> Path:
    root = Path(cache_dir or DEFAULT_SELECTED_CACHE_DIR)
    return root / ref.run_date / f"{ref.run_cycle:02d}" / f"f{ref.forecast_hour:02d}.npz"


def downsample_hrrr_grid(
    lats: np.ndarray,
    lons: np.ndarray,
    fields: Mapping[str, np.ndarray],
    stride: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    stride = max(1, int(stride))
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    field_arrays = {key: np.asarray(value, dtype=float) for key, value in fields.items()}
    if stride <= 1:
        return lat_arr, lon_arr, field_arrays
    rows = slice(None, None, stride)
    cols = slice(None, None, stride)
    if lat_arr.ndim == 1:
        lat_out = lat_arr[rows]
    else:
        lat_out = lat_arr[rows, cols]
    if lon_arr.ndim == 1:
        lon_out = lon_arr[cols]
    else:
        lon_out = lon_arr[rows, cols]
    fields_out = {
        key: value[rows, cols] if value.ndim == 2 else value
        for key, value in field_arrays.items()
    }
    return lat_out, lon_out, fields_out


def validate_decoded_hrrr_fields(
    lats: np.ndarray,
    lons: np.ndarray,
    fields: Mapping[str, np.ndarray],
    required_field_keys: Iterable[str] = REQUIRED_FIELD_KEYS,
) -> None:
    if not fields:
        raise SelectedHrrrValidationError("Decoded HRRR hour has no fields")
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.size == 0 or lon_arr.size == 0:
        raise SelectedHrrrValidationError("Decoded HRRR hour has empty lat/lon arrays")
    if not np.isfinite(lat_arr).any() or not np.isfinite(lon_arr).any():
        raise SelectedHrrrValidationError("Decoded HRRR hour has non-finite lat/lon arrays")

    expected_shape = _expected_grid_shape(lat_arr, lon_arr)
    missing = [key for key in required_field_keys if key not in fields]
    if missing:
        raise SelectedHrrrValidationError("Decoded HRRR hour missing required fields: " + ", ".join(missing))

    for key, raw_value in fields.items():
        arr = np.asarray(raw_value, dtype=float)
        if arr.ndim != 2:
            raise SelectedHrrrValidationError(f"Decoded HRRR field {key} is not 2D")
        if arr.shape != expected_shape:
            raise SelectedHrrrValidationError(f"Decoded HRRR field {key} shape {arr.shape} does not match {expected_shape}")
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            raise SelectedHrrrValidationError(f"Decoded HRRR field {key} contains no finite values")
        _validate_plausibility(key, finite)


def _validate_plausibility(key: str, finite: np.ndarray) -> None:
    limits = {
        "cape": (-10.0, 15000.0),
        "cape_ml": (-10.0, 15000.0),
        "cape_mu": (-10.0, 15000.0),
        "cin": (-2000.0, 50.0),
        "cin_ml": (-2000.0, 50.0),
        "cin_mu": (-2000.0, 50.0),
        "td2m": (180.0, 340.0),
        "t2m": (180.0, 350.0),
        "pwat": (0.0, 120.0),
        "u10": (-150.0, 150.0),
        "v10": (-150.0, 150.0),
        "u500": (-170.0, 170.0),
        "v500": (-170.0, 170.0),
        "hgt500": (3500.0, 7000.0),
        "hgt700": (2000.0, 4500.0),
        "hgt850": (500.0, 3000.0),
        "hgt1000": (-500.0, 1200.0),
        "srh01": (0.0, 5000.0),
        "srh03": (0.0, 7000.0),
    }
    if key not in limits:
        return
    lo, hi = limits[key]
    p01 = float(np.nanpercentile(finite, 1.0))
    p99 = float(np.nanpercentile(finite, 99.0))
    if p01 < lo or p99 > hi:
        raise SelectedHrrrValidationError(
            f"Decoded HRRR field {key} outside plausible range {lo:g}..{hi:g}: p01={p01:g}, p99={p99:g}"
        )


def _expected_grid_shape(lats: np.ndarray, lons: np.ndarray) -> tuple[int, int]:
    if lats.ndim == 1 and lons.ndim == 1:
        return int(lats.size), int(lons.size)
    if lats.ndim == 2 and lons.ndim == 2 and lats.shape == lons.shape:
        return tuple(int(v) for v in lats.shape)
    raise SelectedHrrrValidationError(f"lat/lon arrays must be 1D axes or matching 2D grids, got {lats.shape} and {lons.shape}")


def _grid_shape(lats: np.ndarray, lons: np.ndarray, fields: Mapping[str, np.ndarray]) -> tuple[int, int]:
    first = np.asarray(next(iter(fields.values())), dtype=float)
    return tuple(int(v) for v in first.shape)


def _load_cache(
    path: Path,
    ttl_hours: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, Any]] | None:
    if ttl_hours <= 0 or not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600.0
    if age_hours > ttl_hours:
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            metadata = json.loads(str(data["__metadata"].item())) if "__metadata" in data else {}
            field_names = json.loads(str(data["__field_names"].item()))
            fields = {name: np.asarray(data[f"field__{name}"], dtype=float) for name in field_names if f"field__{name}" in data}
            return np.asarray(data["lats"], dtype=float), np.asarray(data["lons"], dtype=float), fields, metadata
    except Exception:
        return None


def _save_cache(
    path: Path,
    lats: np.ndarray,
    lons: np.ndarray,
    fields: Mapping[str, np.ndarray],
    metadata: Mapping[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload: dict[str, Any] = {
        "lats": np.asarray(lats, dtype=float),
        "lons": np.asarray(lons, dtype=float),
        "__metadata": np.asarray(json.dumps(dict(metadata), default=_json_default)),
        "__field_names": np.asarray(json.dumps(sorted(fields))),
    }
    for key, value in fields.items():
        payload[f"field__{key}"] = np.asarray(value, dtype=float)
    with tmp_path.open("wb") as fh:
        np.savez(fh, **payload)
    tmp_path.replace(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _cycle_metadata(cycle: HrrrCycle) -> dict[str, Any]:
    return {
        "runDate": cycle.run_date,
        "runCycle": cycle.run_cycle,
        "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
        "label": cycle.label,
    }


def _cycle_policy_metadata(require_forecast_hour: int) -> dict[str, Any]:
    required_hours = sorted({0, int(require_forecast_hour)})
    return {
        "name": "extended_hrrr_complete_required_hours",
        "model": "HRRR",
        "allowedRunCyclesUTC": list(EXTENDED_CYCLE_HOURS),
        "requiredForecastHoursChecked": required_hours,
        "description": (
            "Select the newest 00Z/06Z/12Z/18Z HRRR cycle with usable selected-field "
            f".idx files for {', '.join(f'f{hour:02d}' for hour in required_hours)}."
        ),
    }


def _fallback_reason(checked_cycles: list[dict[str, Any]], require_forecast_hour: int) -> str | None:
    if len(checked_cycles) <= 1:
        return None
    latest = checked_cycles[0]
    missing_hours = [
        int(report.get("forecastHour", require_forecast_hour))
        for report in latest.get("hours", [])
        if not (report.get("idxAvailable") and report.get("requiredFieldsPresent"))
    ]
    missing = ", ".join(f"f{hour:02d}" for hour in sorted(set(missing_hours)) if hour >= 0)
    if missing:
        return f"{latest.get('label', 'Latest extended HRRR cycle')} incomplete for {missing}"
    return f"{latest.get('label', 'Latest extended HRRR cycle')} failed completeness checks"


def _cycle_completeness(
    session: requests.Session,
    cycle: HrrrCycle,
    required_hours: Iterable[int],
) -> dict[str, Any]:
    hour_reports: list[dict[str, Any]] = []
    for forecast_hour in required_hours:
        ref = hour_ref(cycle, forecast_hour)
        report: dict[str, Any] = {
            "forecastHour": forecast_hour,
            "idxUrl": ref.idx_url,
            "idxAvailable": False,
            "requiredFieldsPresent": False,
        }
        try:
            response = _request_with_backoff(session, "GET", ref.idx_url, timeout=12, retries=1)
            report["statusCode"] = response.status_code
            if response.ok and "GRIB" not in response.text[:32] and len(response.text.splitlines()) > 5:
                records = parse_idx(response.text)
                term_report = selected_term_report(records)
                report.update(term_report)
                report["idxAvailable"] = True
                report["requiredFieldsPresent"] = not term_report["missingRequiredTerms"]
            elif response.status_code == 404:
                report["error"] = "idx_not_found"
            else:
                report["error"] = f"idx_unusable_status_{response.status_code}"
        except Exception as exc:  # noqa: BLE001
            report["error"] = f"{type(exc).__name__}: {exc}"
        hour_reports.append(report)

    complete = all(item.get("idxAvailable") and item.get("requiredFieldsPresent") for item in hour_reports)
    return {
        **_cycle_metadata(cycle),
        "complete": complete,
        "hours": hour_reports,
    }
