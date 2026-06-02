"""Gather SPC-report-labeled HRRR archive samples for XGBoost training."""

from __future__ import annotations

import argparse
import calendar
import io
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import numpy as np
import requests

from backend.grib2 import decode_grib2
from backend.hrrr_filter import _messages_to_fields
from backend.ml.features import FEATURE_NAMES, HAZARD_KEYS, feature_row
from backend.ml.reports import labels_for_sample

SPC_INDEX_URL = "https://origin-west-www-spc.woc.noaa.gov/wcm/index.html"
SPC_DATA_BASE_URL = "https://www.spc.noaa.gov/wcm/index.html"
HRRR_BASE_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "ml_data" / "archive_samples.parquet"
)

PILOT_YEARS = (2022, 2023, 2024)
PILOT_MONTHS = (3, 4, 5, 6)
PILOT_CYCLES = (0, 6, 12, 18)
PILOT_FORECAST_HOURS = tuple(range(49))

SELECTED_HRRR_TERMS = (
    ":CAPE:surface:",
    ":CIN:surface:",
    ":CAPE:90-0 mb above ground:",
    ":CIN:90-0 mb above ground:",
    ":CAPE:180-0 mb above ground:",
    ":CIN:180-0 mb above ground:",
    ":CAPE:255-0 mb above ground:",
    ":CIN:255-0 mb above ground:",
    ":CAPE:0-3000 m above ground:",
    ":PWAT:entire atmosphere",
    ":PRES:surface:",
    ":DPT:2 m above ground:",
    ":TMP:2 m above ground:",
    ":TMP:850 mb:",
    ":TMP:700 mb:",
    ":TMP:500 mb:",
    ":UGRD:10 m above ground:",
    ":VGRD:10 m above ground:",
    ":UGRD:500 mb:",
    ":VGRD:500 mb:",
    ":HGT:850 mb:",
    ":HGT:700 mb:",
    ":HGT:500 mb:",
    ":HLCY:1000-0 m above ground:",
    ":HLCY:3000-0 m above ground:",
)

NEGATIVE_POINTS = (
    # Tornado alley / Great Plains
    (35.22, -97.44),  # Oklahoma City OK
    (36.15, -95.99),  # Tulsa OK
    (37.69, -97.34),  # Wichita KS
    (39.05, -95.68),  # Topeka KS
    (38.97, -94.67),  # Kansas City MO
    (40.82, -96.69),  # Lincoln NE
    (41.26, -96.01),  # Omaha NE
    (43.55, -96.73),  # Sioux Falls SD
    (32.78, -96.80),  # Dallas TX
    (31.55, -97.15),  # Waco TX
    (29.76, -95.37),  # Houston TX
    (30.27, -97.74),  # Austin TX
    (35.47, -97.52),  # Yukon OK
    (34.61, -98.40),  # Lawton OK
    (36.40, -97.87),  # Enid OK
    # Midwest
    (41.88, -87.63),  # Chicago IL
    (43.07, -89.40),  # Madison WI
    (44.98, -93.27),  # Minneapolis MN
    (46.87, -96.79),  # Fargo ND
    (38.63, -90.20),  # St. Louis MO
    (39.96, -82.99),  # Columbus OH
    (41.50, -81.69),  # Cleveland OH
    (42.33, -83.05),  # Detroit MI
    (39.10, -84.51),  # Cincinnati OH
    (39.77, -86.16),  # Indianapolis IN
    (41.60, -93.62),  # Des Moines IA
    (43.65, -85.49),  # Big Rapids MI
    # Southeast / Mid-South
    (33.75, -84.39),  # Atlanta GA
    (36.16, -86.78),  # Nashville TN
    (35.15, -90.05),  # Memphis TN
    (30.33, -81.66),  # Jacksonville FL
    (32.37, -86.30),  # Montgomery AL
    (32.30, -90.18),  # Jackson MS
    (30.45, -91.19),  # Baton Rouge LA
    (29.95, -90.07),  # New Orleans LA
    (35.23, -82.56),  # Asheville NC
    (36.00, -78.90),  # Durham NC
    (34.00, -81.03),  # Columbia SC
    (33.45, -88.82),  # Meridian MS
    (31.23, -85.39),  # Dothan AL
    # Central / western fringes
    (35.53, -100.97),  # Amarillo TX
    (32.45, -99.74),  # Abilene TX
    (30.88, -102.90),  # Fort Stockton TX
    (38.25, -98.61),  # Hutchinson KS
    (45.47, -98.48),  # Aberdeen SD
    (33.39, -104.52),  # Roswell NM
    (35.08, -106.65),  # Albuquerque NM
    (40.25, -103.80),  # Greeley CO area
    (38.85, -104.82),  # Colorado Springs CO
    (37.87, -100.36),  # Dodge City KS
    (31.85, -102.37),  # Odessa TX
)


@dataclass(frozen=True)
class HrrrSampleRef:
    run_date: str
    run_cycle: int
    forecast_hour: int

    @property
    def valid_time(self) -> datetime:
        cycle = datetime.strptime(
            f"{self.run_date}{self.run_cycle:02d}", "%Y%m%d%H"
        ).replace(tzinfo=timezone.utc)
        return cycle + timedelta(hours=self.forecast_hour)

    @property
    def grib_url(self) -> str:
        return (
            f"{HRRR_BASE_URL}/hrrr.{self.run_date}/conus/"
            f"hrrr.t{self.run_cycle:02d}z.wrfsfcf{self.forecast_hour:02d}.grib2"
        )

    @property
    def idx_url(self) -> str:
        return f"{self.grib_url}.idx"


def _require_frame_deps() -> Any:
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Archive gathering requires pandas and pyarrow. Run `pip install -r backend/requirements.txt`."
        ) from exc
    return pd


def discover_spc_csv_urls(session: requests.Session) -> dict[str, list[str]]:
    response = session.get(SPC_INDEX_URL, timeout=30)
    response.raise_for_status()
    urls: dict[str, list[str]] = {hazard: [] for hazard in HAZARD_KEYS}
    for href in re.findall(
        r'href=["\']([^"\']+\.csv)["\']', response.text, flags=re.IGNORECASE
    ):
        lower = href.lower()
        hazard = None
        if "torn" in lower:
            hazard = "tornado"
        elif "hail" in lower:
            hazard = "hail"
        elif "wind" in lower:
            hazard = "wind"
        if hazard:
            # The WCM index is mirrored through origin-west, but its CSV data
            # paths are served from www.spc.noaa.gov.
            urls[hazard].append(urljoin(SPC_DATA_BASE_URL, href))
    return urls


def selected_spc_csv_urls(
    discovered: dict[str, list[str]], years: Iterable[int]
) -> dict[str, list[str]]:
    year_set = set(int(y) for y in years)
    selected: dict[str, list[str]] = {}
    for hazard, urls in discovered.items():
        annual_urls: list[str] = []
        for year in sorted(year_set):
            annual_urls.extend(
                [url for url in urls if url.rsplit("/", 1)[-1].startswith(f"{year}_")]
            )
        selected[hazard] = annual_urls or urls
    return selected


def _first_col(frame: Any, candidates: Iterable[str]) -> str | None:
    lower_to_name = {str(col).lower(): col for col in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lower_to_name:
            return lower_to_name[candidate.lower()]
    return None


def _parse_spc_datetime(
    row: Any, year_col: str, month_col: str, day_col: str, time_col: str
) -> datetime | None:
    try:
        year = int(row[year_col])
        month = int(row[month_col])
        day = int(row[day_col])
        raw_time = str(row[time_col]).strip()
        if ":" in raw_time:
            parts = raw_time.split(":")
            hour = int(parts[0])
            minute = int(parts[1])
        else:
            hhmm = str(int(float(raw_time))).zfill(4)
            hour = int(hhmm[:2])
            minute = int(hhmm[2:])
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _normalize_spc_frame(
    frame: Any, hazard: str, years: set[int], months: set[int]
) -> list[dict[str, Any]]:
    year_col = _first_col(frame, ("yr", "year"))
    month_col = _first_col(frame, ("mo", "month"))
    day_col = _first_col(frame, ("dy", "day"))
    time_col = _first_col(frame, ("time", "utc_time"))
    lat_col = _first_col(frame, ("slat", "lat", "latitude"))
    lon_col = _first_col(frame, ("slon", "lon", "longitude"))
    if not all((year_col, month_col, day_col, time_col, lat_col, lon_col)):
        return []

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        report_time = _parse_spc_datetime(row, year_col, month_col, day_col, time_col)
        if (
            report_time is None
            or report_time.year not in years
            or report_time.month not in months
        ):
            continue
        try:
            lat = float(row[lat_col])
            lon = float(row[lon_col])
        except (TypeError, ValueError):
            continue
        if not (20.0 <= lat <= 55.0 and -130.0 <= lon <= -60.0):
            continue
        rows.append({"hazard": hazard, "time": report_time, "lat": lat, "lon": lon})
    return rows


def load_spc_reports(
    session: requests.Session, years: Iterable[int], months: Iterable[int]
) -> list[dict[str, Any]]:
    pd = _require_frame_deps()
    year_set = set(int(y) for y in years)
    month_set = set(int(m) for m in months)
    reports: list[dict[str, Any]] = []
    selected_urls_by_hazard = selected_spc_csv_urls(
        discover_spc_csv_urls(session), year_set
    )
    for hazard, urls in selected_urls_by_hazard.items():
        for url in urls:
            try:
                response = session.get(url, timeout=45)
                response.raise_for_status()
                frame = pd.read_csv(io.StringIO(response.text))
                reports.extend(_normalize_spc_frame(frame, hazard, year_set, month_set))
            except Exception as exc:  # noqa: BLE001
                print(f"Skipping SPC CSV {url}: {exc}")
    return reports


def _month_days(year: int, month: int) -> Iterable[str]:
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        yield f"{year:04d}{month:02d}{day:02d}"


def _run_dates_for_window(
    years: Iterable[int], months: Iterable[int], run_dates: Iterable[str] | None
) -> Iterable[str]:
    if run_dates:
        wanted_years = set(int(y) for y in years)
        wanted_months = set(int(m) for m in months)
        for raw_date in sorted(set(str(date) for date in run_dates)):
            parsed = datetime.strptime(raw_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            if parsed.year in wanted_years and parsed.month in wanted_months:
                yield raw_date
        return
    for year in years:
        for month in months:
            yield from _month_days(int(year), int(month))


def _valid_hour_set(valid_hours: Iterable[str] | None) -> set[datetime] | None:
    if not valid_hours:
        return None
    return {
        datetime.strptime(str(valid_hour), "%Y%m%d%H").replace(tzinfo=timezone.utc)
        for valid_hour in valid_hours
    }


def iter_hrrr_refs(
    years: Iterable[int],
    months: Iterable[int],
    cycles: Iterable[int],
    forecast_hours: Iterable[int],
    run_dates: Iterable[str] | None = None,
    valid_hours: Iterable[str] | None = None,
) -> Iterable[HrrrSampleRef]:
    valid_time_filter = _valid_hour_set(valid_hours)
    for run_date in _run_dates_for_window(years, months, run_dates):
        for cycle in cycles:
            for forecast_hour in forecast_hours:
                ref = HrrrSampleRef(run_date, int(cycle), int(forecast_hour))
                if (
                    valid_time_filter is not None
                    and ref.valid_time not in valid_time_filter
                ):
                    continue
                yield ref


def _parse_idx(idx_text: str) -> list[tuple[int, int, str]]:
    records = []
    for line in idx_text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        try:
            records.append((int(parts[0]), int(parts[1]), ":" + parts[2]))
        except ValueError:
            continue
    return records


def _selected_ranges(
    records: list[tuple[int, int, str]], content_length: int | None
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for idx, (_, offset, descriptor) in enumerate(records):
        if not any(term in descriptor for term in SELECTED_HRRR_TERMS):
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


def _fetch_hrrr_subset(
    session: requests.Session, ref: HrrrSampleRef
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    idx_response = session.get(ref.idx_url, timeout=30)
    if idx_response.status_code == 404:
        raise FileNotFoundError(ref.idx_url)
    idx_response.raise_for_status()
    head = session.head(ref.grib_url, timeout=20)
    content_length = (
        int(head.headers["content-length"])
        if head.ok and head.headers.get("content-length")
        else None
    )
    ranges = _selected_ranges(_parse_idx(idx_response.text), content_length)
    if not ranges:
        raise ValueError(f"No selected HRRR records found in {ref.idx_url}")

    def _fetch_range(start_end: tuple[int, int]) -> tuple[int, bytes]:
        start, end = start_end
        r = session.get(
            ref.grib_url, headers={"Range": f"bytes={start}-{end}"}, timeout=60
        )
        r.raise_for_status()
        return start, r.content

    # Fetch all (merged) byte ranges in parallel
    chunks_map: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=min(len(ranges), 2)) as ex:
        futures = {ex.submit(_fetch_range, r): r for r in ranges}
        for fut in as_completed(futures):
            start, data = fut.result()
            chunks_map[start] = data
    chunks = [chunks_map[start] for start, _ in sorted(chunks_map.items())]
    messages = decode_grib2(b"".join(chunks))
    return _messages_to_fields(messages)


def _nearest_grid_index(
    lats: np.ndarray, lons: np.ndarray, lat: float, lon: float
) -> tuple[int, int]:
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        i_lat = int(np.nanargmin(np.abs(lat_arr - lat)))
        i_lon = int(np.nanargmin(np.abs(lon_arr - lon)))
        return i_lat, i_lon
    distance = (lat_arr - lat) ** 2 + ((lon_arr - lon) * np.cos(np.radians(lat))) ** 2
    return tuple(
        int(x) for x in np.unravel_index(int(np.nanargmin(distance)), distance.shape)
    )


def _value(
    fields: dict[str, np.ndarray],
    key: str,
    i_lat: int,
    i_lon: int,
    default: float = 0.0,
) -> float:
    arr = np.asarray(fields.get(key), dtype=float) if key in fields else None
    if arr is None or arr.ndim != 2:
        return default
    value = float(arr[i_lat, i_lon])
    return value if np.isfinite(value) else default


def _ingredients_at(
    lats: np.ndarray,
    lons: np.ndarray,
    fields: dict[str, np.ndarray],
    lat: float,
    lon: float,
) -> dict[str, Any]:
    from backend import metpy_diagnostics as diag
    from backend.bundle_builder import _ingredients_at_point

    i_lat, i_lon = _nearest_grid_index(lats, lons, lat, lon)
    cape = _value(fields, "cape", i_lat, i_lon)
    cape180 = _value(fields, "cape_180", i_lat, i_lon, cape * 0.85)
    cape3km = _value(fields, "cape_3km", i_lat, i_lon, 0.0)
    mlcape = _value(fields, "cape_ml", i_lat, i_lon, cape180)
    mucape = _value(fields, "cape_mu", i_lat, i_lon, max(cape, mlcape))
    surface_cin = min(0.0, _value(fields, "cin", i_lat, i_lon))
    cin180 = min(0.0, _value(fields, "cin_180", i_lat, i_lon, surface_cin))
    mlcin = min(0.0, _value(fields, "cin_ml", i_lat, i_lon, cin180))
    mucin = min(0.0, _value(fields, "cin_mu", i_lat, i_lon, mlcin))
    td2m = _value(fields, "td2m", i_lat, i_lon, 285.0)
    t2m = _value(fields, "t2m", i_lat, i_lon, td2m + 8.0)
    pwat = _value(fields, "pwat", i_lat, i_lon, 20.0)
    if all(k in fields for k in ("u500", "v500", "u10", "v10")):
        shear_kt = float(
            np.hypot(
                _value(fields, "u500", i_lat, i_lon)
                - _value(fields, "u10", i_lat, i_lon),
                _value(fields, "v500", i_lat, i_lon)
                - _value(fields, "v10", i_lat, i_lon),
            )
            * 1.9438445
        )
    else:
        shear_kt = 0.0
    srh01 = _value(fields, "srh01", i_lat, i_lon, max(0.0, (shear_kt - 15.0) * 6.0))
    srh03 = _value(fields, "srh03", i_lat, i_lon, srh01 * 1.4)
    lcl_m = float(np.clip(125.0 * max(0.0, t2m - td2m), 100.0, 3500.0))
    comps = diag.composites(
        cape=np.array([cape]),
        mlcape=np.array([mlcape]),
        mucape=np.array([mucape]),
        shear_kt=np.array([shear_kt]),
        srh01=np.array([srh01]),
        srh03=np.array([srh03]),
        cin=np.array([surface_cin]),
        cin_mu=np.array([mucin]),
        td2m_K=np.array([td2m]),
        t2m_K=np.array([t2m]),
        lcl_m=np.array([lcl_m]),
        surface_pressure_pa=np.array(
            [_value(fields, "surface_pressure", i_lat, i_lon, np.nan)]
        ),
        t850_K=np.array([_value(fields, "t850", i_lat, i_lon, np.nan)]),
        t700_K=np.array([_value(fields, "t700", i_lat, i_lon, np.nan)]),
        t500_K=np.array([_value(fields, "t500", i_lat, i_lon, np.nan)]),
        hgt850_m=np.array([_value(fields, "hgt850", i_lat, i_lon, np.nan)]),
        hgt700_m=np.array([_value(fields, "hgt700", i_lat, i_lon, np.nan)]),
        hgt500_m=np.array([_value(fields, "hgt500", i_lat, i_lon, np.nan)]),
    )
    comps_scalar = {k: float(v[0]) for k, v in comps.items()}
    return _ingredients_at_point(
        cape,
        mlcape,
        mucape,
        surface_cin,
        mlcin,
        mucin,
        cape3km,
        cape180,
        cin180,
        td2m,
        t2m,
        pwat,
        shear_kt,
        srh01,
        srh03,
        shear_kt * 0.5,
        comps_scalar,
    )


def _dedupe_points(
    points: Iterable[tuple[float, float]], max_points: int
) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for lat, lon in points:
        key = (round(lat * 10), round(lon * 10))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((lat, lon))
        if len(deduped) >= max_points:
            break
    return deduped


def _candidate_points(
    reports: list[dict[str, Any]],
    valid_time: datetime,
    max_points: int,
    rng: random.Random,
    negative_points_per_hour: int = 0,
) -> list[tuple[float, float]]:
    report_points = [
        (float(report["lat"]), float(report["lon"]))
        for report in reports
        if valid_time <= report["time"] < valid_time + timedelta(hours=1)
    ]
    rng.shuffle(report_points)
    negative_points = list(NEGATIVE_POINTS)
    rng.shuffle(negative_points)

    if negative_points_per_hour > 0:
        negative_count = min(max_points, max(0, int(negative_points_per_hour)))
        return _dedupe_points(
            [
                *negative_points[:negative_count],
                *report_points,
                *negative_points[negative_count:],
            ],
            max_points,
        )

    points = [*report_points, *negative_points]
    rng.shuffle(points)
    return _dedupe_points(points, max_points)


def _dedupe_feature_label_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    key_columns = [*FEATURE_NAMES, *(f"label_{hazard}" for hazard in HAZARD_KEYS)]
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    dropped = 0
    for row in rows:
        key = tuple(row.get(column) for column in key_columns)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, dropped


def gather(args: argparse.Namespace) -> int:
    pd = _require_frame_deps()
    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-archive-gatherer/1.0"
    rng = random.Random(args.random_seed)

    if args.dry_run:
        selected_urls_by_hazard = selected_spc_csv_urls(
            discover_spc_csv_urls(session), args.years
        )
        first_refs = list(
            iter_hrrr_refs(
                args.years,
                args.months,
                args.cycles,
                args.forecast_hours,
                args.run_dates,
                args.valid_hours,
            )
        )[:5]
        print(
            json.dumps(
                {
                    "selectedSpcCsvs": selected_urls_by_hazard,
                    "firstHrrrRefs": [
                        ref.__dict__ | {"validTimeISO": ref.valid_time.isoformat()}
                        for ref in first_refs
                    ],
                    "downloads": "dry-run only; no SPC CSV files or HRRR GRIB byte ranges downloaded",
                },
                indent=2,
                default=str,
            )
        )
        return 0

    reports = load_spc_reports(session, args.years, args.months)
    print(f"Loaded {len(reports)} SPC reports for pilot window", flush=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: list[dict[str, Any]] = []
    skipped_count = [0]
    fetched_count = [0]
    last_ref: list[Any] = [None]

    def _checkpoint(
        current_rows: list[dict[str, Any]], _args: argparse.Namespace
    ) -> None:
        """Write current rows to a .ckpt parquet alongside the output file."""
        ckpt_path = _args.output.with_suffix(".ckpt.parquet")
        _args.output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(current_rows).to_parquet(ckpt_path, index=False)
        r = last_ref[0]
        tag = f"{r.run_date}_z{r.run_cycle:02d}_+{r.forecast_hour:02d}h" if r else "?"
        print(
            f"  [checkpoint] {len(current_rows)} rows → {ckpt_path.name} (resume: --start-from {tag})",
            flush=True,
        )

    def _process_ref(ref: HrrrSampleRef) -> list[dict[str, Any]]:
        worker_session = requests.Session()
        worker_session.headers["User-Agent"] = "AutoOutlook-archive-gatherer/1.0"
        worker_rng = random.Random(
            args.random_seed ^ hash(ref.run_date) ^ ref.run_cycle ^ ref.forecast_hour
        )
        try:
            lats, lons, fields = _fetch_hrrr_subset(worker_session, ref)
        except Exception as exc:  # noqa: BLE001
            return [{"__skip__": True, "__exc__": str(exc)}]
        batch: list[dict[str, Any]] = []
        for lat, lon in _candidate_points(
            reports,
            ref.valid_time,
            args.points_per_hour,
            worker_rng,
            args.negative_points_per_hour,
        ):
            labels = labels_for_sample(
                reports, ref.valid_time, lat, lon, args.radius_km
            )
            ingredients = _ingredients_at(lats, lons, fields, lat, lon)
            batch.append(
                {
                    "runDate": ref.run_date,
                    "runCycle": ref.run_cycle,
                    "forecastHour": ref.forecast_hour,
                    "validTimeISO": ref.valid_time.isoformat().replace("+00:00", "Z"),
                    "sampleLat": lat,
                    "sampleLon": lon,
                    **feature_row(ingredients, ref.forecast_hour),
                    **{f"label_{hazard}": labels[hazard] for hazard in HAZARD_KEYS},
                }
            )
        return batch

    all_refs = list(
        iter_hrrr_refs(
            args.years,
            args.months,
            args.cycles,
            args.forecast_hours,
            args.run_dates,
            args.valid_hours,
        )
    )

    # --start-from: skip refs up to and including the given checkpoint
    if args.start_from:
        import re

        m = re.match(r"(\d{8})_z(\d{2})_\+(\d+)h", args.start_from)
        if not m:
            raise ValueError(
                f"--start-from must be YYYYMMDD_zHH_+FFh, got: {args.start_from}"
            )
        sf_date, sf_cycle, sf_fh = m.group(1), int(m.group(2)), int(m.group(3))
        skip_idx = next(
            (
                i
                for i, r in enumerate(all_refs)
                if r.run_date == sf_date
                and r.run_cycle == sf_cycle
                and r.forecast_hour == sf_fh
            ),
            None,
        )
        if skip_idx is not None:
            all_refs = all_refs[skip_idx + 1 :]
            print(
                f"Resuming after {args.start_from} — {len(all_refs)} refs remaining",
                flush=True,
            )

    # Submit all refs; executor keeps only max_workers=12 running at a time.
    # Cancel remaining futures once max_samples is reached.
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_ref = {executor.submit(_process_ref, ref): ref for ref in all_refs}
        for fut in as_completed(future_to_ref):
            ref = future_to_ref[fut]
            batch = fut.result()
            if batch and batch[0].get("__skip__"):
                skipped_count[0] += 1
                exc_msg = batch[0].get("__exc__", "")
                if skipped_count[0] <= 20 or skipped_count[0] % 50 == 0:
                    print(f"Skipping {ref}: {exc_msg}", flush=True)
            else:
                fetched_count[0] += 1
                rows.extend(batch)
                last_ref[0] = ref
                if fetched_count[0] % 25 == 0 or fetched_count[0] == 1:
                    print(
                        f"[{fetched_count[0]} fetched | {len(rows)} rows | {skipped_count[0]} skipped] last: {ref.run_date} z{ref.run_cycle:02d} +{ref.forecast_hour:02d}h",
                        flush=True,
                    )
                    # Checkpoint: flush rows to disk every 25 files so progress survives a kill
                    _checkpoint(rows, args)
            if args.max_samples is not None and len(rows) >= args.max_samples:
                for f in future_to_ref:
                    f.cancel()
                break

    duplicates_dropped = 0
    if args.dedupe_feature_label_rows:
        rows, duplicates_dropped = _dedupe_feature_label_rows(rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)
    if args.start_from:
        ckpt_path = args.output.with_suffix(".ckpt.parquet")
        base_path = ckpt_path if ckpt_path.exists() else args.output
        if base_path.exists():
            existing_df = pd.read_parquet(base_path)
            new_df = pd.concat([existing_df, new_df], ignore_index=True)
            print(
                f"Merged {len(existing_df)} existing rows + {len(rows)} new rows = {len(new_df)} total",
                flush=True,
            )
    new_df.to_parquet(args.output, index=False)
    # Clean up checkpoint file now that final parquet is written
    ckpt = args.output.with_suffix(".ckpt.parquet")
    if ckpt.exists():
        ckpt.unlink()
    final_rows = new_df.to_dict("records")
    print(
        json.dumps(
            {
                "rows": len(new_df),
                "skippedHrrrHours": skipped_count[0],
                "duplicatesDropped": duplicates_dropped,
                "uniqueRunDates": len(
                    {str(row.get("runDate", "")) for row in final_rows}
                ),
                "positives": {
                    hazard: int(
                        sum(int(row.get(f"label_{hazard}", 0)) for row in final_rows)
                    )
                    for hazard in HAZARD_KEYS
                },
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=list(PILOT_YEARS))
    parser.add_argument("--months", type=int, nargs="+", default=list(PILOT_MONTHS))
    parser.add_argument("--cycles", type=int, nargs="+", default=list(PILOT_CYCLES))
    parser.add_argument(
        "--forecast-hours", type=int, nargs="+", default=list(PILOT_FORECAST_HOURS)
    )
    parser.add_argument(
        "--run-dates",
        nargs="+",
        help="Optional HRRR run dates to gather, formatted YYYYMMDD",
    )
    parser.add_argument(
        "--valid-hours",
        nargs="+",
        help="Optional valid hours to keep, formatted YYYYMMDDHH",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--radius-km", type=float, default=40.0)
    parser.add_argument("--points-per-hour", type=int, default=8)
    parser.add_argument("--negative-points-per-hour", type=int, default=0)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument(
        "--start-from",
        default=None,
        help="Skip all refs up to and including this one. Format: YYYYMMDD_zHH_+FFh (e.g. 20210304_z00_+24h)",
    )
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument(
        "--dedupe-feature-label-rows",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop duplicate rows by feature vector + hazard labels before writing Parquet.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    raise SystemExit(gather(parse_args()))


if __name__ == "__main__":
    main()
