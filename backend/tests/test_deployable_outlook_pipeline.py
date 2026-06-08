from __future__ import annotations

import json
import argparse
import importlib.util
import os
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.bundle_builder import HGT500_CONTOUR_LEVELS, _hgt500_lines_from_field, _wind500_vectors_from_fields, fetch_full_conus_500mb_overlay
from backend.grib2 import _extract_n_bits_from_bytes
from backend.hrrr_filter import _messages_to_fields
from backend.hrrr_selected import (
    REQUIRED_HRRR_TERMS,
    HrrrCycle,
    HrrrCycleDetection,
    SelectedHrrrValidationError,
    _fetch_range,
    _request_with_backoff,
    coalesced_fetch_ranges,
    descriptor_matches_selected,
    downsample_hrrr_grid,
    latest_available_hrrr_cycle_with_metadata,
    parse_idx,
    selected_record_ranges,
    selected_ranges,
    selected_term_report,
    validate_decoded_hrrr_fields,
)
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    apply_category_probability_ceiling,
    apply_environmental_probability_caps,
    apply_offshore_probability_suppression,
    category_grid_from_probabilities,
    constrain_hazard_probability_shapes_to_risk_support,
    gridded_features_from_fields,
    hazard_probability_shapes_from_grids,
    postprocess_category_grid,
    probability_tile,
    risk_polygons_from_grid,
)
from backend.ml.outlook_pipeline import (
    ALL_FORECAST_HOURS,
    PRODUCTION_FORECAST_HOURS,
    CloudRunTaskShard,
    FetchedHour,
    _cache_previous_incremental_cycle,
    _hydrate_incremental_artifacts_from_gcs,
    _merged_d1_00z_cache_dir,
    _publish_complete_incremental_snapshot,
    _publish_incremental_artifacts_to_gcs,
    _publish_incremental_shard_artifacts_to_gcs,
    _publish_working_dir,
    _incremental_hour_ready,
    _region_from_max_risk_grid,
    resolve_cloud_run_task_forecast_hours,
    resolve_cycle_policy,
    resolve_forecast_hours,
    resolve_cli_forecast_hours,
    resolve_required_forecast_hour,
    run_incremental_pipeline,
    run_pipeline,
)
from backend.ml.spc_verification import compare_prediction_to_spc, official_category_grid


def small_fields(shape: tuple[int, int] = (5, 5)) -> dict[str, np.ndarray]:
    base = np.full(shape, 1200.0)
    return {
        "cape": base,
        "cape_ml": base * 0.85,
        "cape_mu": base * 1.15,
        "cin": np.full(shape, -40.0),
        "cin_ml": np.full(shape, -55.0),
        "td2m": np.full(shape, 292.0),
        "t2m": np.full(shape, 300.0),
        "pwat": np.full(shape, 32.0),
        "u10": np.full(shape, 5.0),
        "v10": np.full(shape, 1.0),
        "u500": np.full(shape, 25.0),
        "v500": np.full(shape, 11.0),
        "hgt500": np.full(shape, 5700.0),
        "srh01": np.full(shape, 80.0),
        "srh03": np.full(shape, 160.0),
    }


def skipped_category_adjacencies(grid: np.ndarray) -> list[tuple[int, int]]:
    arr = np.asarray(grid, dtype=np.int16)
    pairs: list[tuple[int, int]] = []
    for row_offset, col_offset in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = arr[max(0, row_offset): arr.shape[0] + min(0, row_offset), max(0, col_offset): arr.shape[1] + min(0, col_offset)]
        b = arr[max(0, -row_offset): arr.shape[0] - max(0, row_offset), max(0, -col_offset): arr.shape[1] - max(0, col_offset)]
        skipped = (a > 0) & (b > 0) & (np.abs(a - b) > 1)
        if np.any(skipped):
            pairs.extend((int(x), int(y)) for x, y in zip(a[skipped], b[skipped], strict=False))
    return pairs


def fake_spc_geojson() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-100.0, 30.0],
                    [-96.0, 30.0],
                    [-96.0, 34.0],
                    [-100.0, 34.0],
                    [-100.0, 30.0],
                ]],
            },
            "properties": {
                "LABEL": "MRGL",
                "VALID_ISO": "2024-05-04T12:00:00+00:00",
                "EXPIRE_ISO": "2024-05-05T12:00:00+00:00",
                "ISSUE_ISO": "2024-05-04T05:58:00+00:00",
                "FORECASTER": "Unit",
            },
        }],
    }


def polygon_area(ring: list[list[float]]) -> float:
    coords = ring[:-1] if ring and ring[0] == ring[-1] else ring
    area = 0.0
    for idx, (x0, y0) in enumerate(coords):
        x1, y1 = coords[(idx + 1) % len(coords)]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def projected_geojson_geometry(geometry: dict):
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    return transform(transformer, shape(geometry))


def area_coverage_fraction(candidate, target) -> float:
    if target.is_empty or float(target.area) <= 0.0:
        return 0.0
    return float(candidate.intersection(target).area) / float(target.area)


def required_idx_text() -> str:
    lines = []
    for idx, term in enumerate(REQUIRED_HRRR_TERMS, start=1):
        lines.append(f"{idx}:{(idx - 1) * 100}:d=2024050412{term}anl:")
    lines.append(f"{len(lines) + 1}:{len(lines) * 100}:d=2024050412:REFC:entire atmosphere:anl:")
    return "\n".join(lines)


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", content: bytes = b"", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHrrrIdxSession:
    def __init__(self, complete_cycle: int) -> None:
        self.complete_cycle = complete_cycle
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, **_kwargs):
        if method != "GET":
            return FakeResponse(405)
        if f"t{self.complete_cycle:02d}z.wrfsfcf00" in url or f"t{self.complete_cycle:02d}z.wrfsfcf48" in url:
            return FakeResponse(200, required_idx_text())
        return FakeResponse(404)


class MatrixHrrrIdxSession:
    def __init__(self, available_hours_by_cycle: dict[int, set[int]]) -> None:
        self.available_hours_by_cycle = available_hours_by_cycle
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, **_kwargs):
        if method != "GET":
            return FakeResponse(405)
        for cycle, hours in self.available_hours_by_cycle.items():
            if f"t{cycle:02d}z" not in url:
                continue
            for hour in hours:
                if f"wrfsfcf{hour:02d}" in url:
                    return FakeResponse(200, required_idx_text())
        return FakeResponse(404)


class SequenceSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = 0
        self.requests: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls += 1
        self.requests.append({"method": method, "url": url, "kwargs": kwargs})
        return self.responses.pop(0)


class DeployableOutlookPipelineTests(unittest.TestCase):
    def test_grib_bit_reader_preserves_values_wider_than_one_byte(self) -> None:
        values, bit_offset = _extract_n_bits_from_bytes(
            np.asarray([0x12, 0x34, 0xAB, 0xCD], dtype=np.uint8),
            0,
            2,
            16,
        )

        self.assertEqual(bit_offset, 32)
        self.assertEqual(values.tolist(), [0x1234, 0xABCD])

    def test_hgt_pressure_levels_decode_without_cape_requirement(self) -> None:
        lats = np.array([[35.0, 35.0], [36.0, 36.0]])
        lons = np.array([[-98.0, -97.0], [-98.0, -97.0]])
        messages = [
            {
                "category": 3,
                "parameter": 5,
                "level_type": 100,
                "level_value": level_value,
                "lats": lats,
                "lons": lons,
                "values": np.full((2, 2), value, dtype=float),
            }
            for level_value, value in (
                (50000.0, 5700.0),
                (70000.0, 3100.0),
                (85000.0, 1500.0),
                (1000.0, 100.0),
            )
        ]

        _, _, fields = _messages_to_fields(messages, require_cape=False)

        self.assertEqual(sorted(fields), ["hgt1000", "hgt500", "hgt700", "hgt850"])
        self.assertEqual(float(fields["hgt700"][0, 0]), 3100.0)

    def test_selected_hrrr_terms_filter_only_requested_records(self) -> None:
        idx_text = "\n".join([
            "1:0:d=2024050412:CAPE:surface:anl:",
            "2:100:d=2024050412:XYZ:entire atmosphere:anl:",
            "3:200:d=2024050412:HLCY:3000-0 m above ground:anl:",
            "4:300:d=2024050412:TMP:850 mb:anl:",
            "5:400:d=2024050412:VGRD:500 mb:anl:",
        ])
        records = parse_idx(idx_text)

        self.assertTrue(descriptor_matches_selected(records[0][2]))
        self.assertFalse(descriptor_matches_selected(records[1][2]))
        self.assertEqual(selected_ranges(records, 500), [(0, 99), (200, 299), (300, 399), (400, 499)])

    def test_selected_hrrr_ranges_coalesce_adjacent_and_tiny_gaps(self) -> None:
        idx_text = "\n".join([
            "1:0:d=2024050412:CAPE:surface:anl:",
            "2:100:d=2024050412:CIN:surface:anl:",
            "3:200:d=2024050412:XYZ:entire atmosphere:anl:",
            "4:260:d=2024050412:DPT:2 m above ground:anl:",
            "5:360:d=2024050412:TMP:2 m above ground:anl:",
            "6:900:d=2024050412:UGRD:10 m above ground:anl:",
        ])
        records = parse_idx(idx_text)
        ranges = selected_record_ranges(records, 1000)

        self.assertEqual(selected_ranges(records, 1000), [(0, 99), (100, 199), (260, 359), (360, 899), (900, 999)])
        self.assertEqual(coalesced_fetch_ranges(ranges, max_gap_bytes=0), [(0, 199), (260, 999)])
        self.assertEqual(coalesced_fetch_ranges(ranges, max_gap_bytes=64), [(0, 999)])

    def test_selected_term_report_separates_required_and_optional_missing_terms(self) -> None:
        records = parse_idx(required_idx_text())

        report = selected_term_report(records)

        self.assertEqual(report["missingRequiredTerms"], [])
        self.assertIn(":PWAT:entire atmosphere", report["missingOptionalTerms"])
        self.assertIn(":CAPE:surface:", report["matchedTerms"])

    def test_byte_range_request_retries_transient_statuses(self) -> None:
        session = SequenceSession([FakeResponse(503), FakeResponse(200, content=b"ok")])

        response = _request_with_backoff(session, "GET", "https://example.test/file", retries=1, backoff_seconds=0)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.calls, 2)

    def test_byte_range_request_rejects_full_grib_fallback(self) -> None:
        session = SequenceSession([FakeResponse(200, content=b"GRIB" + (b"x" * 2048))])

        with self.assertRaises(ValueError):
            _fetch_range(session, "https://example.test/file", 0, 10)

    def test_byte_range_fetch_uses_bounded_timeout_and_retry_budget(self) -> None:
        session = SequenceSession([FakeResponse(503), FakeResponse(200, content=b"GRIBabc")])

        with patch("backend.hrrr_selected.DEFAULT_RANGE_REQUEST_TIMEOUT_SECONDS", 12.0), patch(
            "backend.hrrr_selected.DEFAULT_RANGE_REQUEST_RETRIES",
            1,
        ):
            start, content = _fetch_range(session, "https://example.test/file", 0, 6)

        self.assertEqual(start, 0)
        self.assertEqual(content, b"GRIBabc")
        self.assertEqual(session.calls, 2)
        self.assertEqual([call["kwargs"]["timeout"] for call in session.requests], [12.0, 12.0])

    def test_latest_cycle_detection_falls_back_to_complete_extended_cycle(self) -> None:
        detection = latest_available_hrrr_cycle_with_metadata(
            session=FakeHrrrIdxSession(complete_cycle=12),
            now=datetime(2024, 5, 4, 19, tzinfo=timezone.utc),
            max_lookback_hours=12,
        )

        self.assertEqual(detection.selected.run_cycle, 12)
        self.assertEqual(detection.metadata["selected"]["runCycle"], 12)
        self.assertFalse(detection.metadata["checkedCycles"][0]["complete"])
        self.assertTrue(detection.metadata["checkedCycles"][1]["complete"])
        self.assertTrue(detection.metadata["selectedCycleWasFallback"])
        self.assertEqual(detection.metadata["requiredForecastHourForCycle"], 48)
        self.assertEqual(detection.metadata["requiredForecastHoursChecked"], [0, 48])
        self.assertIn("f48", detection.metadata["fallbackReason"])

    def test_latest_cycle_detection_falls_back_for_requested_hour_completeness(self) -> None:
        detection = latest_available_hrrr_cycle_with_metadata(
            session=MatrixHrrrIdxSession({18: {0}, 12: {0, 12}}),
            now=datetime(2024, 5, 4, 19, tzinfo=timezone.utc),
            max_lookback_hours=12,
            require_forecast_hour=12,
        )

        self.assertEqual(detection.selected.run_cycle, 12)
        self.assertEqual(detection.metadata["requiredForecastHourForCycle"], 12)
        self.assertEqual(detection.metadata["requiredForecastHoursChecked"], [0, 12])
        self.assertTrue(detection.metadata["selectedCycleWasFallback"])
        self.assertIn("f12", detection.metadata["fallbackReason"])

    def test_downsampled_hrrr_fields_keep_consistent_shapes_and_validate(self) -> None:
        lats = np.linspace(25.0, 50.0, 6)
        lons = np.linspace(-125.0, -70.0, 9)
        fields = small_fields((6, 9))

        ds_lats, ds_lons, ds_fields = downsample_hrrr_grid(lats, lons, fields, stride=3)
        validate_decoded_hrrr_fields(ds_lats, ds_lons, ds_fields, required_field_keys=sorted(fields.keys()))

        self.assertEqual(ds_lats.shape, (2,))
        self.assertEqual(ds_lons.shape, (3,))
        self.assertEqual(ds_fields["cape"].shape, (2, 3))

    def test_hrrr_validation_rejects_implausible_required_fields(self) -> None:
        lats = np.linspace(25.0, 50.0, 5)
        lons = np.linspace(-125.0, -70.0, 5)
        fields = small_fields()
        fields["t2m"] = np.full((5, 5), 999.0)

        with self.assertRaises(SelectedHrrrValidationError):
            validate_decoded_hrrr_fields(lats, lons, fields, required_field_keys=sorted(fields.keys()))

    def test_forecast_hour_resolution_defaults_to_deployment_hours(self) -> None:
        self.assertEqual(resolve_forecast_hours(), list(PRODUCTION_FORECAST_HOURS))
        self.assertEqual(resolve_forecast_hours(all_hours=True), list(ALL_FORECAST_HOURS))
        self.assertEqual(resolve_forecast_hours([6, 0, 6]), [0, 6])



    def test_cycle_requirement_uses_requested_forecast_hours(self) -> None:
        hours = resolve_forecast_hours([0, 6, 12])

        self.assertEqual(resolve_required_forecast_hour(hours, cycle_policy="complete-requested"), 12)
        self.assertEqual(resolve_required_forecast_hour(hours, cycle_policy="complete-48"), 48)
        self.assertEqual(resolve_required_forecast_hour(hours, cycle_policy="latest-startable"), 0)
        self.assertEqual(resolve_required_forecast_hour(hours, require_complete_hour=18, cycle_policy="latest-startable"), 18)

    def test_all_hours_cycle_requirement_still_requires_f48(self) -> None:
        hours = resolve_forecast_hours(all_hours=True)

        self.assertEqual(resolve_required_forecast_hour(hours, cycle_policy="complete-requested"), 48)
        self.assertEqual(resolve_cycle_policy(None, incremental=False), "complete-requested")
        self.assertEqual(resolve_cycle_policy(None, incremental=True), "latest-startable")

    def test_incremental_cli_defaults_to_all_hours(self) -> None:
        args = argparse.Namespace(
            incremental=True,
            publish_each_hour=False,
            forecast_hours=None,
            all_hours=False,
            initial_hours=None,
        )

        self.assertEqual(resolve_cli_forecast_hours(args), list(ALL_FORECAST_HOURS))

        args.forecast_hours = [0, 6]
        self.assertEqual(resolve_cli_forecast_hours(args), [0, 6])

    def test_cloud_run_task_shard_splits_requested_hours(self) -> None:
        hours = list(range(10))

        self.assertEqual(resolve_cloud_run_task_forecast_hours(hours, CloudRunTaskShard(index=0, count=3)), [0, 3, 6, 9])
        self.assertEqual(resolve_cloud_run_task_forecast_hours(hours, CloudRunTaskShard(index=1, count=3)), [1, 4, 7])
        self.assertEqual(resolve_cloud_run_task_forecast_hours(hours, CloudRunTaskShard(index=2, count=3)), [2, 5, 8])

    def test_incremental_pipeline_processes_task_shard_but_indexes_all_requested_hours(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        fetched_hours: list[int] = []

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            fetched_hours.append(ref.forecast_hour)
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[2, 2] = 0.2
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            index = run_incremental_pipeline(
                output_dir=Path(tmp) / "latest_incremental",
                forecast_hours=[0, 1, 2, 3],
                process_forecast_hours=[1, 3],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                hour_workers=1,
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

        self.assertEqual(fetched_hours, [1, 3])
        self.assertEqual(index["requestedForecastHours"], [0, 1, 2, 3])
        self.assertEqual(index["processForecastHours"], [1, 3])
        self.assertEqual(index["readyForecastHours"], [1, 3])
        self.assertEqual(index["pendingForecastHours"], [0, 2])
        self.assertEqual(index["status"], "partial")

    def test_pipeline_passes_requested_hour_requirement_to_cycle_detection(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        called: dict[str, int] = {}

        def fake_latest(**kwargs):
            called["required"] = kwargs["require_forecast_hour"]
            return HrrrCycleDetection(
                selected=cycle,
                metadata={
                    "selected": {"runDate": cycle.run_date, "runCycle": cycle.run_cycle, "label": cycle.label},
                    "checkedCycles": [{"runDate": cycle.run_date, "runCycle": cycle.run_cycle, "complete": True, "hours": []}],
                },
            )

        def fake_fetch(_ref, _session):
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ), patch("backend.ml.outlook_pipeline.latest_available_hrrr_cycle_with_metadata", side_effect=fake_latest):
            metadata = run_pipeline(
                output_dir=Path(tmp) / "latest",
                forecast_hours=[0, 6, 12],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                verify_spc=False,
                preview=False,
                min_successful_hours=1,
                max_workers=1,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

        self.assertEqual(called["required"], 12)
        self.assertEqual(metadata["requiredForecastHourForCycle"], 12)
        self.assertEqual(metadata["requestedForecastHours"], [0, 6, 12])
        self.assertEqual(metadata["cyclePolicy"]["name"], "complete-requested")

    def test_incremental_latest_startable_leaves_future_missing_hours_pending(self) -> None:
        cycle = HrrrCycle("20240504", 18)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        called: dict[str, int] = {}

        def fake_latest(**kwargs):
            called["required"] = kwargs["require_forecast_hour"]
            return HrrrCycleDetection(
                selected=cycle,
                metadata={
                    "selected": {"runDate": cycle.run_date, "runCycle": cycle.run_cycle, "label": cycle.label},
                    "checkedCycles": [{"runDate": cycle.run_date, "runCycle": cycle.run_cycle, "complete": True, "hours": []}],
                },
            )

        def fake_fetch(ref, _session):
            if ref.forecast_hour > 0:
                raise FileNotFoundError(ref.idx_url)
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ), patch("backend.ml.outlook_pipeline.latest_available_hrrr_cycle_with_metadata", side_effect=fake_latest):
            index = run_incremental_pipeline(
                output_dir=Path(tmp) / "latest_incremental",
                forecast_hours=[0, 1, 2],
                now=datetime(2024, 5, 4, 19, tzinfo=timezone.utc),
                tile_stride=1,
                max_workers=1,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

        self.assertEqual(called["required"], 0)
        self.assertEqual(index["cyclePolicy"]["name"], "latest-startable")
        self.assertEqual(index["readyForecastHours"], [0])
        self.assertEqual(index["failedForecastHours"], [])
        self.assertEqual(index["pendingForecastHours"], [1, 2])

    def test_hrrr_500mb_wind_vectors_use_real_uv_components_in_knots(self) -> None:
        lats = np.linspace(25.0, 50.0, 30)
        lons = np.linspace(-125.0, -70.0, 30)
        u500 = np.full((30, 30), 10.0)
        v500 = np.full((30, 30), -5.0)

        vectors = _wind500_vectors_from_fields(u500, v500, lats, lons)

        self.assertGreater(len(vectors), 0)
        first = vectors[0]
        self.assertEqual(first["level"], "500mb")
        self.assertAlmostEqual(first["uKt"], 10.0 * 1.9438445)
        self.assertAlmostEqual(first["vKt"], -5.0 * 1.9438445)
        self.assertAlmostEqual(first["speedKt"], np.hypot(10.0, -5.0) * 1.9438445)

    def test_hrrr_500mb_vectors_require_valid_conus_fields(self) -> None:
        lats = np.linspace(5.0, 10.0, 30)
        lons = np.linspace(-150.0, -140.0, 30)
        vectors = _wind500_vectors_from_fields(np.ones((30, 30)), np.ones((30, 30)), lats, lons)

        self.assertEqual(vectors, [])

    def test_missing_hrrr_500mb_height_returns_no_contours(self) -> None:
        lats = np.linspace(25.0, 50.0, 5)
        lons = np.linspace(-125.0, -70.0, 5)

        self.assertEqual(_hgt500_lines_from_field(None, lats, lons), [])

    def test_hrrr_500mb_contours_are_safe_under_parallel_hours(self) -> None:
        lats = np.linspace(25.0, 50.0, 80)
        lons = np.linspace(-125.0, -70.0, 100)
        lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
        hgt500 = 5250.0 + ((lat_grid - 25.0) / 25.0) * 760.0 + np.sin((lon_grid + 100.0) / 5.0) * 30.0

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: _hgt500_lines_from_field(hgt500, lats, lons), range(8)))

        self.assertTrue(all(lines for lines in results))

    def test_500mb_overlay_uses_real_full_conus_fields(self) -> None:
        lats = np.linspace(20.0, 55.0, 48)
        lons = np.linspace(-130.0, -60.0, 72)
        _lon_grid, lat_grid = np.meshgrid(lons, lats)
        hgt500 = 5250.0 + ((lat_grid - 20.0) / 35.0) * 760.0
        u500 = np.full_like(hgt500, 12.0)
        v500 = np.full_like(hgt500, -6.0)

        def fake_fetcher(_target_dt, grid_stride=4):
            return {
                "runDate": "20240504",
                "runCycle": 12,
                "modelForecastHour": 3,
                "validTimeISO": "2024-05-04T15:00:00Z",
                "gridStride": grid_stride,
                "lats": lats,
                "lons": lons,
                "fields": {"hgt500": hgt500, "u500": u500, "v500": v500},
                "cacheHit": False,
                "cachePath": None,
            }

        overlay = fetch_full_conus_500mb_overlay(
            datetime(2024, 5, 4, 15, tzinfo=timezone.utc),
            fetcher=fake_fetcher,
        )

        self.assertEqual(overlay["metadata"]["domain"], "CONUS")
        self.assertTrue(overlay["metadata"]["hasHeightContours"])
        self.assertTrue(overlay["metadata"]["hasWindVectors"])
        self.assertGreater(overlay["metadata"]["windVectorCount"], 1)
        self.assertGreater(overlay["metadata"]["heightContourCount"], 1)
        self.assertTrue(all(line["value"] in HGT500_CONTOUR_LEVELS for line in overlay["upperAirLines"]))
        self.assertTrue(any(min(coord[0] for coord in line["coords"]) <= -125.0 for line in overlay["upperAirLines"]))
        first_vector = overlay["upperAirVectors"][0]
        self.assertAlmostEqual(first_vector["uKt"], 12.0 * 1.9438445)
        self.assertAlmostEqual(first_vector["vKt"], -6.0 * 1.9438445)

    def test_overlay_message_decode_does_not_require_surface_cape(self) -> None:
        values = np.ones((2, 2))
        messages = [
            {"category": 3, "parameter": 5, "level_type": 100, "level_value": 50000.0, "lats": np.array([20.0, 21.0]), "lons": np.array([-100.0, -99.0]), "values": values * 5700.0},
            {"category": 2, "parameter": 2, "level_type": 100, "level_value": 50000.0, "lats": np.array([20.0, 21.0]), "lons": np.array([-100.0, -99.0]), "values": values * 10.0},
            {"category": 2, "parameter": 3, "level_type": 100, "level_value": 50000.0, "lats": np.array([20.0, 21.0]), "lons": np.array([-100.0, -99.0]), "values": values * -5.0},
        ]

        _lats, _lons, fields = _messages_to_fields(messages, require_cape=False)

        self.assertEqual(sorted(fields), ["hgt500", "u500", "v500"])

    def test_gridded_features_include_derived_severe_weather_fields(self) -> None:
        fields = small_fields()
        features = gridded_features_from_fields(fields, forecast_hour=18)

        from backend.ml.features import FEATURE_NAMES
        self.assertEqual(features.shape, (5, 5))
        self.assertEqual(features.matrix.shape, (25, len(FEATURE_NAMES)))
        self.assertAlmostEqual(float(features.raw["forecastHour"][0, 0]), 18.0)
        self.assertGreater(float(features.raw["shear06Kt"][0, 0]), 0.0)
        self.assertGreater(float(features.raw["sfcDewpointF"][0, 0]), 60.0)
        self.assertTrue(np.all((features.normalized["mucape"] >= 0.0) & (features.normalized["mucape"] <= 1.0)))

    def test_probability_categories_use_spc_day1_non_sig_tornado_table(self) -> None:
        fields = small_fields((2, 4))
        fields["cape_mu"] = np.full((2, 4), 3200.0)
        fields["cape_ml"] = np.full((2, 4), 2400.0)
        fields["u500"] = np.full((2, 4), 42.0)
        fields["td2m"] = np.full((2, 4), 297.0)
        fields["srh01"] = np.full((2, 4), 260.0)
        fields["srh03"] = np.full((2, 4), 340.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probabilities = {
            "tornado": np.array([[0.00, 0.02, 0.05, 0.10], [0.15, 0.30, 0.45, 0.60]]),
            "hail": np.zeros((2, 4)),
            "wind": np.zeros((2, 4)),
        }

        categories = category_grid_from_probabilities(probabilities, features)

        self.assertEqual([[SPC_RISK_LABELS[int(v)] for v in row] for row in categories], [
            ["TSTM", "MRGL", "SLGT", "ENH"],
            ["ENH", "MDT", "HIGH", "HIGH"],
        ])

    def test_probability_categories_use_spc_day1_non_sig_wind_hail_table(self) -> None:
        fields = small_fields((1, 5))
        fields["cape_mu"] = np.full((1, 5), 3600.0)
        fields["cape_ml"] = np.full((1, 5), 2800.0)
        fields["u500"] = np.full((1, 5), 55.0)
        fields["td2m"] = np.full((1, 5), 297.0)
        fields["srh01"] = np.full((1, 5), 260.0)
        fields["srh03"] = np.full((1, 5), 340.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probabilities = {
            "tornado": np.zeros((1, 5)),
            "hail": np.array([[0.05, 0.15, 0.30, 0.45, 0.60]]),
            "wind": np.zeros((1, 5)),
        }

        categories = category_grid_from_probabilities(probabilities, features)

        self.assertEqual([[SPC_RISK_LABELS[int(v)] for v in row] for row in categories], [
            ["MRGL", "SLGT", "ENH", "ENH", "MDT"],
        ])

    def test_hail_wind_can_still_register_as_mrgl(self) -> None:
        fields = small_fields((1, 2))
        probabilities = {
            "tornado": np.zeros((1, 2)),
            "hail": np.array([[0.05, 0.0]]),
            "wind": np.array([[0.0, 0.05]]),
        }

        categories = category_grid_from_probabilities(probabilities, gridded_features_from_fields(fields, forecast_hour=0))

        self.assertEqual([[SPC_RISK_LABELS[int(v)] for v in row] for row in categories], [
            ["MRGL", "MRGL"],
        ])

    def test_hail_wind_slgt_requires_hazard_specific_support(self) -> None:
        fields = small_fields((1, 2))
        probabilities = {
            "tornado": np.zeros((1, 2)),
            "hail": np.full((1, 2), 0.18),
            "wind": np.full((1, 2), 0.18),
        }

        categories = category_grid_from_probabilities(probabilities, gridded_features_from_fields(fields, forecast_hour=0))

        self.assertTrue(np.all(categories <= SPC_RISK_LABELS.index("MRGL")))

        supported_fields = small_fields((1, 2))
        supported_fields["cape_mu"] = np.full((1, 2), 1100.0)
        supported_fields["cape_ml"] = np.full((1, 2), 900.0)
        supported_fields["cape"] = np.full((1, 2), 850.0)
        supported_fields["u500"] = np.full((1, 2), 35.0)
        supported_fields["td2m"] = np.full((1, 2), 290.0)
        supported_fields["srh03"] = np.full((1, 2), 200.0)
        supported = gridded_features_from_fields(supported_fields, forecast_hour=0)

        supported_categories = category_grid_from_probabilities(probabilities, supported)

        self.assertTrue(np.all(supported_categories >= SPC_RISK_LABELS.index("SLGT")))

    def test_category_grid_gates_uncalibrated_high_end_risk(self) -> None:
        weak_fields = small_fields((1, 2))
        weak_fields["td2m"] = np.full((1, 2), 282.0)
        weak_features = gridded_features_from_fields(weak_fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((1, 2), 0.80),
            "hail": np.full((1, 2), 0.90),
            "wind": np.full((1, 2), 0.90),
        }

        weak_categories = category_grid_from_probabilities(high_probs, weak_features)

        self.assertTrue(np.all(weak_categories <= SPC_RISK_LABELS.index("TSTM")))

        cape_driven_fields = small_fields((1, 2))
        cape_driven_fields["cape_mu"] = np.full((1, 2), 2200.0)
        cape_driven_fields["cape"] = np.full((1, 2), 1900.0)
        cape_driven_fields["td2m"] = np.full((1, 2), 296.0)
        cape_driven_fields["u500"] = np.full((1, 2), 23.5)
        cape_driven_fields["v500"] = np.full((1, 2), 1.0)
        cape_driven_fields["srh01"] = np.full((1, 2), 35.0)
        cape_driven_fields["srh03"] = np.full((1, 2), 90.0)
        cape_driven_features = gridded_features_from_fields(cape_driven_fields, forecast_hour=0)

        cape_driven_categories = category_grid_from_probabilities(high_probs, cape_driven_features)

        self.assertTrue(np.all(cape_driven_categories <= SPC_RISK_LABELS.index("MRGL")))

        strong_fields = small_fields((1, 2))
        strong_fields["cape_mu"] = np.full((1, 2), 2000.0)
        strong_fields["u500"] = np.full((1, 2), 35.0)
        strong_features = gridded_features_from_fields(strong_fields, forecast_hour=0)
        candidate_model = {
            "trainingRows": 6000,
            "datasetQuality": {
                "trainingRows": 6000,
                "minimumRecommendedRows": 5000,
                "experimentalOnly": False,
                "status": "candidate",
            },
        }

        capped_categories = category_grid_from_probabilities(high_probs, strong_features, candidate_model)

        self.assertTrue(np.all(capped_categories <= SPC_RISK_LABELS.index("ENH")))

    def test_environmental_probability_caps_match_model_category_cap_thresholds(self) -> None:
        fields = small_fields((2, 2))
        fields["cape_mu"] = np.full((2, 2), 3200.0)
        fields["cape_ml"] = np.full((2, 2), 2400.0)
        fields["u500"] = np.full((2, 2), 40.0)
        fields["srh01"] = np.full((2, 2), 220.0)
        fields["srh03"] = np.full((2, 2), 320.0)
        fields["td2m"] = np.full((2, 2), 296.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.80),
            "hail": np.full((2, 2), 0.90),
            "wind": np.full((2, 2), 0.90),
        }
        candidate_model = {
            "trainingRows": 6000,
            "datasetQuality": {
                "trainingRows": 6000,
                "minimumRecommendedRows": 5000,
                "experimentalOnly": False,
                "status": "candidate",
            },
        }

        capped = apply_environmental_probability_caps(high_probs, features, candidate_model)
        self.assertLessEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.299)
        self.assertLessEqual(float(np.nanmax(capped.probabilities["hail"])), 0.59)
        self.assertLessEqual(float(np.nanmax(capped.probabilities["wind"])), 0.59)
        categories = category_grid_from_probabilities(capped.probabilities, features, candidate_model)
        self.assertTrue(np.all(categories <= SPC_RISK_LABELS.index("ENH")))

    def test_great_lakes_tornado_logic_penalizes_marine_stability(self) -> None:
        fields = small_fields((2, 2))
        fields["cape"] = np.full((2, 2), 250.0)      # sbcape collapsed but not fully elevated
        fields["cape_ml"] = np.full((2, 2), 1200.0)  # MLCAPE strong (prevents environmental capping)
        fields["cape_mu"] = np.full((2, 2), 1200.0)  # mucape elevated
        fields["cin"] = np.full((2, 2), -120.0)      # strong capping
        fields["cin_ml"] = np.full((2, 2), -120.0)   # strong capping (prioritized by gridded features)
        fields["u500"] = np.full((2, 2), 35.0)
        fields["srh01"] = np.full((2, 2), 220.0)     # strong low-level shear/helicity (prevents capping)
        fields["srh03"] = np.full((2, 2), 350.0)     # strong 0-3km shear/helicity (prevents capping)
        fields["td2m"] = np.full((2, 2), 292.0)      # warm/moist dewpoint (65.9F, prevents dry air and LCL caps)

        # Great Lakes coordinates (e.g. Lake Michigan lat 44.0, lon -86.5)
        lats = np.array([43.9, 44.1])
        lons = np.array([-86.6, -86.4])

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.05),
            "wind": np.full((2, 2), 0.05),
        }

        capped = apply_environmental_probability_caps(high_probs, features, lats=lats, lons=lons)

        # Verify the modifier was applied, penalized, and is bounded (capped above 0.065 because raw is 0.10, multiplier min is 0.65)
        self.assertTrue(capped.report["greatLakesTornadoModifierApplied"])
        self.assertGreater(capped.report["greatLakesPenalizedCells"], 0)
        self.assertEqual(capped.report["greatLakesEnhancedCells"], 0)
        # raw 0.10 * 0.65 (min penalty) = 0.065
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.065)

    def test_great_lakes_tornado_logic_grants_lake_boundary_bonus(self) -> None:
        fields = small_fields((2, 2))
        fields["cape"] = np.full((2, 2), 1800.0)      # SBCAPE strong
        fields["cape_ml"] = np.full((2, 2), 1600.0)
        fields["cape_mu"] = np.full((2, 2), 2000.0)
        fields["cin"] = np.full((2, 2), -10.0)        # minimal capping
        fields["u500"] = np.full((2, 2), 40.0)        # strong deep shear (discrete supercell active)
        fields["srh01"] = np.full((2, 2), 220.0)      # strong low-level shear/helicity
        fields["srh03"] = np.full((2, 2), 350.0)      # strong 0-3km shear/helicity
        fields["td2m"] = np.full((2, 2), 291.0)      # warm/moist dewpoint (64F)

        # Great Lakes coordinates (lat 44.0, lon -86.5)
        lats = np.array([43.9, 44.1])
        lons = np.array([-86.6, -86.4])

        features = gridded_features_from_fields(fields, forecast_hour=0)
        # Adjust LCL to be low (< 1000m)
        features.raw["lclM"] = np.full((2, 2), 800.0)

        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.05),
            "wind": np.full((2, 2), 0.05),
        }

        capped = apply_environmental_probability_caps(high_probs, features, lats=lats, lons=lons)

        # Verify the modifier was applied and enhanced risk near boundary
        self.assertTrue(capped.report["greatLakesTornadoModifierApplied"])
        self.assertEqual(capped.report["greatLakesPenalizedCells"], 0)
        self.assertGreater(capped.report["greatLakesEnhancedCells"], 0)
        # raw 0.10 * 1.15 (supercell upgrade) * 1.15 (lake bonus) = 0.13225
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.13225)

    def test_great_lakes_tornado_logic_does_not_affect_other_regions(self) -> None:
        fields = small_fields((2, 2))
        fields["cape"] = np.full((2, 2), 800.0)       # SBCAPE strong but below 1000.0 (prevents discrete supercell upgrade)
        fields["cape_ml"] = np.full((2, 2), 1200.0)  # MLCAPE strong (prevents environmental capping)
        fields["cape_mu"] = np.full((2, 2), 1200.0)
        fields["cin"] = np.full((2, 2), -40.0)       # minimal capping
        fields["cin_ml"] = np.full((2, 2), -40.0)    # minimal capping
        fields["u500"] = np.full((2, 2), 35.0)        # strong deep shear (discrete supercell active, QLCS inactive)
        fields["srh01"] = np.full((2, 2), 220.0)     # strong low-level shear/helicity (prevents capping)
        fields["srh03"] = np.full((2, 2), 350.0)     # strong 0-3km shear/helicity (prevents capping)
        fields["td2m"] = np.full((2, 2), 292.0)      # warm/moist dewpoint (65.9F, prevents dry air and LCL caps)

        # Non-Great Lakes coordinates (e.g. Oklahoma lat 35.0, lon -97.0)
        lats = np.array([34.9, 35.1])
        lons = np.array([-97.1, -96.9])

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.05),
            "wind": np.full((2, 2), 0.05),
        }

        capped = apply_environmental_probability_caps(high_probs, features, lats=lats, lons=lons)
        # Verify that despite stable profile, Oklahoma has NO Great Lakes modifier effect
        self.assertTrue(capped.report["greatLakesTornadoModifierApplied"])
        self.assertEqual(capped.report["greatLakesPenalizedCells"], 0)
        self.assertEqual(capped.report["greatLakesEnhancedCells"], 0)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.10)

    def test_storm_mode_discrete_supercell_enhances_tornado_risk(self) -> None:
        fields = small_fields((2, 2))
        fields["cape_ml"] = np.full((2, 2), 1200.0)
        fields["cape"] = np.full((2, 2), 1200.0)      # strong sbcape
        fields["u500"] = np.full((2, 2), 40.0)        # strong deep shear
        fields["srh01"] = np.full((2, 2), 220.0)      # strong low-level shear/SRH
        fields["srh03"] = np.full((2, 2), 350.0)
        fields["td2m"] = np.full((2, 2), 292.0)       # warm/moist dewpoint (lcl will be 1000m, low LCL)

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.05),
            "wind": np.full((2, 2), 0.05),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["discreteSupercellCells"], 0)
        # raw 0.10 * 1.15 = 0.115
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.115)

    def test_storm_mode_qlcs_favors_wind_and_limits_tornado(self) -> None:
        fields = small_fields((2, 2))
        fields["cape_ml"] = np.full((2, 2), 800.0)
        fields["cape"] = np.full((2, 2), 900.0)
        fields["u500"] = np.full((2, 2), 32.0)        # moderate deep shear
        fields["srh01"] = np.full((2, 2), 180.0)      # strong low-level helicity/shear
        fields["srh03"] = np.full((2, 2), 250.0)
        fields["td2m"] = np.full((2, 2), 290.0)       # warm/moist dewpoint

        features = gridded_features_from_fields(fields, forecast_hour=0)
        # Make storm rel low so it is not a clean discrete supercell
        features.raw["stormRelWindKt"] = np.full((2, 2), 10.0)

        high_probs = {
            "tornado": np.full((2, 2), 0.20),
            "hail": np.full((2, 2), 0.05),
            "wind": np.full((2, 2), 0.10),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["qlcsCells"], 0)
        # wind enhanced: 0.10 * 1.10 = 0.11
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.11)
        # tornado capped strictly at 0.099
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.099)

    def test_storm_mode_pulse_limits_organized_severe(self) -> None:
        fields = small_fields((2, 2))
        fields["cape"] = np.full((2, 2), 2000.0)      # high CAPE
        fields["cape_ml"] = np.full((2, 2), 1800.0)
        fields["cape_mu"] = np.full((2, 2), 2200.0)
        fields["u500"] = np.full((2, 2), 5.0)         # very weak shear
        fields["srh01"] = np.full((2, 2), 10.0)
        fields["srh03"] = np.full((2, 2), 15.0)

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.20),
            "wind": np.full((2, 2), 0.20),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["pulseCells"], 0)
        # tornado capped at 0.019
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)
        # wind and hail capped at 0.14
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.14)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.14)

    def test_storm_mode_elevated_convection_suppresses_tornado_but_keeps_hail(self) -> None:
        fields = small_fields((2, 2))
        fields["cape"] = np.full((2, 2), 50.0)        # stable at surface
        fields["cape_ml"] = np.full((2, 2), 1200.0)   # MLCAPE strong (prevents environmental capping)
        fields["cape_mu"] = np.full((2, 2), 1500.0)   # highly unstable aloft
        fields["cin"] = np.full((2, 2), -150.0)
        fields["cin_ml"] = np.full((2, 2), -150.0)
        fields["u500"] = np.full((2, 2), 35.0)
        fields["srh01"] = np.full((2, 2), 220.0)
        fields["srh03"] = np.full((2, 2), 350.0)
        fields["td2m"] = np.full((2, 2), 292.0)

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.10),
            "wind": np.full((2, 2), 0.10),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["elevatedCells"], 0)
        # tornado capped at 0.019
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)
        # wind probability scaled directly by 0.70: 0.10 * 0.7 = 0.07
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.07)
        # hail risk preserved
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.10)

    def test_storm_mode_high_based_favors_wind_and_limits_tornado(self) -> None:
        fields = small_fields((2, 2))
        fields["cape"] = np.full((2, 2), 1200.0)
        fields["cape_ml"] = np.full((2, 2), 1000.0)
        fields["cape_mu"] = np.full((2, 2), 1500.0)
        fields["u500"] = np.full((2, 2), 35.0)
        fields["srh01"] = np.full((2, 2), 220.0)
        fields["srh03"] = np.full((2, 2), 350.0)
        # Set td2m very low so that T2m - Td2m is large, creating high LCL but avoiding the 0.04 dry-air cap
        fields["td2m"] = np.full((2, 2), 283.0)       # dewpoint ~49.7F (lcl will be np.clip(125 * 17, 100, 3500) = 2125m)

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.10),
            "wind": np.full((2, 2), 0.10),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["highBasedCells"], 0)
        # tornado capped at 0.019
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)
        # wind preserved/enhanced (0.10 * 1.05 = 0.105)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.105)
        # hail fully preserved
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.10)

    def test_storm_mode_tropical_mini_supercell_allows_tornado_risk(self) -> None:
        fields = small_fields((2, 2))
        fields["cape_ml"] = np.full((2, 2), 400.0)    # modest CAPE (normally capped to 0.019 for mlcape < 500)
        fields["cape"] = np.full((2, 2), 400.0)
        fields["cape_mu"] = np.full((2, 2), 600.0)
        fields["pwat"] = np.full((2, 2), 50.0)        # extremely high PWAT (~2.0 in)
        fields["u500"] = np.full((2, 2), 35.0)
        fields["srh01"] = np.full((2, 2), 220.0)      # strong low-level shear
        fields["srh03"] = np.full((2, 2), 350.0)
        fields["td2m"] = np.full((2, 2), 294.0)       # very warm/moist dewpoint

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.05),
            "wind": np.full((2, 2), 0.05),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["tropicalMiniSupercellCells"], 0)
        # tornado cap relaxed to 0.099 (so 0.10 is capped to exactly 0.099)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.099)

    def test_storm_mode_cold_core_allows_brief_tornado(self) -> None:
        fields = small_fields((2, 2))
        fields["hgt500"] = np.full((2, 2), 5400.0)    # extremely low 500mb height
        fields["td2m"] = np.full((2, 2), 282.0)       # cool surface (47.9F, normally capped by sfcDewpointF < 55)
        fields["cape"] = np.full((2, 2), 600.0)        # moderate CAPE to avoid 0.04 wind/hail caps
        fields["cape_ml"] = np.full((2, 2), 600.0)
        fields["cape_mu"] = np.full((2, 2), 700.0)
        fields["u500"] = np.full((2, 2), 35.0)
        fields["srh01"] = np.full((2, 2), 110.0)      # moderate low-level shear (avoids QLCS)
        fields["srh03"] = np.full((2, 2), 350.0)
        # Force low LCL
        fields["t2m"] = fields["td2m"] + 4.0          # LCL will be 125.0 * 4 = 500m (< 800m)

        features = gridded_features_from_fields(fields, forecast_hour=0)
        high_probs = {
            "tornado": np.full((2, 2), 0.10),
            "hail": np.full((2, 2), 0.20),
            "wind": np.full((2, 2), 0.20),
        }

        capped = apply_environmental_probability_caps(high_probs, features)
        self.assertGreater(capped.report["coldCoreCells"], 0)
        # tornado cap relaxed to 0.049
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.049)
        # wind and hail capped conservatively at 0.14
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.14)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.14)

    def test_plains_dryline_and_discrete_supercell_boost(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Oklahoma
        lats = np.full(shape, 35.0)
        lons = np.array([[-98.0, -96.0], [-98.0, -96.0]])
        # Set up a strong zonal dewpoint gradient (grad_dew >= 1.0)
        fields["td2m"] = np.array([[282.0, 292.0], [282.0, 292.0]]) # 47.9F to 65.9F
        # Standard temperature (warm)
        fields["t2m"] = fields["td2m"] + 5.0
        
        # High instability and shear
        fields["cape"] = np.full(shape, 2000.0)
        fields["cape_ml"] = np.full(shape, 2000.0)
        fields["cape_mu"] = np.full(shape, 2200.0)
        fields["cin"] = np.full(shape, 0.0)
        fields["cin_ml"] = np.full(shape, 0.0)
        
        # Kinematics
        fields["u10"] = np.full(shape, -5.0) # easterly backed wind
        fields["v10"] = np.full(shape, 2.0)
        fields["u500"] = np.full(shape, 35.0)
        fields["v500"] = np.full(shape, 10.0)
        fields["srh01"] = np.full(shape, 200.0)
        fields["srh03"] = np.full(shape, 250.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.10),
            "hail": np.full(shape, 0.20),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["plainsDrylineCells"], 0)
        self.assertGreater(capped.report["plainsDiscreteSupercellCells"], 0)
        # Tornado and hail probabilities should be boosted:
        # Tornado: 0.10 * 1.15 (standard supercell) * 1.15 (plains supercell) = 0.13225
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.13225)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.276)


    def test_plains_triple_point_focused_forcing(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        lats = np.full(shape, 35.0)
        lons = np.array([[-98.0, -96.0], [-98.0, -96.0]])
        # Dewpoint gradient (dryline)
        fields["td2m"] = np.array([[282.0, 292.0], [282.0, 292.0]])
        # Temperature gradient (front)
        fields["t2m"] = np.array([[285.0, 298.0], [285.0, 298.0]]) # strong temperature gradient
        
        # High instability and shear
        fields["cape"] = np.full(shape, 1500.0)
        fields["cape_ml"] = np.full(shape, 1500.0)
        fields["cape_mu"] = np.full(shape, 1800.0)
        fields["cin"] = np.full(shape, -10.0)
        
        # Kinematics with backed winds
        fields["u10"] = np.full(shape, -6.0)
        fields["u500"] = np.full(shape, 40.0)
        fields["srh01"] = np.full(shape, 180.0)
        fields["srh03"] = np.full(shape, 220.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.10),
            "wind": np.full(shape, 0.10),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["plainsTriplePointCells"], 0)
        self.assertGreater(capped.report["plainsDiscreteSupercellCells"], 0)

    def test_plains_warm_front_backed_winds(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        lats = np.full(shape, 35.0)
        lons = np.array([[-98.0, -96.0], [-98.0, -96.0]])
        # Warm front temperature gradient
        fields["t2m"] = np.array([[288.0, 297.0], [288.0, 297.0]])
        fields["td2m"] = np.full(shape, 290.0) # moist dewpoint (62.3F)
        
        # Backed surface wind & strong low-level helicity
        fields["u10"] = np.full(shape, -4.0)
        fields["srh01"] = np.full(shape, 160.0)
        fields["srh03"] = np.full(shape, 200.0)
        fields["cape"] = np.full(shape, 1800.0)
        fields["cape_ml"] = np.full(shape, 1800.0)
        fields["cape_mu"] = np.full(shape, 2000.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.10),
            "wind": np.full(shape, 0.10),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["plainsWarmFrontCells"], 0)

    def test_plains_eml_moderate_cin_cap_handling(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        lats = np.full(shape, 35.0)
        lons = np.array([[-98.0, -96.0], [-98.0, -96.0]])
        
        # Strong zonal dewpoint gradient (dryline)
        fields["td2m"] = np.array([[282.0, 292.0], [282.0, 292.0]])
        fields["t2m"] = fields["td2m"] + 5.0
        
        # Moderate CIN capping (-200 J/kg)
        fields["cin"] = np.full(shape, -200.0)
        fields["cin_ml"] = np.full(shape, -200.0)
        
        # High MLCAPE and shear
        fields["cape"] = np.full(shape, 2000.0)
        fields["cape_ml"] = np.full(shape, 2000.0)
        fields["cape_mu"] = np.full(shape, 2200.0)
        fields["u10"] = np.full(shape, -6.0) # backed surface wind
        fields["u500"] = np.full(shape, 40.0)
        fields["srh01"] = np.full(shape, 180.0)
        fields["srh03"] = np.full(shape, 220.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        # Moderate input probabilities that would normally be slammed by -200 CIN caps
        probs = {
            "tornado": np.full(shape, 0.08),
            "hail": np.full(shape, 0.30),
            "wind": np.full(shape, 0.25),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        # Check that EML cap relaxation worked
        self.assertGreater(capped.report["plainsDiscreteSupercellCells"], 0)
        # Standard cap would force tornado to 0.049. With Plains relaxation, it is boosted by +15% to 0.092
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.1058)

    def test_plains_linear_forcing_shifts_weight(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        lats = np.full(shape, 35.0)
        lons = np.array([[-98.0, -96.0], [-98.0, -96.0]])
        
        # Strong temperature gradient (cold front)
        fields["t2m"] = np.array([[282.0, 298.0], [282.0, 298.0]])
        fields["td2m"] = np.full(shape, 290.0)
        
        # Unbacked winds (westerly component u10 >= 0)
        fields["u10"] = np.full(shape, 5.0)
        fields["v10"] = np.full(shape, -4.0)
        fields["u500"] = np.full(shape, 35.0)
        fields["srh01"] = np.full(shape, 80.0)
        fields["srh03"] = np.full(shape, 120.0)
        fields["cape"] = np.full(shape, 1200.0)
        fields["cape_ml"] = np.full(shape, 1200.0)
        fields["cape_mu"] = np.full(shape, 1400.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.08),
            "hail": np.full(shape, 0.15),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["plainsLinearForcingCells"], 0)
        # Significant tornado capped strictly to 0.049 (original 0.08 capped to 0.049)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.049)
        # Wind/hail boosted by +10% (0.15 * 1.10 = 0.165)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.165)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.165)

    def test_plains_large_hail_supercell_boost(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        lats = np.full(shape, 38.5) # Kansas
        lons = np.full(shape, -98.0)
        
        # High CAPE (lapse rates proxy) and strong shear
        fields["cape"] = np.full(shape, 2600.0)
        fields["cape_ml"] = np.full(shape, 2600.0)
        fields["cape_mu"] = np.full(shape, 2800.0)
        fields["u500"] = np.full(shape, 45.0) # strong shear
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.30),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["plainsLargeHailSetups"], 0)
        # Hail should be boosted by +20% (0.30 * 1.20 = 0.36)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.36)

    def test_dixie_se_hslc_nocturnal_risk(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Mississippi
        lats = np.full(shape, 32.5)
        lons = np.full(shape, -89.5)
        
        # High shear, low CAPE setup (MLCAPE = 400, Dewpoint = 62F, shear = 45, srh01 = 220)
        fields["cape"] = np.full(shape, 500.0)
        fields["cape_ml"] = np.full(shape, 400.0)
        fields["cape_mu"] = np.full(shape, 600.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["td2m"] = np.full(shape, 290.0) # 62.3 F
        fields["t2m"] = np.full(shape, 292.0)
        
        # Backed winds and strong shear/helicity
        fields["u10"] = np.full(shape, -5.0)
        fields["u500"] = np.full(shape, 40.0)
        fields["srh01"] = np.full(shape, 220.0) # Strong LLJ
        fields["srh03"] = np.full(shape, 280.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        # Input probability that would normally be restricted to 0.019 under MLCAPE < 500
        probs = {
            "tornado": np.full(shape, 0.08),
            "hail": np.full(shape, 0.05),
            "wind": np.full(shape, 0.05),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["dixieSeHslcCells"], 0)
        # The CAPE-based tornado cap should be relaxed to 0.099 (so 0.08 is preserved),
        # and then boosted by +15% due to srh01 >= 200 (LLJ boost): 0.08 * 1.15 = 0.092
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.092)

    def test_dixie_se_warm_sector_discrete_cells(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Alabama
        lats = np.full(shape, 32.5)
        lons = np.full(shape, -86.5)
        
        # High shear, high instability discrete warm sector
        fields["cape"] = np.full(shape, 1500.0)
        fields["cape_ml"] = np.full(shape, 1200.0)
        fields["cape_mu"] = np.full(shape, 1800.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["td2m"] = np.full(shape, 291.0) # 64.1 F
        fields["t2m"] = np.full(shape, 295.0)
        
        # Backed winds and strong kinematics (discrete supercell triggers)
        fields["u10"] = np.full(shape, -6.0) # Backed easterly
        fields["u500"] = np.full(shape, 45.0)
        fields["srh01"] = np.full(shape, 180.0)
        fields["srh03"] = np.full(shape, 240.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.11),
            "hail": np.full(shape, 0.15),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["dixieSeWarmSectorDiscreteCells"], 0)
        # The tornado probability should be boosted:
        # 0.11 * 1.15 (standard supercell) * 1.20 (dixie warm-sector discrete) = 0.1518
        # Since standard cap is 0.14 and continuous upgrades are applied after caps, 0.1518 is the correct value.
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.1518)

    def test_dixie_se_embedded_qlcs_tornadoes(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Georgia
        lats = np.full(shape, 33.0)
        lons = np.full(shape, -83.5)
        
        # QLCS environment (shear >= 30, srh01 >= 125, not discrete)
        fields["cape"] = np.full(shape, 1200.0)
        fields["cape_ml"] = np.full(shape, 1000.0)
        fields["cape_mu"] = np.full(shape, 1400.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["td2m"] = np.full(shape, 290.0)
        fields["t2m"] = np.full(shape, 293.0)
        
        # u500 is set to 18 to keep shear below 35 (31.1 kt) to avoid discrete_supercell classification
        fields["u10"] = np.full(shape, 2.0)
        fields["u500"] = np.full(shape, 18.0)
        fields["srh01"] = np.full(shape, 180.0) # Very strong srh01
        fields["srh03"] = np.full(shape, 220.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.15),
            "hail": np.full(shape, 0.10),
            "wind": np.full(shape, 0.10),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["dixieSeEmbeddedQlcsCells"], 0)
        # Embedded QLCS tornado capped at 0.099
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.099)

    def test_dixie_se_florida_sea_breeze_pulse(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Florida
        lats = np.full(shape, 28.0)
        lons = np.full(shape, -81.5)
        
        # Sea breeze boundary gradient (strong temperature gradient)
        fields["t2m"] = np.array([[290.0, 298.0], [290.0, 298.0]]) # 18 deg F diff (grad_temp >= 0.8)
        fields["td2m"] = np.full(shape, 294.0) # rich dewpoint (70F)
        
        fields["cape"] = np.full(shape, 2500.0)
        fields["cape_ml"] = np.full(shape, 2000.0)
        fields["cape_mu"] = np.full(shape, 2800.0)
        fields["cin"] = np.full(shape, 0.0) # cin > -30.0 for spouts
        fields["cin_ml"] = np.full(shape, 0.0)
        fields["u500"] = np.full(shape, 10.0) # very weak shear
        fields["srh01"] = np.full(shape, 20.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        # Favorable spout conditions: sbcape >= 500, lcl <= 1000
        fields["lcl"] = np.full(shape, 500.0)
        
        probs = {
            "tornado": np.full(shape, 0.10),
            "hail": np.full(shape, 0.25),
            "wind": np.full(shape, 0.25),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["dixieSeSeaBreezePulseCells"], 0)
        # Landspout/waterspout allowed to reach 0.049
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.049)
        # Wind and hail capped at 0.14 max (but standard weak-kinematic cap limits them to 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.04)

    def test_dixie_se_florida_sea_breeze_supercellular(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Florida
        lats = np.full(shape, 28.0)
        lons = np.full(shape, -81.5)
        
        # Boundary gradient present
        fields["t2m"] = np.array([[290.0, 298.0], [290.0, 298.0]])
        fields["td2m"] = np.full(shape, 292.0)
        
        # Strong background kinematics (shear >= 30, srh01 >= 100)
        fields["cape"] = np.full(shape, 1500.0)
        fields["cape_ml"] = np.full(shape, 1200.0)
        fields["cape_mu"] = np.full(shape, 1800.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["u500"] = np.full(shape, 35.0) # strong shear
        fields["srh01"] = np.full(shape, 130.0) # srh01 >= 125 to avoid standard 0.049 cap
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.06),
            "hail": np.full(shape, 0.15),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["dixieSeSeaBreezeSupercellCells"], 0)
        # Since background kinematics are strong, standard caps apply without sea-breeze pulse penalty:
        # Tornado can exceed 0.019 (0.06 is preserved)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.06)

    def test_dixie_se_moisture_guardrails(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Florida
        lats = np.full(shape, 26.0)
        lons = np.full(shape, -80.5)
        
        # Extremely high moisture (Dewpoint = 72F, PWAT = 2.1 in) but weak kinematics (shear = 10, srh01 = 20)
        fields["td2m"] = np.full(shape, 295.0) # 71.3 F
        fields["t2m"] = np.full(shape, 297.0)
        fields["cape"] = np.full(shape, 2000.0)
        fields["cape_ml"] = np.full(shape, 1800.0)
        fields["cape_mu"] = np.full(shape, 2200.0)
        fields["cin"] = np.full(shape, 0.0)
        fields["u500"] = np.full(shape, 10.0)
        fields["srh01"] = np.full(shape, 20.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.15),
            "hail": np.full(shape, 0.20),
            "wind": np.full(shape, 0.20),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        # Since kinematics are weak, tornado cap must strictly apply (capped to 0.019)
        # and no moisture-based upgrade is allowed.
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)


    def test_midwest_prior_convection_stabilization(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Illinois
        lats = np.full(shape, 40.0)
        lons = np.full(shape, -89.0)
        
        # Stabilized prior convection profile: sbcape collapsed relative to mucape (sbcape = 100, mucape = 1200), cin = -120
        fields["cape"] = np.full(shape, 100.0)
        fields["cape_ml"] = np.full(shape, 80.0)
        fields["cape_mu"] = np.full(shape, 1200.0)
        fields["cin"] = np.full(shape, -120.0)
        fields["cin_ml"] = np.full(shape, -120.0)
        
        # High moisture (pwatIn >= 1.2)
        fields["pwat"] = np.full(shape, 40.0) # pwat = 40mm / 25.4 = 1.57 in
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.15),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["midwestStabilizedCells"], 0)
        # Tornado capped to 0.019, then penalized by -30% (0.019 * 0.70 = 0.0133)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.0133)
        # Wind/hail capped and penalized by -30%. Max wind gets 0.02156 due to standard caps and elevated caps.
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.02156)

    def test_midwest_boundary_enhanced_tornado_bonus(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Indiana
        lats = np.full(shape, 40.0)
        lons = np.full(shape, -86.0)
        
        # Outflow/warm front temperature boundary active (grad_temp >= 0.8)
        fields["t2m"] = np.array([[290.0, 298.0], [290.0, 298.0]]) # 18 deg F diff
        fields["td2m"] = np.full(shape, 292.0)
        
        # Surface-based inflow intact (sbcape >= 1000, cin >= -50, lcl <= 1200)
        fields["cape"] = np.full(shape, 1500.0)
        fields["cape_ml"] = np.full(shape, 1200.0)
        fields["cape_mu"] = np.full(shape, 1800.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["cin_ml"] = np.full(shape, -10.0)
        
        # Strong low-level shear/helicity (srh01 >= 150)
        fields["srh01"] = np.full(shape, 180.0)
        fields["srh03"] = np.full(shape, 240.0)
        fields["u500"] = np.full(shape, 40.0) # deep shear
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.08),
            "hail": np.full(shape, 0.15),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["midwestBoundaryEnhancedCells"], 0)
        # Tornado boosted by standard supercell boost (+15%) and Midwest boundary bonus (+20%):
        # 0.08 * 1.15 * 1.20 = 0.1104, within relaxed cap of 0.14
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.1104)

    def test_high_plains_high_based_wind_hail(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Colorado
        lats = np.full(shape, 40.0)
        lons = np.full(shape, -104.5)
        
        # High based (LCL >= 1800m): t2m - td2m >= 14.4 K -> e.g. t2m = 308, td2m = 293 (spread = 15 K, LCL = 1875m)
        fields["td2m"] = np.full(shape, 293.0) # 67.7F
        fields["t2m"] = np.full(shape, 308.0) # LCL = 125 * 15 = 1875m
        
        # Subcloud dryness spread >= 300m
        fields["pwat"] = np.full(shape, 20.0) # pwat = 20 / 25.4 = 0.78 in -> moistureDepth = 1181m -> spread = 1875 - 1181 = 694m
        
        # Instability and deep shear present (MLCAPE >= 1000, shear >= 30)
        fields["cape"] = np.full(shape, 1500.0)
        fields["cape_ml"] = np.full(shape, 1200.0)
        fields["cape_mu"] = np.full(shape, 1800.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["cin_ml"] = np.full(shape, -10.0)
        fields["u500"] = np.full(shape, 35.0)
        fields["srh01"] = np.full(shape, 60.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.10),
            "hail": np.full(shape, 0.20),
            "wind": np.full(shape, 0.20),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["highPlainsHighBasedCells"], 0)
        # Tornado strictly capped at 0.019
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)
        # Wind boosted by +15% (0.20 * 1.15 = 0.23) and hail boosted by +10% (0.20 * 1.10 = 0.22)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.23)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.22)

    def test_steep_lapse_rate_landspout_mode(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Colorado
        lats = np.full(shape, 40.0)
        lons = np.full(shape, -104.5)
        
        # Keep temperature/dewpoint gradient columns-wise to keep grad_temp >= 1.0
        fields["t2m"] = np.array([[290.0, 300.0], [290.0, 300.0]])
        fields["td2m"] = np.array([[282.0, 292.0], [282.0, 292.0]])
        
        # Weak deep shear (< 20)
        fields["u10"] = np.full(shape, 0.0)
        fields["v10"] = np.full(shape, 0.0)
        fields["u500"] = np.full(shape, 5.0)
        fields["v500"] = np.full(shape, 0.0)
        fields["srh01"] = np.full(shape, 20.0)
        
        # Strong sbcape (>= 800), minimal cap (cin >= -20), low LCL (<= 1000)
        fields["cape"] = np.full(shape, 1200.0)
        fields["cin"] = np.full(shape, 0.0)
        fields["cin_ml"] = np.full(shape, 0.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.10),
            "hail": np.full(shape, 0.05),
            "wind": np.full(shape, 0.05),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["steepLapseRateLandspoutCells"], 0)
        # Landspout allowed to reach 0.049 cap despite weak kinematics/shear
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.049)

    def test_northern_plains_elevated_hail(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Montana
        lats = np.full(shape, 46.0)
        lons = np.full(shape, -110.0)
        
        # Elevated convection mode: mucape >= 500, sbcape collapsed (< 100)
        fields["cape"] = np.full(shape, 50.0)
        fields["cape_ml"] = np.full(shape, 40.0)
        fields["cape_mu"] = np.full(shape, 1000.0)
        fields["cin"] = np.full(shape, -150.0)
        fields["cin_ml"] = np.full(shape, -150.0)
        
        # Strong deep shear (shear >= 35)
        fields["u500"] = np.full(shape, 40.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.25),
            "wind": np.full(shape, 0.10),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["northernElevatedHailCells"], 0)
        # Elevated hail cap relaxed to 0.29 (so 0.25 is fully preserved)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.25)

    def test_northern_nocturnal_mcs_wind(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: North Dakota
        lats = np.full(shape, 47.0)
        lons = np.full(shape, -100.0)
        
        # MCS mode (pwat >= 1.4, mucape >= 1000, shear >= 25, srh01 >= 50)
        fields["pwat"] = np.full(shape, 45.0) # 1.57 in
        fields["cape"] = np.full(shape, 1200.0)
        fields["cape_ml"] = np.full(shape, 1000.0)
        fields["cape_mu"] = np.full(shape, 1400.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["cin_ml"] = np.full(shape, -10.0)
        fields["td2m"] = np.full(shape, 290.0)
        fields["t2m"] = np.full(shape, 293.0)
        
        # Kinematics: shear >= 25, srh01 >= 50 (but not discrete)
        fields["u10"] = np.full(shape, 2.0)
        fields["u500"] = np.full(shape, 25.0) # shear = 23 * 1.94 = 44.7 Kt
        fields["srh01"] = np.full(shape, 100.0)
        fields["srh03"] = np.full(shape, 140.0)
        
        # timings: nocturnal timing forecast_hour >= 12
        features = gridded_features_from_fields(fields, forecast_hour=15)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.15),
            "wind": np.full(shape, 0.25),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["northernNocturnalMcsCells"], 0)
        # Wind boosted by standard MCS wind boost (+10%) and Northern nocturnal MCS boost (+10%):
        # 0.25 * 1.10 * 1.10 = 0.3025
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.3025)


    def test_northeast_low_cape_high_shear(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Pennsylvania
        lats = np.full(shape, 40.0)
        lons = np.full(shape, -76.0)
        
        # Low MLCAPE but strong deep shear, high low-level SRH, sufficient dewpoint
        fields["cape"] = np.full(shape, 400.0)
        fields["cape_ml"] = np.full(shape, 300.0)
        fields["cape_mu"] = np.full(shape, 500.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["cin_ml"] = np.full(shape, -10.0)
        
        fields["u10"] = np.full(shape, 0.0)
        fields["v10"] = np.full(shape, 0.0)
        fields["u500"] = np.full(shape, 20.0) # shear = 20 * 1.94 = 38.8 Kt
        fields["srh01"] = np.full(shape, 180.0)
        fields["srh03"] = np.full(shape, 240.0)
        fields["td2m"] = np.full(shape, 287.0) # dewpoint = 56.9 F
        fields["t2m"] = np.full(shape, 292.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.04),
            "hail": np.full(shape, 0.10),
            "wind": np.full(shape, 0.20),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["northeastLowCapeHighShearCells"], 0)
        # Wind cap is relaxed to 0.29 (so 0.20 wind is fully preserved)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.20)
        # Tornado cap relaxed to 0.049 (so 0.04 tornado is fully preserved)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.04)

    def test_northeast_cad_stable_wedge(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Virginia / Appalachians
        lats = np.full(shape, 38.0)
        lons = np.full(shape, -79.0)
        
        # Cool surface temp, easterly wedge wind, collapsed SBCAPE relative to elevated
        fields["t2m"] = np.full(shape, 285.0) # 53.3 F
        fields["td2m"] = np.full(shape, 283.0)
        fields["u10"] = np.full(shape, -5.0)
        fields["v10"] = np.full(shape, -5.0)
        
        fields["cape"] = np.full(shape, 50.0)
        fields["cape_ml"] = np.full(shape, 40.0)
        fields["cape_mu"] = np.full(shape, 400.0)
        
        # Strong shear (shear >= 35) to allow elevated hail cap relaxation
        fields["u500"] = np.full(shape, 20.0) # shear = hypot(25, 16) * 1.94 = 57.6 Kt
        fields["v500"] = np.full(shape, 11.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.25),
            "wind": np.full(shape, 0.15),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["northeastCadStableCells"], 0)
        # Tornado cap strictly limited to 0.019, then penalized by -30% (0.019 * 0.70 = 0.0133)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.0133)
        # Elevated hail cap is relaxed to 0.29 under strong shear (so 0.25 is preserved)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.25)
        # Wind is penalized by -30% (standard cap 0.04 * 0.70 = 0.028)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.028)

    def test_northeast_wedge_front_boundary(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: New York
        lats = np.full(shape, 41.0)
        lons = np.full(shape, -75.0)
        
        # Wedge front boundary active (grad_temp columns-wise)
        fields["t2m"] = np.array([[290.0, 298.0], [290.0, 298.0]])
        fields["td2m"] = np.full(shape, 290.0) # dewpoint = 62.3 F
        
        # Backed easterly winds, strong helicity, surface inflow intact
        fields["u10"] = np.full(shape, -5.0)
        fields["v10"] = np.full(shape, 0.0)
        fields["srh01"] = np.full(shape, 180.0)
        fields["srh03"] = np.full(shape, 240.0)
        
        fields["cape"] = np.full(shape, 1200.0)
        fields["cape_ml"] = np.full(shape, 1000.0)
        fields["cape_mu"] = np.full(shape, 1400.0)
        fields["cin"] = np.full(shape, -10.0)
        fields["cin_ml"] = np.full(shape, -10.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.08),
            "hail": np.full(shape, 0.10),
            "wind": np.full(shape, 0.10),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["northeastWedgeFrontCells"], 0)
        # Tornado boosted by standard supercell boost (+15%), wedge front bonus (+20%), and Great Lakes bonus (+15%):
        # 0.08 * 1.15 * 1.20 * 1.15 = 0.12696
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.12696)

    def test_desert_southwest_dry_microburst(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Arizona
        lats = np.full(shape, 33.0)
        lons = np.full(shape, -112.0)
        
        # High based (LCL >= 2000m): spread 16K -> LCL = 2000m
        fields["td2m"] = np.full(shape, 282.0) # 47.9 F (dry)
        fields["t2m"] = np.full(shape, 298.0) # spread = 16 K -> LCL = 2000m
        fields["pwat"] = np.full(shape, 15.0) # dry PWAT
        
        # Minimal elevated instability (MUCAPE >= 200)
        fields["cape"] = np.full(shape, 250.0)
        fields["cape_ml"] = np.full(shape, 200.0)
        fields["cape_mu"] = np.full(shape, 400.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.05),
            "hail": np.full(shape, 0.05),
            "wind": np.full(shape, 0.20),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["desertSouthwestDryMicrobursts"], 0)
        # Tornado strictly capped at 0.019
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)
        # Wind boosted by +15% dry microburst and +5% high-based convection: 0.20 * 1.15 * 1.05 = 0.2415
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.2415)

    def test_desert_southwest_monsoon_suppressor(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Arizona / New Mexico
        lats = np.full(shape, 32.0)
        lons = np.full(shape, -108.0)
        
        # Deep monsoonal moisture (PWAT >= 1.4 in, Dewpoint >= 60)
        fields["pwat"] = np.full(shape, 45.0) # 1.77 in
        fields["td2m"] = np.full(shape, 290.0) # 62.3 F
        fields["t2m"] = np.full(shape, 293.0)
        
        # Very weak kinematics (shear < 20, srh01 < 50)
        fields["u10"] = np.full(shape, 0.0)
        fields["v10"] = np.full(shape, 0.0)
        fields["u500"] = np.full(shape, 0.0)
        fields["v500"] = np.full(shape, 0.0)
        fields["srh01"] = np.full(shape, 10.0)
        fields["srh03"] = np.full(shape, 20.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.15),
            "hail": np.full(shape, 0.25),
            "wind": np.full(shape, 0.25),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["desertSouthwestMonsoonHeavyRainCells"], 0)
        # All severe hazards strictly suppressed to baseline/marginal caps (tornado 0.01, wind/hail 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.01)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.04)

    def test_pacific_northwest_cold_core_convection(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: California/Oregon border
        lats = np.full(shape, 42.0)
        lons = np.full(shape, -121.0)
        
        # Cold-core setup: hgt500 <= 5550m, cool dewpoint (50 F), low LCL (800m), modest sbcape
        fields["hgt500"] = np.full(shape, 5450.0)
        fields["td2m"] = np.full(shape, 283.15) # 50.0 F
        fields["t2m"] = np.full(shape, 289.55) # LCL = 125 * 6.4 = 800m
        
        fields["cape"] = np.full(shape, 400.0)
        fields["cape_ml"] = np.full(shape, 300.0)
        fields["cape_mu"] = np.full(shape, 500.0)
        fields["cin"] = np.full(shape, -5.0)
        fields["cin_ml"] = np.full(shape, -5.0)
        
        fields["srh01"] = np.full(shape, 100.0)
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.04),
            "hail": np.full(shape, 0.12),
            "wind": np.full(shape, 0.12),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["pacificNorthwestColdCoreCells"], 0)
        # Caps relaxed to 0.049 tornado and 0.14 wind/hail (so inputs are fully preserved)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.12)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.12)

    def test_pacific_northwest_terrain_forced_guardrail(self) -> None:
        shape = (2, 2)
        fields = small_fields(shape)
        # Bounding box coordinates: Washington
        lats = np.full(shape, 47.0)
        lons = np.full(shape, -120.0)
        
        # Weak instability and weak shear (no thermodynamic/kinematic overlap)
        fields["cape"] = np.full(shape, 200.0)
        fields["cape_ml"] = np.full(shape, 150.0)
        fields["cape_mu"] = np.full(shape, 250.0)
        
        fields["u10"] = np.full(shape, 0.0)
        fields["v10"] = np.full(shape, 0.0)
        fields["u500"] = np.full(shape, 5.0) # shear = 5 * 1.94 = 9.7 Kt
        
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probs = {
            "tornado": np.full(shape, 0.08),
            "hail": np.full(shape, 0.20),
            "wind": np.full(shape, 0.20),
        }
        
        capped = apply_environmental_probability_caps(probs, features, lats=lats, lons=lons)
        self.assertGreater(capped.report["pacificNorthwestTerrainForcedClippedCells"], 0)
        # All hazards strictly capped to baseline TSTM/MRGL limits (tornado 0.019, wind/hail 0.14, but standard cap restricts to 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.019)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["hail"])), 0.04)
        self.assertAlmostEqual(float(np.nanmax(capped.probabilities["wind"])), 0.04)


    def test_moderate_environment_can_reach_slgt(self) -> None:
        """Verify that the AND-logic probability caps and relaxed kinematic mask
        allow moderate severe environments (e.g. MUCAPE 900 + 32kt shear where
        only one parameter is marginal) to reach SLGT when the ML model predicts
        probabilities >= 0.15 for hail or wind.  Previously, OR-logic caps and a
        35kt kinematic mask hard-clamped these cells to MRGL."""
        shape = (3, 3)
        fields = small_fields(shape)
        # Moderate instability — MUCAPE just below 1000, but shear is strong
        fields["cape_mu"] = np.full(shape, 900.0)
        fields["cape_ml"] = np.full(shape, 800.0)
        fields["cape"] = np.full(shape, 750.0)
        fields["cin"] = np.full(shape, -30.0)
        fields["cin_ml"] = np.full(shape, -30.0)
        fields["td2m"] = np.full(shape, 290.0)   # dewpoint 62.3°F
        fields["u500"] = np.full(shape, 35.0)     # shear ≈ 61.5kt (very strong, stormRelWindKt >= 24)
        fields["srh01"] = np.full(shape, 120.0)
        fields["srh03"] = np.full(shape, 200.0)

        features = gridded_features_from_fields(fields, forecast_hour=12)
        # ML model outputs probabilities above the SLGT threshold
        probs = {
            "tornado": np.full(shape, 0.01),
            "hail": np.full(shape, 0.18),
            "wind": np.full(shape, 0.16),
        }

        # Stage 1: environmental caps should NOT cap hail/wind to 0.14 because
        # although MUCAPE < 1000, shear >= 32 (AND-logic requires BOTH marginal).
        capped = apply_environmental_probability_caps(probs, features)
        self.assertGreaterEqual(
            float(np.nanmax(capped.probabilities["hail"])), 0.15,
            "Hail should not be capped below SLGT threshold when shear is strong",
        )
        self.assertGreaterEqual(
            float(np.nanmax(capped.probabilities["wind"])), 0.15,
            "Wind should not be capped below SLGT threshold when shear is strong",
        )

        # Stage 2: category grid — the relaxed kinematic mask (30kt) should
        # allow SLGT (ordinal 3) since shear ≈ 42.7kt > 30.
        categories = category_grid_from_probabilities(capped.probabilities, features)
        max_category = int(np.nanmax(categories))
        self.assertGreaterEqual(
            max_category,
            SPC_RISK_LABELS.index("SLGT"),
            "Moderate environments with strong shear should be able to reach SLGT",
        )

    def test_category_probability_ceiling_keeps_tiles_consistent_with_final_categories(self) -> None:
        probabilities = {
            "tornado": np.array([[0.45, 0.45, 0.45, 0.45, 0.45]]),
            "hail": np.array([[0.80, 0.80, 0.80, 0.80, 0.80]]),
            "wind": np.array([[0.80, 0.80, 0.80, 0.80, 0.80]]),
        }
        final_categories = np.array([[
            SPC_RISK_LABELS.index("TSTM"),
            SPC_RISK_LABELS.index("MRGL"),
            SPC_RISK_LABELS.index("SLGT"),
            SPC_RISK_LABELS.index("ENH"),
            SPC_RISK_LABELS.index("MDT"),
        ]], dtype=np.int16)

        capped = apply_category_probability_ceiling(probabilities, final_categories)

        self.assertLess(float(capped.probabilities["tornado"][0, 0]), 0.02)
        self.assertLess(float(capped.probabilities["hail"][0, 1]), 0.15)
        self.assertLess(float(capped.probabilities["wind"][0, 2]), 0.30)
        self.assertLess(float(capped.probabilities["tornado"][0, 3]), 0.30)
        self.assertLess(float(capped.probabilities["tornado"][0, 4]), 0.45)
        self.assertLess(float(capped.probabilities["hail"][0, 3]), 0.60)
        self.assertEqual(float(capped.probabilities["wind"][0, 4]), 0.80)
        self.assertTrue(capped.report["categoryConsistencyCapsApplied"])

    def test_postprocess_downgrades_isolated_high_cells(self) -> None:
        fields = small_fields((9, 9))
        fields["cape_mu"] = np.full((9, 9), 3200.0)
        fields["cape_ml"] = np.full((9, 9), 2200.0)
        fields["u500"] = np.full((9, 9), 38.0)
        fields["srh01"] = np.full((9, 9), 240.0)
        fields["srh03"] = np.full((9, 9), 330.0)
        fields["td2m"] = np.full((9, 9), 297.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.ones((9, 9), dtype=np.int16)
        category_grid[4, 4] = SPC_RISK_LABELS.index("HIGH")
        probabilities = {
            "tornado": np.full((9, 9), 0.01),
            "hail": np.full((9, 9), 0.01),
            "wind": np.full((9, 9), 0.01),
        }

        processed = postprocess_category_grid(category_grid, probabilities, features)

        self.assertLess(int(processed.category_grid[4, 4]), SPC_RISK_LABELS.index("HIGH"))
        self.assertGreaterEqual(processed.report["downgradedCells"]["isolatedComponent"], 1)

    def test_postprocess_keeps_gulf_of_mexico_offshore_cells_strict(self) -> None:
        fields = small_fields((5, 5))
        fields["cape_mu"] = np.full((5, 5), 2600.0)
        fields["cape_ml"] = np.full((5, 5), 1800.0)
        fields["u500"] = np.full((5, 5), 36.0)
        fields["srh01"] = np.full((5, 5), 140.0)
        fields["srh03"] = np.full((5, 5), 220.0)
        fields["td2m"] = np.full((5, 5), 295.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((5, 5), SPC_RISK_LABELS.index("ENH"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((5, 5), 0.02),
            "hail": np.full((5, 5), 0.30),
            "wind": np.full((5, 5), 0.30),
        }
        lats = np.linspace(24.0, 25.0, 5)
        lons = np.linspace(-94.0, -90.0, 5)

        processed = postprocess_category_grid(category_grid, probabilities, features, lats, lons)

        self.assertTrue(np.all(processed.category_grid == SPC_RISK_LABELS.index("NONE")))
        self.assertGreater(processed.report["downgradedCells"]["gulfOfMexico"], 0)

    def test_postprocess_keeps_florida_gulf_offshore_cells_strict(self) -> None:
        fields = small_fields((5, 5))
        fields["cape_mu"] = np.full((5, 5), 2600.0)
        fields["cape_ml"] = np.full((5, 5), 1800.0)
        fields["u500"] = np.full((5, 5), 36.0)
        fields["srh01"] = np.full((5, 5), 140.0)
        fields["srh03"] = np.full((5, 5), 220.0)
        fields["td2m"] = np.full((5, 5), 295.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((5, 5), SPC_RISK_LABELS.index("ENH"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((5, 5), 0.02),
            "hail": np.full((5, 5), 0.30),
            "wind": np.full((5, 5), 0.30),
        }
        lats = np.linspace(24.2, 25.0, 5)
        lons = np.linspace(-85.0, -83.0, 5)

        processed = postprocess_category_grid(category_grid, probabilities, features, lats, lons)

        self.assertTrue(np.all(processed.category_grid == SPC_RISK_LABELS.index("NONE")))
        self.assertGreater(processed.report["downgradedCells"]["floridaGulf"], 0)

    def test_postprocess_keeps_atlantic_ocean_offshore_cells_strict(self) -> None:
        fields = small_fields((5, 5))
        fields["cape_mu"] = np.full((5, 5), 2600.0)
        fields["cape_ml"] = np.full((5, 5), 1800.0)
        fields["u500"] = np.full((5, 5), 36.0)
        fields["srh01"] = np.full((5, 5), 140.0)
        fields["srh03"] = np.full((5, 5), 220.0)
        fields["td2m"] = np.full((5, 5), 295.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((5, 5), SPC_RISK_LABELS.index("ENH"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((5, 5), 0.02),
            "hail": np.full((5, 5), 0.30),
            "wind": np.full((5, 5), 0.30),
        }
        lats = np.linspace(30.0, 32.0, 5)
        lons = np.linspace(-78.0, -76.0, 5)

        processed = postprocess_category_grid(category_grid, probabilities, features, lats, lons)

        self.assertTrue(np.all(processed.category_grid == SPC_RISK_LABELS.index("NONE")))
        self.assertGreater(processed.report["downgradedCells"]["atlanticOcean"], 0)

    def test_postprocess_keeps_south_texas_gulf_coast_strict(self) -> None:
        fields = small_fields((5, 5))
        fields["cape_mu"] = np.full((5, 5), 2600.0)
        fields["cape_ml"] = np.full((5, 5), 1800.0)
        fields["u500"] = np.full((5, 5), 36.0)
        fields["srh01"] = np.full((5, 5), 140.0)
        fields["srh03"] = np.full((5, 5), 220.0)
        fields["td2m"] = np.full((5, 5), 295.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((5, 5), SPC_RISK_LABELS.index("ENH"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((5, 5), 0.02),
            "hail": np.full((5, 5), 0.30),
            "wind": np.full((5, 5), 0.30),
        }
    def test_postprocess_does_not_cap_southeast_texas_land(self) -> None:
        fields = small_fields((5, 5))
        fields["cape_mu"] = np.full((5, 5), 2600.0)
        fields["cape_ml"] = np.full((5, 5), 1800.0)
        fields["u500"] = np.full((5, 5), 36.0)
        fields["srh01"] = np.full((5, 5), 140.0)
        fields["srh03"] = np.full((5, 5), 220.0)
        fields["td2m"] = np.full((5, 5), 295.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((5, 5), SPC_RISK_LABELS.index("ENH"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((5, 5), 0.02),
            "hail": np.full((5, 5), 0.30),
            "wind": np.full((5, 5), 0.30),
        }
        # Southeast Texas land coordinates (inland, north of the Gulf coastline)
        lats = np.linspace(29.1, 29.5, 5)
        lons = np.linspace(-95.5, -94.5, 5)

        processed = postprocess_category_grid(category_grid, probabilities, features, lats, lons)

        # Southeast Texas land should retain its ENH category and NOT be capped to NONE
        self.assertTrue(np.all(processed.category_grid == SPC_RISK_LABELS.index("ENH")))
        self.assertNotIn("southTexasGulfCoast", processed.report["downgradedCells"])

    def test_postprocess_caps_mexico_but_not_us_cities(self) -> None:
        fields = small_fields((5, 5))
        fields["cape_mu"] = np.full((5, 5), 3000.0)
        fields["cape_ml"] = np.full((5, 5), 2100.0)
        fields["u500"] = np.full((5, 5), 42.0)
        fields["srh01"] = np.full((5, 5), 210.0)
        fields["srh03"] = np.full((5, 5), 280.0)
        fields["td2m"] = np.full((5, 5), 295.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((5, 5), SPC_RISK_LABELS.index("SLGT"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((5, 5), 0.05),
            "hail": np.full((5, 5), 0.15),
            "wind": np.full((5, 5), 0.15),
        }

        # Test case A: Coordinates inside Mexico (south of US-Mexico border)
        lats_mex = np.linspace(24.0, 25.5, 5)
        lons_mex = np.linspace(-102.0, -100.0, 5)
        processed_mex = postprocess_category_grid(category_grid, probabilities, features, lats_mex, lons_mex)
        self.assertTrue(np.all(processed_mex.category_grid == SPC_RISK_LABELS.index("MRGL")))
        self.assertGreater(processed_mex.report["downgradedCells"]["texasMexicoBorder"], 0)

        # Test case B: US cities (e.g. San Antonio lon -98.5, lat 29.4 or Midland lon -102.1, lat 32.0)
        lats_us = np.linspace(29.4, 32.0, 5)
        lons_us = np.linspace(-102.1, -98.5, 5)
        processed_us = postprocess_category_grid(category_grid, probabilities, features, lats_us, lons_us)
        # Should retain SLGT category (uncapped)
        self.assertTrue(np.all(processed_us.category_grid == SPC_RISK_LABELS.index("SLGT")))

    def test_postprocess_adds_mrgl_buffer_around_slgt(self) -> None:
        fields = small_fields((11, 11))
        fields["cape_mu"] = np.full((11, 11), 2400.0)
        fields["cape_ml"] = np.full((11, 11), 1700.0)
        fields["u500"] = np.full((11, 11), 34.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((11, 11), SPC_RISK_LABELS.index("TSTM"), dtype=np.int16)
        category_grid[3:8, 3:8] = SPC_RISK_LABELS.index("SLGT")
        probabilities = {
            "tornado": np.full((11, 11), 0.01),
            "hail": np.full((11, 11), 0.14),
            "wind": np.full((11, 11), 0.14),
        }

        processed = postprocess_category_grid(category_grid, probabilities, features)

        self.assertTrue(np.any(processed.category_grid == SPC_RISK_LABELS.index("MRGL")))
        self.assertFalse(skipped_category_adjacencies(processed.category_grid))
        self.assertGreater(processed.report["hierarchyBuffers"]["addedCellsByCategory"]["MRGL"], 0)

    def test_postprocess_adds_all_intermediate_buffers_around_enh(self) -> None:
        fields = small_fields((13, 13))
        fields["cape_mu"] = np.full((13, 13), 2800.0)
        fields["cape_ml"] = np.full((13, 13), 2100.0)
        fields["u500"] = np.full((13, 13), 45.0)
        fields["srh01"] = np.full((13, 13), 220.0)
        fields["srh03"] = np.full((13, 13), 300.0)
        fields["td2m"] = np.full((13, 13), 296.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        category_grid = np.full((13, 13), SPC_RISK_LABELS.index("TSTM"), dtype=np.int16)
        category_grid[4:9, 4:9] = SPC_RISK_LABELS.index("ENH")
        probabilities = {
            "tornado": np.full((13, 13), 0.01),
            "hail": np.full((13, 13), 0.29),
            "wind": np.full((13, 13), 0.29),
        }

        processed = postprocess_category_grid(category_grid, probabilities, features)

        self.assertTrue(np.any(processed.category_grid == SPC_RISK_LABELS.index("SLGT")))
        self.assertTrue(np.any(processed.category_grid == SPC_RISK_LABELS.index("MRGL")))
        self.assertFalse(skipped_category_adjacencies(processed.category_grid))
        added = processed.report["hierarchyBuffers"]["addedCellsByCategory"]
        self.assertGreater(added["SLGT"], 0)
        self.assertGreater(added["MRGL"], 0)

    def test_offshore_probability_suppression_zeros_open_water_hazards(self) -> None:
        probabilities = {
            "tornado": np.full((3, 3), 0.10),
            "hail": np.full((3, 3), 0.30),
            "wind": np.full((3, 3), 0.30),
        }
        lats = np.linspace(26.0, 27.0, 3)
        lons = np.linspace(-94.0, -92.0, 3)

        capped = apply_offshore_probability_suppression(probabilities, lats, lons)

        self.assertTrue(np.all(capped.probabilities["tornado"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["hail"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["wind"] == 0.0))
        self.assertTrue(capped.report["offshoreProbabilitySuppressionApplied"])

    def test_offshore_probability_suppression_covers_bahamas_south_of_florida(self) -> None:
        probabilities = {
            "tornado": np.full((3, 3), 0.10),
            "hail": np.full((3, 3), 0.30),
            "wind": np.full((3, 3), 0.30),
        }
        lats = np.linspace(22.0, 24.0, 3)
        lons = np.linspace(-79.0, -75.0, 3)

        capped = apply_offshore_probability_suppression(probabilities, lats, lons)

        self.assertTrue(np.all(capped.probabilities["tornado"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["hail"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["wind"] == 0.0))
        self.assertGreater(capped.report["offshoreProbabilitySuppressedCells"]["atlanticOcean"], 0)

    def test_offshore_probability_suppression_covers_south_keys_edge_tiles(self) -> None:
        probabilities = {
            "tornado": np.full((3, 3), 0.10),
            "hail": np.full((3, 3), 0.30),
            "wind": np.full((3, 3), 0.30),
        }
        lats = np.linspace(22.8, 23.3, 3)
        lons = np.linspace(-80.5, -79.8, 3)

        capped = apply_offshore_probability_suppression(probabilities, lats, lons)

        self.assertTrue(np.all(capped.probabilities["tornado"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["hail"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["wind"] == 0.0))

        self.assertTrue(np.all(capped.probabilities["tornado"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["hail"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["wind"] == 0.0))

    def test_offshore_probability_suppression_does_not_cover_southeast_texas_land(self) -> None:
        probabilities = {
            "tornado": np.full((3, 3), 0.10),
            "hail": np.full((3, 3), 0.30),
            "wind": np.full((3, 3), 0.30),
        }
        # Southeast Texas land coordinates (inland, north of Gulf coast)
        lats = np.linspace(29.1, 29.5, 3)
        lons = np.linspace(-95.5, -94.5, 3)

        capped = apply_offshore_probability_suppression(probabilities, lats, lons)

        self.assertTrue(np.all(capped.probabilities["tornado"] == 0.10))
        self.assertTrue(np.all(capped.probabilities["hail"] == 0.30))
        self.assertTrue(np.all(capped.probabilities["wind"] == 0.30))
        self.assertNotIn("southTexasGulfCoast", capped.report.get("offshoreProbabilitySuppressedCells", {}))

    def test_philippines_offshore_suppression_keeps_land_and_coastal_buffer(self) -> None:
        probabilities = {
            "tornado": np.full((3, 3), 0.04),
            "hail": np.full((3, 3), 0.12),
            "wind": np.full((3, 3), 0.12),
        }

        luzon_lats = np.linspace(14.45, 14.75, 3)
        luzon_lons = np.linspace(120.85, 121.15, 3)
        land = apply_offshore_probability_suppression(probabilities, luzon_lats, luzon_lons)
        self.assertGreater(float(np.max(land.probabilities["wind"])), 0.0)
        self.assertIn("philippinesOffshore", land.report["offshoreProbabilitySuppressedCells"])

        open_water_lats = np.linspace(13.0, 13.5, 3)
        open_water_lons = np.linspace(127.5, 128.0, 3)
        water = apply_offshore_probability_suppression(probabilities, open_water_lats, open_water_lons)
        self.assertTrue(np.all(water.probabilities["tornado"] == 0.0))
        self.assertTrue(np.all(water.probabilities["hail"] == 0.0))
        self.assertTrue(np.all(water.probabilities["wind"] == 0.0))
        self.assertGreater(water.report["offshoreProbabilitySuppressedCells"]["philippinesOffshore"], 0)

    def test_philippines_maritime_environment_caps_high_end_severe_probabilities(self) -> None:
        fields = small_fields((3, 3))
        fields["cape"] = np.full((3, 3), 1800.0)
        fields["cape_ml"] = np.full((3, 3), 1400.0)
        fields["cape_mu"] = np.full((3, 3), 2000.0)
        fields["cin"] = np.full((3, 3), -25.0)
        fields["cin_ml"] = np.full((3, 3), -25.0)
        fields["td2m"] = np.full((3, 3), 297.0)
        fields["t2m"] = np.full((3, 3), 302.0)
        fields["pwat"] = np.full((3, 3), 55.0)
        fields["u10"] = np.zeros((3, 3))
        fields["v10"] = np.zeros((3, 3))
        fields["u500"] = np.full((3, 3), 6.0)
        fields["v500"] = np.zeros((3, 3))
        fields["srh01"] = np.full((3, 3), 35.0)
        fields["srh03"] = np.full((3, 3), 70.0)
        features = gridded_features_from_fields(fields, 12)
        probabilities = {
            "tornado": np.full((3, 3), 0.30),
            "hail": np.full((3, 3), 0.45),
            "wind": np.full((3, 3), 0.45),
        }

        capped = apply_environmental_probability_caps(
            probabilities,
            features,
            {"productionCapable": True, "datasetQuality": {"trainingRows": 9000, "minimumRecommendedRows": 5000}},
            lats=np.linspace(14.45, 14.75, 3),
            lons=np.linspace(120.85, 121.15, 3),
        )
        category = category_grid_from_probabilities(capped.probabilities, features, {"productionCapable": True})

        self.assertTrue(capped.report["philippinesRegionalCalibrationApplied"])
        self.assertLessEqual(float(np.max(capped.probabilities["tornado"])), 0.019)
        self.assertLessEqual(float(np.max(capped.probabilities["hail"])), 0.14)
        self.assertLessEqual(float(np.max(capped.probabilities["wind"])), 0.14)
        self.assertLessEqual(int(np.max(category)), SPC_RISK_LABELS.index("MRGL"))

    def test_philippines_organized_environment_can_buffer_wind_but_stays_capped(self) -> None:
        fields = small_fields((3, 3))
        fields["cape"] = np.full((3, 3), 1700.0)
        fields["cape_ml"] = np.full((3, 3), 1300.0)
        fields["cape_mu"] = np.full((3, 3), 2100.0)
        fields["cin"] = np.full((3, 3), -45.0)
        fields["cin_ml"] = np.full((3, 3), -45.0)
        fields["td2m"] = np.full((3, 3), 297.0)
        fields["t2m"] = np.full((3, 3), 302.0)
        fields["pwat"] = np.full((3, 3), 58.0)
        fields["u10"] = np.zeros((3, 3))
        fields["v10"] = np.zeros((3, 3))
        fields["u500"] = np.full((3, 3), 19.0)
        fields["v500"] = np.zeros((3, 3))
        fields["srh01"] = np.full((3, 3), 90.0)
        fields["srh03"] = np.full((3, 3), 125.0)
        features = gridded_features_from_fields(fields, 12)
        probabilities = {
            "tornado": np.full((3, 3), 0.08),
            "hail": np.full((3, 3), 0.20),
            "wind": np.full((3, 3), 0.20),
        }

        capped = apply_environmental_probability_caps(
            probabilities,
            features,
            {"productionCapable": True, "datasetQuality": {"trainingRows": 9000, "minimumRecommendedRows": 5000}},
            lats=np.linspace(14.45, 14.75, 3),
            lons=np.linspace(120.85, 121.15, 3),
        )

        self.assertEqual(capped.report["philippinesGustBufferCells"], 0)
        self.assertLessEqual(float(np.max(capped.probabilities["wind"])), 0.164)
        self.assertLessEqual(float(np.max(capped.probabilities["hail"])), 0.29)

    def test_probability_tile_suppresses_strict_marine_zone_by_tile_center(self) -> None:
        category = np.full((3, 3), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((3, 3), 0.04),
            "hail": np.full((3, 3), 0.12),
            "wind": np.full((3, 3), 0.12),
        }

        # Case A: Strict marine zone (open Gulf of Mexico)
        lats_marine = np.linspace(26.0, 27.0, 3)
        lons_marine = np.linspace(-94.0, -92.0, 3)
        tile_marine = probability_tile(lats_marine, lons_marine, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=3)
        self.assertEqual(tile_marine["categoryLabel"], [["NONE"]])
        self.assertEqual(tile_marine["probabilities"]["tornado"], [[0.0]])
        self.assertEqual(tile_marine["probabilities"]["hail"], [[0.0]])
        self.assertEqual(tile_marine["probabilities"]["wind"], [[0.0]])

        # Case B: Southeast Texas land (should not be suppressed)
        lats_land = np.linspace(29.1, 29.5, 3)
        lons_land = np.linspace(-95.5, -94.5, 3)
        tile_land = probability_tile(lats_land, lons_land, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=3)
        self.assertEqual(tile_land["categoryLabel"], [["MRGL"]])
        self.assertEqual(tile_land["probabilities"]["tornado"], [[0.04]])
        self.assertEqual(tile_land["probabilities"]["hail"], [[0.12]])
        self.assertEqual(tile_land["probabilities"]["wind"], [[0.12]])

    def test_probability_tile_caps_mexico_but_not_us(self) -> None:
        category = np.full((3, 3), SPC_RISK_LABELS.index("SLGT"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((3, 3), 0.05),
            "hail": np.full((3, 3), 0.15),
            "wind": np.full((3, 3), 0.15),
        }

        # Case A: Mexico border corridor capped to MRGL
        lats_mex = np.linspace(24.0, 25.5, 3)
        lons_mex = np.linspace(-102.0, -100.0, 3)
        tile_mex = probability_tile(lats_mex, lons_mex, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=3)
        self.assertEqual(tile_mex["categoryLabel"], [["MRGL"]])
        self.assertEqual(tile_mex["probabilities"]["tornado"], [[0.049]])
        self.assertEqual(tile_mex["probabilities"]["hail"], [[0.149]])
        self.assertEqual(tile_mex["probabilities"]["wind"], [[0.149]])

        # Case B: US region uncapped
        lats_us = np.linspace(30.8, 32.1, 3)
        lons_us = np.linspace(-99.0, -97.0, 3)
        tile_us = probability_tile(lats_us, lons_us, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=3)
        self.assertEqual(tile_us["categoryLabel"], [["SLGT"]])
        self.assertEqual(tile_us["probabilities"]["tornado"], [[0.05]])
        self.assertEqual(tile_us["probabilities"]["hail"], [[0.15]])
        self.assertEqual(tile_us["probabilities"]["wind"], [[0.15]])

    def test_risk_polygons_are_geojson_features(self) -> None:
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        category = np.ones((5, 5), dtype=int)
        category[1:4, 1:4] = SPC_RISK_LABELS.index("MRGL")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=3)

        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertTrue(any(feature["properties"]["category"] == "MRGL" for feature in geojson["features"]))
        labels = [feature["properties"]["category"] for feature in geojson["features"]]
        self.assertEqual(labels, ["TSTM", "MRGL"])
        self.assertLessEqual(len(geojson["features"]), 2)
        for feature in geojson["features"]:
            self.assertIn(feature["geometry"]["type"], {"Polygon", "MultiPolygon"})
            self.assertIn("sourceCellCount", feature["properties"])
            rings = (
                [feature["geometry"]["coordinates"][0]]
                if feature["geometry"]["type"] == "Polygon"
                else [polygon[0] for polygon in feature["geometry"]["coordinates"]]
            )
            for ring in rings:
                area = 0.0
                for idx, (x0, y0) in enumerate(ring[:-1]):
                    x1, y1 = ring[idx + 1]
                    area += x0 * y1 - x1 * y0
                self.assertLessEqual(area / 2.0, 0.0)

    def test_risk_polygon_features_follow_category_layer_order(self) -> None:
        lats = np.linspace(30.0, 38.0, 9)
        lons = np.linspace(-104.0, -96.0, 9)
        category = np.full((9, 9), SPC_RISK_LABELS.index("TSTM"), dtype=np.int16)
        category[1:8, 1:8] = SPC_RISK_LABELS.index("MRGL")
        category[2:7, 2:7] = SPC_RISK_LABELS.index("SLGT")
        category[3:6, 3:6] = SPC_RISK_LABELS.index("ENH")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=1)

        labels = [feature["properties"]["category"] for feature in geojson["features"]]
        self.assertEqual(labels, ["TSTM", "MRGL", "SLGT", "ENH"])
        self.assertLess(len(geojson["features"]), int(np.sum(category > 0)))
        for feature in geojson["features"]:
            self.assertIn(feature["geometry"]["type"], {"Polygon", "MultiPolygon"})
            self.assertTrue(feature["properties"]["vectorization"]["cumulativeMask"])

    def test_risk_polygon_generalization_merges_neighbors_and_removes_noise(self) -> None:
        lats = np.linspace(25.0, 45.0, 40)
        lons = np.linspace(-110.0, -80.0, 40)
        category = np.zeros((40, 40), dtype=np.int16)
        category[8:24, 8:18] = SPC_RISK_LABELS.index("TSTM")
        category[8:24, 21:31] = SPC_RISK_LABELS.index("TSTM")
        category[14:16, 14:16] = SPC_RISK_LABELS.index("NONE")
        category[2:4, 2:4] = SPC_RISK_LABELS.index("TSTM")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        tstm = next(feature for feature in geojson["features"] if feature["properties"]["category"] == "TSTM")

        self.assertEqual(tstm["properties"]["componentCount"], 1)
        self.assertGreater(tstm["properties"]["cellCount"], tstm["properties"]["sourceCellCount"])
        self.assertTrue(tstm["properties"]["vectorization"]["cartographicGeneralization"])
        self.assertGreaterEqual(tstm["properties"]["vectorization"]["closeIterations"], 3)

    def test_risk_polygon_generalization_prunes_thin_tendrils(self) -> None:
        lats = np.linspace(25.0, 45.0, 60)
        lons = np.linspace(-110.0, -80.0, 60)
        category = np.zeros((60, 60), dtype=np.int16)
        category[20:36, 10:25] = SPC_RISK_LABELS.index("TSTM")
        category[20:36, 31:42] = SPC_RISK_LABELS.index("TSTM")
        category[27, 42:57] = SPC_RISK_LABELS.index("TSTM")
        category[4:7, 4:7] = SPC_RISK_LABELS.index("TSTM")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        tstm = next(feature for feature in geojson["features"] if feature["properties"]["category"] == "TSTM")
        geometry = tstm["geometry"]
        rings = (
            [geometry["coordinates"][0]]
            if geometry["type"] == "Polygon"
            else [polygon[0] for polygon in geometry["coordinates"]]
        )
        max_lon = max(point[0] for ring in rings for point in ring)

        self.assertEqual(tstm["properties"]["componentCount"], 1)
        self.assertLess(max_lon, -85.0)
        self.assertGreaterEqual(tstm["properties"]["vectorization"]["tendrilPruneIterations"], 2)

    def test_risk_polygon_higher_risk_occupies_display_gap(self) -> None:
        lats = np.linspace(28.0, 40.0, 100)
        lons = np.linspace(-104.0, -86.0, 120)
        category = np.zeros((100, 120), dtype=np.int16)
        category[12:88, 10:110] = SPC_RISK_LABELS.index("TSTM")
        category[26:74, 28:92] = SPC_RISK_LABELS.index("MRGL")
        category[40:60, 45:75] = SPC_RISK_LABELS.index("SLGT")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        self.assertEqual([feature["properties"]["category"] for feature in geojson["features"]], ["TSTM", "MRGL", "SLGT"])
        features = {feature["properties"]["category"]: feature for feature in geojson["features"]}
        tstm = projected_geojson_geometry(features["TSTM"]["geometry"])
        mrgl = projected_geojson_geometry(features["MRGL"]["geometry"])
        slgt = projected_geojson_geometry(features["SLGT"]["geometry"])

        self.assertLess(tstm.distance(mrgl), 1_000.0)
        self.assertLess(mrgl.distance(slgt), 1_000.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(features["MRGL"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(features["SLGT"]["properties"]["vectorization"]["displayBandGapKm"], 0.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 0.0)
        self.assertEqual(features["MRGL"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 10.0)
        self.assertEqual(features["SLGT"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 10.0)
        for category_name in ["TSTM", "MRGL", "SLGT"]:
            vectorization = features[category_name]["properties"]["vectorization"]
            self.assertEqual(vectorization["displayGeometry"], "band_with_higher_owned_gap")
            self.assertEqual(vectorization["targetDisplayBandGapKm"], 10.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["targetDisplayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(features["MRGL"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(features["SLGT"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(features["MRGL"]["properties"]["vectorization"]["targetDisplayLowerOwnedBoundaryKm"], 5.0)
        self.assertEqual(features["SLGT"]["properties"]["vectorization"]["targetDisplayLowerOwnedBoundaryKm"], 5.0)

    def test_risk_polygon_higher_owned_gap_preserves_display_area_safeguards(self) -> None:
        lats = np.linspace(28.0, 40.0, 100)
        lons = np.linspace(-104.0, -86.0, 120)
        category = np.zeros((100, 120), dtype=np.int16)
        category[28:72, 34:86] = SPC_RISK_LABELS.index("ENH")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        self.assertEqual([feature["properties"]["category"] for feature in geojson["features"]], ["TSTM", "MRGL", "SLGT", "ENH"])
        features = {feature["properties"]["category"]: feature for feature in geojson["features"]}
        display_areas = [features[category_name]["properties"]["displayAreaKm2"] for category_name in ["TSTM", "MRGL", "SLGT", "ENH"]]

        self.assertTrue(all(area > 0 for area in display_areas))
        self.assertLess(max(display_areas) / min(display_areas), 5.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(features["MRGL"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(features["SLGT"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(features["ENH"]["properties"]["vectorization"]["displayBandGapKm"], 0.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 0.0)
        for category_name in ["MRGL", "SLGT", "ENH"]:
            self.assertEqual(features[category_name]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 10.0)
        for category_name in ["TSTM", "MRGL", "SLGT"]:
            self.assertGreater(features[category_name]["properties"]["vectorization"]["displayMinimumSupportKm"], 0.0)
        tstm_vectorization = features["TSTM"]["properties"]["vectorization"]
        self.assertEqual(tstm_vectorization["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(tstm_vectorization["targetDisplayLowerOwnedBoundaryKm"], 0.0)
        for category_name in ["MRGL", "SLGT", "ENH"]:
            vectorization = features[category_name]["properties"]["vectorization"]
            self.assertEqual(vectorization["displayLowerOwnedBoundaryKm"], 0.0)
            self.assertEqual(vectorization["targetDisplayLowerOwnedBoundaryKm"], 5.0)

    def test_risk_polygon_display_smoothing_removes_boxy_rectangles(self) -> None:
        lats = np.linspace(28.0, 40.0, 80)
        lons = np.linspace(-104.0, -86.0, 100)
        category = np.zeros((80, 100), dtype=np.int16)
        category[18:58, 20:76] = SPC_RISK_LABELS.index("TSTM")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        tstm = next(feature for feature in geojson["features"] if feature["properties"]["category"] == "TSTM")
        geometry = tstm["geometry"]
        rings = (
            [geometry["coordinates"][0]]
            if geometry["type"] == "Polygon"
            else [polygon[0] for polygon in geometry["coordinates"]]
        )
        largest_ring = max(rings, key=len)
        unique_lons = {round(point[0], 3) for point in largest_ring}
        unique_lats = {round(point[1], 3) for point in largest_ring}

        self.assertGreater(len(largest_ring), 8)
        self.assertGreater(len(unique_lons), 4)
        self.assertGreater(len(unique_lats), 4)
        self.assertEqual(tstm["properties"]["vectorization"]["displayGeometry"], "smoothed_cumulative_band")

    def test_broad_tstm_contour_does_not_fallback_to_bbox(self) -> None:
        lats = np.linspace(23.0, 46.0, 120)
        lons = np.linspace(-105.0, -74.0, 160)
        category = np.zeros((120, 160), dtype=np.int16)
        category[16:50, 12:92] = SPC_RISK_LABELS.index("TSTM")
        category[44:78, 60:126] = SPC_RISK_LABELS.index("TSTM")
        category[70:104, 102:150] = SPC_RISK_LABELS.index("TSTM")
        category[28:44, 70:92] = SPC_RISK_LABELS.index("NONE")
        category[60:72, 92:112] = SPC_RISK_LABELS.index("NONE")

        geojson = risk_polygons_from_grid(lats, lons, category, 33, "2026-05-09T15:00:00Z", min_cells=10)
        tstm = next(feature for feature in geojson["features"] if feature["properties"]["category"] == "TSTM")
        geometry = tstm["geometry"]
        rings = (
            [geometry["coordinates"][0]]
            if geometry["type"] == "Polygon"
            else [polygon[0] for polygon in geometry["coordinates"]]
        )
        largest_ring = max(rings, key=lambda ring: abs(polygon_area(ring)))
        lons_out = [point[0] for point in largest_ring]
        lats_out = [point[1] for point in largest_ring]
        bbox_area = (max(lons_out) - min(lons_out)) * (max(lats_out) - min(lats_out))
        fill_fraction = abs(polygon_area(largest_ring)) / bbox_area

        self.assertGreater(len(largest_ring), 12)
        self.assertLess(fill_fraction, 0.85)
        self.assertEqual(tstm["properties"]["vectorization"]["method"], "marching_squares_cumulative_contours")

    def test_risk_polygon_contours_are_safe_under_parallel_hours(self) -> None:
        lats = np.linspace(25.0, 50.0, 80)
        lons = np.linspace(-124.0, -67.0, 120)
        category = np.zeros((80, 120), dtype=int)
        category[10:45, 20:85] = SPC_RISK_LABELS.index("TSTM")
        category[20:40, 35:75] = SPC_RISK_LABELS.index("MRGL")
        category[28:36, 50:65] = SPC_RISK_LABELS.index("SLGT")

        def build(hour: int) -> dict[str, object]:
            return risk_polygons_from_grid(lats, lons, category, hour, f"2024-05-04T{hour:02d}:00:00Z", min_cells=3)

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(build, range(8)))

        self.assertEqual([result["type"] for result in results], ["FeatureCollection"] * 8)
        self.assertTrue(all(result["features"] for result in results))

    def test_probability_tile_downsampling_preserves_block_max_categories_and_probabilities(self) -> None:
        lats = np.linspace(34.0, 37.0, 4)
        lons = np.linspace(-100.0, -97.0, 4)
        category = np.zeros((4, 4), dtype=np.int16)
        category[1, 1] = SPC_RISK_LABELS.index("SLGT")
        probabilities = {
            "tornado": np.zeros((4, 4)),
            "hail": np.zeros((4, 4)),
            "wind": np.zeros((4, 4)),
        }
        probabilities["hail"][1, 1] = 0.29

        tile = probability_tile(lats, lons, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=4)

        self.assertEqual(tile["shape"], [1, 1])
        self.assertEqual(tile["categoryLabel"], [["SLGT"]])
        self.assertEqual(tile["probabilities"]["hail"], [[0.29]])

    def test_hazard_probability_shapes_use_vector_probability_contours(self) -> None:
        lats = np.linspace(30.0, 39.0, 10)
        lons = np.linspace(-104.0, -95.0, 10)
        category = np.full((10, 10), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        category[2:8, 2:8] = SPC_RISK_LABELS.index("SLGT")
        category[4:6, 4:6] = SPC_RISK_LABELS.index("ENH")
        probabilities = {
            "tornado": np.zeros((10, 10)),
            "hail": np.full((10, 10), 0.05),
            "wind": np.zeros((10, 10)),
        }
        probabilities["hail"][2:8, 2:8] = 0.15
        probabilities["hail"][4:6, 4:6] = 0.30

        shapes = hazard_probability_shapes_from_grids(
            lats,
            lons,
            probabilities,
            category,
            3,
            "2024-05-04T15:00:00Z",
            min_cells=1,
        )

        hail = [feature for feature in shapes["features"] if feature["properties"]["hazard"] == "hail"]
        self.assertEqual([feature["properties"]["label"] for feature in hail[:3]], ["5%", "15%", "30%"])
        self.assertLess(len(hail), int(np.sum(probabilities["hail"] >= 0.05)))
        for feature in hail:
            self.assertIn(feature["geometry"]["type"], {"Polygon", "MultiPolygon"})
            self.assertEqual(feature["properties"]["forecastHour"], 3)

    def test_hazard_probability_shapes_are_clipped_to_categorical_support(self) -> None:
        def square(x0: float, y0: float, x1: float, y1: float) -> dict:
            return {
                "type": "Polygon",
                "coordinates": [[
                    [x0, y0],
                    [x1, y0],
                    [x1, y1],
                    [x0, y1],
                    [x0, y0],
                ]],
            }

        risk_shapes = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": square(-105.0, 30.0, -95.0, 40.0),
                    "properties": {"category": "TSTM", "ordinal": 1},
                },
                {
                    "type": "Feature",
                    "geometry": square(-103.0, 32.0, -97.0, 38.0),
                    "properties": {"category": "MRGL", "ordinal": 2},
                },
            ],
        }
        hazard_shapes = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": square(-106.0, 29.0, -94.0, 41.0),
                    "properties": {"hazard": "thunder", "probability": 0.10},
                },
                {
                    "type": "Feature",
                    "geometry": square(-104.0, 31.0, -96.0, 39.0),
                    "properties": {"hazard": "hail", "probability": 0.05},
                },
                {
                    "type": "Feature",
                    "geometry": square(-104.0, 31.0, -96.0, 39.0),
                    "properties": {"hazard": "wind", "probability": 0.05},
                },
                {
                    "type": "Feature",
                    "geometry": square(-104.0, 31.0, -96.0, 39.0),
                    "properties": {"hazard": "hail", "probability": 0.15},
                },
            ],
        }

        constrained = constrain_hazard_probability_shapes_to_risk_support(
            hazard_shapes,
            risk_shapes,
        )
        from shapely.geometry import shape

        self.assertTrue(all(shape(feature["geometry"]).is_valid for feature in constrained["features"]))
        by_hazard_probability = {
            (
                feature["properties"]["hazard"],
                feature["properties"]["probability"],
            ): projected_geojson_geometry(feature["geometry"])
            for feature in constrained["features"]
        }
        tstm = projected_geojson_geometry(risk_shapes["features"][0]["geometry"])
        mrgl = projected_geojson_geometry(risk_shapes["features"][1]["geometry"])

        self.assertLess(by_hazard_probability[("thunder", 0.10)].difference(tstm).area, 1.0)
        self.assertLess(by_hazard_probability[("hail", 0.05)].difference(tstm).area, 1.0)
        self.assertLess(by_hazard_probability[("wind", 0.05)].difference(tstm).area, 1.0)
        self.assertGreater(by_hazard_probability[("hail", 0.05)].difference(mrgl).area, 1.0)
        self.assertGreater(by_hazard_probability[("wind", 0.05)].difference(mrgl).area, 1.0)
        self.assertLess(by_hazard_probability[("hail", 0.15)].difference(mrgl).area, 1.0)
        self.assertEqual(
            next(
                feature["properties"]["vectorization"]["minimumSupportCategory"]
                for feature in constrained["features"]
                if (
                    feature["properties"]["hazard"] == "hail"
                    and feature["properties"]["probability"] == 0.05
                )
            ),
            "TSTM",
        )
        self.assertEqual(
            next(
                feature["properties"]["vectorization"]["minimumSupportCategory"]
                for feature in constrained["features"]
                if (
                    feature["properties"]["hazard"] == "hail"
                    and feature["properties"]["probability"] == 0.15
                )
            ),
            "MRGL",
        )

    def test_hazard_probability_shapes_add_lower_probability_support(self) -> None:
        lats = np.linspace(30.0, 38.0, 9)
        lons = np.linspace(-104.0, -96.0, 9)
        category = np.full((9, 9), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        category[2:7, 2:7] = SPC_RISK_LABELS.index("SLGT")
        probabilities = {
            "tornado": np.zeros((9, 9)),
            "hail": np.zeros((9, 9)),
            "wind": np.zeros((9, 9)),
        }
        probabilities["hail"][4, 4] = 0.30

        shapes = hazard_probability_shapes_from_grids(
            lats,
            lons,
            probabilities,
            category,
            0,
            "2024-05-04T12:00:00Z",
            min_cells=1,
        )

        hail = {feature["properties"]["label"]: feature["properties"] for feature in shapes["features"] if feature["properties"]["hazard"] == "hail"}
        self.assertGreater(hail["15%"]["cellCount"], hail["15%"]["sourceCellCount"])
        self.assertGreater(hail["5%"]["cellCount"], hail["5%"]["sourceCellCount"])
        self.assertTrue(hail["15%"]["vectorization"]["hierarchyBuffersApplied"])
        self.assertEqual(hail["15%"]["vectorization"]["supportSource"], "hazard_probability")

    def test_hazard_probability_shapes_do_not_use_category_as_severe_support(self) -> None:
        lats = np.linspace(30.0, 40.0, 40)
        lons = np.linspace(-105.0, -95.0, 40)
        category = np.full((40, 40), SPC_RISK_LABELS.index("SLGT"), dtype=np.int16)
        probabilities = {
            "tornado": np.zeros((40, 40)),
            "hail": np.zeros((40, 40)),
            "wind": np.zeros((40, 40)),
        }
        probabilities["hail"][18:22, 18:22] = 0.30

        shapes = hazard_probability_shapes_from_grids(
            lats,
            lons,
            probabilities,
            category,
            0,
            "2024-05-04T12:00:00Z",
            min_cells=1,
        )

        hail = {
            feature["properties"]["label"]: feature["properties"]
            for feature in shapes["features"]
            if feature["properties"]["hazard"] == "hail"
        }
        thunder = [
            feature["properties"]
            for feature in shapes["features"]
            if feature["properties"]["hazard"] == "thunder"
        ]

        self.assertLess(hail["5%"]["cellCount"], int(category.size * 0.10))
        self.assertLess(hail["15%"]["cellCount"], int(category.size * 0.10))
        self.assertEqual(hail["5%"]["vectorization"]["supportSource"], "hazard_probability")
        self.assertTrue(all(item["vectorization"]["supportSource"] == "category_thunder" for item in thunder))

    def test_hazard_probability_shapes_generalize_nearby_contours(self) -> None:
        lats = np.linspace(25.0, 45.0, 40)
        lons = np.linspace(-110.0, -80.0, 40)
        category = np.full((40, 40), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        probabilities = {
            "tornado": np.zeros((40, 40)),
            "hail": np.zeros((40, 40)),
            "wind": np.zeros((40, 40)),
        }
        probabilities["hail"][8:24, 8:18] = 0.05
        probabilities["hail"][8:24, 21:31] = 0.05
        probabilities["hail"][14:16, 14:16] = 0.0
        probabilities["hail"][2:4, 2:4] = 0.05

        shapes = hazard_probability_shapes_from_grids(
            lats,
            lons,
            probabilities,
            category,
            0,
            "2024-05-04T12:00:00Z",
            min_cells=10,
        )
        hail_5 = next(feature for feature in shapes["features"] if feature["properties"]["hazard"] == "hail" and feature["properties"]["label"] == "5%")

        self.assertEqual(hail_5["properties"]["componentCount"], 1)
        self.assertGreater(hail_5["properties"]["cellCount"], hail_5["properties"]["sourceCellCount"])
        self.assertTrue(hail_5["properties"]["vectorization"]["cartographicGeneralization"])

    def test_hazard_probability_higher_probability_occupies_display_gap(self) -> None:
        lats = np.linspace(28.0, 40.0, 100)
        lons = np.linspace(-104.0, -86.0, 120)
        category = np.full((100, 120), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        probabilities = {
            "tornado": np.zeros((100, 120)),
            "hail": np.zeros((100, 120)),
            "wind": np.zeros((100, 120)),
        }
        probabilities["hail"][12:88, 10:110] = 0.05
        probabilities["hail"][26:74, 28:92] = 0.15
        probabilities["hail"][40:60, 45:75] = 0.30

        shapes = hazard_probability_shapes_from_grids(
            lats,
            lons,
            probabilities,
            category,
            0,
            "2024-05-04T12:00:00Z",
            min_cells=10,
        )
        hail = {feature["properties"]["label"]: feature for feature in shapes["features"] if feature["properties"]["hazard"] == "hail"}
        hail_5 = projected_geojson_geometry(hail["5%"]["geometry"])
        hail_15 = projected_geojson_geometry(hail["15%"]["geometry"])
        hail_30 = projected_geojson_geometry(hail["30%"]["geometry"])

        self.assertLess(hail_5.distance(hail_15), 1_000.0)
        self.assertLess(hail_15.distance(hail_30), 1_000.0)
        for label in ["5%", "15%", "30%"]:
            self.assertEqual(hail[label]["properties"]["vectorization"]["displayGeometry"], "band_with_higher_owned_gap")
            self.assertEqual(hail[label]["properties"]["vectorization"]["targetDisplayBandGapKm"], 10.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(hail["15%"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertEqual(hail["30%"]["properties"]["vectorization"]["displayBandGapKm"], 0.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 0.0)
        self.assertEqual(hail["15%"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 10.0)
        self.assertEqual(hail["30%"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 10.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["targetDisplayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(hail["15%"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(hail["30%"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(hail["15%"]["properties"]["vectorization"]["targetDisplayLowerOwnedBoundaryKm"], 5.0)
        self.assertEqual(hail["30%"]["properties"]["vectorization"]["targetDisplayLowerOwnedBoundaryKm"], 5.0)

    def test_hazard_probability_higher_owned_gap_preserves_smoothed_contours(self) -> None:
        lats = np.linspace(28.0, 40.0, 100)
        lons = np.linspace(-104.0, -86.0, 120)
        category = np.full((100, 120), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        probabilities = {
            "tornado": np.zeros((100, 120)),
            "hail": np.zeros((100, 120)),
            "wind": np.zeros((100, 120)),
        }
        probabilities["hail"][30:70, 36:84] = 0.15

        shapes = hazard_probability_shapes_from_grids(
            lats,
            lons,
            probabilities,
            category,
            0,
            "2024-05-04T12:00:00Z",
            min_cells=10,
        )
        hail = {feature["properties"]["label"]: feature for feature in shapes["features"] if feature["properties"]["hazard"] == "hail"}
        hail_5 = projected_geojson_geometry(hail["5%"]["geometry"])
        hail_15 = projected_geojson_geometry(hail["15%"]["geometry"])

        self.assertLess(hail_5.distance(hail_15), 1_000.0)
        self.assertGreater(hail_5.area, 0.0)
        self.assertGreater(hail_15.area, 0.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayBandGapKm"], 10.0)
        self.assertGreater(hail["5%"]["properties"]["vectorization"]["displayMinimumSupportKm"], 0.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)
        self.assertEqual(hail["15%"]["properties"]["vectorization"]["displayHigherRiskExpansionKm"], 10.0)
        self.assertEqual(hail["15%"]["properties"]["vectorization"]["displayLowerOwnedBoundaryKm"], 0.0)

    def test_frontend_risk_fill_layers_are_stroke_free_except_overlay_compare(self) -> None:
        root = Path(__file__).resolve().parents[2]
        spc_source = (root / "src" / "components" / "SpcLevelOutlookMap.tsx").read_text(encoding="utf-8")
        generated_source = (root / "src" / "components" / "GeneratedOutlookMap.tsx").read_text(encoding="utf-8")

        spc_fill = spc_source[spc_source.index("key={`spc-level-${"):spc_source.index("key={`state-outline-")]
        generated_fill = generated_source[generated_source.index("key={`generated-risk-${"):generated_source.index("key={`generated-state-outline-")]

        self.assertIn("stroke: 'none'", spc_fill)
        self.assertIn("strokeWidth: 0", spc_fill)
        self.assertIn("stroke: isOverlayMode ? style.stroke : 'none'", generated_fill)
        self.assertIn("strokeWidth: isOverlayMode ? 1.65 : 0", generated_fill)

        for fill_block in (spc_fill, generated_fill):
            self.assertNotIn("strokeWidth: 2.2", fill_block)

        # Risk bands render as solid fills with no boundary strokes in the
        # default outlook view. Overlay compare mode may render thin contour
        # strokes so AutoOutlook and SPC boundaries can be visually compared.
        self.assertNotIn("hasMetricDisplayGaps", generated_source)
        self.assertNotIn("riskGapFillCollection", generated_source)
        self.assertNotIn("generated-risk-gap-fill-", generated_source)
        self.assertNotIn("VECTOR_GAP_FILL_STROKE_WIDTH", generated_source)
        self.assertNotIn("VECTOR_GAP_FILL_STROKE_OPACITY", generated_source)
        self.assertNotIn("RISK_BOUNDARY_STROKE_WIDTH", spc_source)
        self.assertNotIn("RISK_BOUNDARY_STROKE_OPACITY", spc_source)
        self.assertNotIn("RISK_BAND_BOUNDARY_STROKE_WIDTH", generated_source)
        self.assertNotIn("RISK_BAND_BOUNDARY_STROKE_OPACITY", generated_source)
        self.assertNotIn("RISK_SEPARATOR_STROKE_WIDTH", spc_source)
        self.assertNotIn("RISK_BAND_SEPARATOR_STROKE_WIDTH", generated_source)
        self.assertNotIn("spc-level-boundary-", spc_source)
        self.assertNotIn("spc-level-separator-", spc_source)
        self.assertNotIn("generated-risk-outline-", generated_source)
        self.assertNotIn("generated-risk-separator-", generated_source)

    def test_frontend_hazard_probability_fills_do_not_use_black_outlines(self) -> None:
        root = Path(__file__).resolve().parents[2]
        hazard_source = (root / "src" / "components" / "GeneratedHazardProbabilityMap.tsx").read_text(encoding="utf-8")
        artifact_probability_source = (root / "src" / "utils" / "artifactProbabilities.ts").read_text(encoding="utf-8")
        probability_fill = hazard_source[hazard_source.index("key={`artifact-prob-${"):hazard_source.index("key={`artifact-sig-")]

        self.assertIn("stroke: 'none'", probability_fill)
        self.assertIn("strokeWidth: 0", probability_fill)
        self.assertIn("strokeOpacity: 0", probability_fill)
        self.assertNotIn("stroke: '#111111'", probability_fill)
        # Hazard probability bands render as solid fills with no boundary
        # strokes. All separator and boundary outline layers removed so
        # bands merge seamlessly through color contrast alone.
        self.assertNotIn("hasMetricDisplayGaps", hazard_source)
        self.assertNotIn("probabilityGapFillCollection", hazard_source)
        self.assertNotIn("artifact-prob-gap-fill-", hazard_source)
        self.assertNotIn("VECTOR_GAP_FILL_STROKE_WIDTH", hazard_source)
        self.assertNotIn("VECTOR_GAP_FILL_STROKE_OPACITY", hazard_source)
        self.assertNotIn("vectorization: feature.properties.vectorization", artifact_probability_source)
        self.assertNotIn("HAZARD_BOUNDARY_STROKE_WIDTH", hazard_source)
        self.assertNotIn("HAZARD_BOUNDARY_STROKE_OPACITY", hazard_source)
        self.assertNotIn("HAZARD_SEPARATOR_STROKE_WIDTH", hazard_source)
        self.assertNotIn("HAZARD_SEPARATOR_STROKE_OPACITY", hazard_source)
        self.assertNotIn("darkenHexColor", hazard_source)
        self.assertNotIn("artifact-prob-boundary-", hazard_source)
        self.assertNotIn("artifact-prob-separator-", hazard_source)

    def test_spc_verification_reports_over_and_underforecast_cells(self) -> None:
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        pred = np.ones((5, 5), dtype=int)
        pred[2:, 2:] = SPC_RISK_LABELS.index("SLGT")
        official = official_category_grid(*np.meshgrid(lats, lons, indexing="ij"), fake_spc_geojson())

        summary = compare_prediction_to_spc(lats, lons, pred, fake_spc_geojson(), {"mucape": np.full((5, 5), 1500.0)})

        self.assertGreater(int(np.sum(official)), 0)
        self.assertGreater(summary["overforecastCells"], 0)
        self.assertGreater(summary["underforecastCells"], 0)
        self.assertIn("leakageGuard", summary)

    def test_pipeline_writes_prediction_artifacts_before_spc_fetch(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        order: dict[str, bool] = {}

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[1:4, 1:4] = 0.20
            probs[2, 2] = 0.35
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        def fake_spc(_session, output_dir):
            order["risk_written_before_spc"] = bool(output_dir and (output_dir / "risk_polygons.geojson").exists())
            return {
                "day1Url": "https://www.spc.noaa.gov/products/outlook/day1otlk.html",
                "geojsonZipUrl": "https://www.spc.noaa.gov/products/outlook/archive/test.zip",
                "fetchedAtISO": "2024-05-04T13:00:00Z",
                "categoryGeojson": fake_spc_geojson(),
            }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            metadata = run_pipeline(
                output_dir=Path(tmp) / "latest",
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                verify_spc=True,
                preview=False,
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
                spc_fetch_fn=fake_spc,
            )
            latest = Path(tmp) / "latest"

            self.assertTrue(order["risk_written_before_spc"])
            self.assertTrue((latest / "metadata.json").exists())
            self.assertTrue((latest / "risk_polygons.geojson").exists())
            self.assertTrue((latest / "probability_tiles.json").exists())
            self.assertEqual(metadata["cycle"], "HRRR 12Z 20240504")
            self.assertTrue(metadata["spcVerification"]["spcFetchedAfterPredictionArtifacts"])

    def test_pipeline_reports_failed_hours_but_publishes_when_minimum_is_met(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            if ref.forecast_hour == 1:
                raise RuntimeError("missing hour")
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            metadata = run_pipeline(
                output_dir=Path(tmp) / "latest",
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                verify_spc=False,
                preview=False,
                min_successful_hours=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            self.assertEqual(metadata["successfulForecastHours"], [0])
            self.assertEqual(metadata["failedHours"][0]["forecastHour"], 1)
            self.assertTrue((Path(tmp) / "latest" / "metadata.json").exists())

    def test_incremental_pipeline_publishes_ready_hours_without_waiting_for_failures(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        fields = small_fields()
        fields["cape"][2, 2] = 2450.0
        fields["cape_ml"][2, 2] = 1980.0
        fields["cape_mu"][2, 2] = 2880.0
        fields["cin_ml"][2, 2] = -88.0
        fields["td2m"][2, 2] = 296.15
        fields["t2m"][2, 2] = 304.15
        fields["pwat"][2, 2] = 41.91
        fields["u10"][2, 2] = 4.0
        fields["v10"][2, 2] = 2.0
        fields["u500"][2, 2] = 24.0
        fields["v500"][2, 2] = 18.0
        fields["srh01"][2, 2] = 142.0
        fields["srh03"][2, 2] = 246.0

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            if ref.forecast_hour == 1:
                raise RuntimeError("missing hour")
            return lats, lons, fields

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[1:4, 1:4] = 0.20
            probs[2, 2] = 0.35
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            index = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            self.assertEqual(index["status"], "partial")
            self.assertEqual(index["readyForecastHours"], [0])
            self.assertEqual(index["failedForecastHours"], [1])
            hour_dir = output_dir / "hours" / "f00"
            self.assertTrue((hour_dir / "risk_polygons.geojson").exists())
            self.assertTrue((hour_dir / "probability_tile.json").exists())
            self.assertTrue((hour_dir / "upper_air_overlay.json").exists())
            self.assertTrue((hour_dir / "hazard_probability_shapes.geojson").exists())
            tile = json.loads((hour_dir / "probability_tile.json").read_text(encoding="utf-8"))
            hour_metadata = json.loads((hour_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("riskShapes", tile)
            self.assertIn("hazardProbabilityShapes", tile)
            self.assertEqual(hour_metadata["artifacts"]["riskPolygons"], "risk_polygons.geojson")
            self.assertEqual(hour_metadata["artifacts"]["probabilityTile"], "probability_tile.json")
            self.assertEqual(hour_metadata["artifacts"]["upperAirOverlay"], "upper_air_overlay.json")
            self.assertIn("region", hour_metadata)
            self.assertIn("ingredients", hour_metadata)
            self.assertIn("ingredientSample", hour_metadata)
            row = hour_metadata["ingredientSample"]["gridRow"]
            col = hour_metadata["ingredientSample"]["gridCol"]
            self.assertEqual((row, col), (2, 2))
            self.assertEqual(hour_metadata["region"]["focusHazard"], "hail")
            self.assertEqual(hour_metadata["region"]["focusMethod"], "highest_hail_probability")
            ingredients = hour_metadata["ingredients"]
            self.assertAlmostEqual(ingredients["mlcape"], float(fields["cape_ml"][row, col]))
            self.assertAlmostEqual(ingredients["mucape"], float(fields["cape_mu"][row, col]))
            self.assertAlmostEqual(ingredients["sbcape"], float(fields["cape"][row, col]))
            self.assertAlmostEqual(ingredients["cin"], float(fields["cin_ml"][row, col]))
            self.assertAlmostEqual(ingredients["sfcDewpointF"], (float(fields["td2m"][row, col]) - 273.15) * 9 / 5 + 32)
            self.assertAlmostEqual(ingredients["pwatIn"], float(fields["pwat"][row, col]) / 25.4)
            self.assertAlmostEqual(ingredients["srh01"], float(fields["srh01"][row, col]))
            self.assertAlmostEqual(ingredients["srh03"], float(fields["srh03"][row, col]))
            self.assertAlmostEqual(
                ingredients["shear06Kt"],
                float(np.hypot(fields["u500"][row, col] - fields["u10"][row, col], fields["v500"][row, col] - fields["v10"][row, col]) * 1.9438445),
            )
            self.assertEqual(
                json.loads((output_dir / "hours" / "f01" / "metadata.json").read_text(encoding="utf-8"))["status"],
                "failed",
            )

    def test_risk_center_locator_prioritizes_tornado_probability(self) -> None:
        lats = np.array([30.0, 31.0, 32.0])
        lons = np.array([-100.0, -99.0, -98.0])
        category_grid = np.full((3, 3), SPC_RISK_LABELS.index("TSTM"), dtype=np.int16)
        category_grid[2, 2] = SPC_RISK_LABELS.index("ENH")
        tornado = np.zeros((3, 3), dtype=float)
        hail = np.zeros((3, 3), dtype=float)
        wind = np.zeros((3, 3), dtype=float)
        tornado[0, 0] = 0.02
        hail[1, 1] = 0.45
        wind[2, 2] = 0.60

        region = _region_from_max_risk_grid(
            lats,
            lons,
            category_grid,
            {"tornado": tornado, "hail": hail, "wind": wind},
            {"type": "FeatureCollection", "features": []},
        )

        self.assertEqual(region["focusHazard"], "tornado")
        self.assertEqual(region["focusMethod"], "highest_tornado_probability")
        self.assertEqual((region["centerLat"], region["centerLon"]), (30.0, -100.0))
        self.assertAlmostEqual(region["focusProbability"], 0.02)

    def test_risk_center_locator_falls_back_to_highest_wind_or_hail_probability(self) -> None:
        lats = np.array([30.0, 31.0, 32.0])
        lons = np.array([-100.0, -99.0, -98.0])
        category_grid = np.full((3, 3), SPC_RISK_LABELS.index("TSTM"), dtype=np.int16)
        tornado = np.zeros((3, 3), dtype=float)
        hail = np.zeros((3, 3), dtype=float)
        wind = np.zeros((3, 3), dtype=float)
        wind[2, 0] = 0.30
        hail[0, 2] = 0.55

        region = _region_from_max_risk_grid(
            lats,
            lons,
            category_grid,
            {"tornado": tornado, "hail": hail, "wind": wind},
            {"type": "FeatureCollection", "features": []},
        )

        self.assertEqual(region["focusHazard"], "hail")
        self.assertEqual(region["focusMethod"], "highest_hail_probability")
        self.assertEqual((region["centerLat"], region["centerLon"]), (30.0, -98.0))
        self.assertAlmostEqual(region["focusProbability"], 0.55)

    def test_generated_risk_map_keeps_less_strict_generated_contours(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        shape = (30, 30)
        lats = np.linspace(30.0, 40.0, shape[0])
        lons = np.linspace(-102.0, -88.0, shape[1])
        fields = small_fields(shape)
        block = (slice(12, 16), slice(12, 16))
        fields["cape"][block] = 2500.0
        fields["cape_ml"][block] = 1700.0
        fields["cape_mu"][block] = 2600.0
        fields["cin_ml"][block] = -55.0
        fields["td2m"][block] = 296.15
        fields["t2m"][block] = 304.15
        fields["u500"][block] = 44.0
        fields["v500"][block] = 1.0
        fields["srh01"][block] = 80.0
        fields["srh03"][block] = 160.0

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            return lats, lons, fields

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[block] = 0.35
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        model_status_payload = {
            "active": True,
            "version": "unit",
            "featureSchemaHash": "hash",
            "productionCapable": True,
            "trainingRows": 6000,
            "datasetQuality": {
                "trainingRows": 6000,
                "minimumRecommendedRows": 5000,
                "status": "production",
            },
        }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value=model_status_payload,
        ):
            output_dir = Path(tmp) / "latest_incremental"
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            hour_dir = output_dir / "hours" / "f00"
            hour_metadata = json.loads((hour_dir / "metadata.json").read_text(encoding="utf-8"))
            risk_geojson = json.loads((hour_dir / "risk_polygons.geojson").read_text(encoding="utf-8"))
            hazard_geojson = json.loads((hour_dir / "hazard_probability_shapes.geojson").read_text(encoding="utf-8"))
            tile = json.loads((hour_dir / "probability_tile.json").read_text(encoding="utf-8"))

            strict_tile_max = max(max(row) for row in tile["categoryOrdinal"])
            map_max = max(feature["properties"]["ordinal"] for feature in risk_geojson["features"])
            hail_thresholds = {
                round(float(feature["properties"]["threshold"]), 2)
                for feature in hazard_geojson["features"]
                if feature["properties"]["hazard"] == "hail"
            }
            strict_hail_peak = max(max(row) for row in tile["probabilities"]["hail"])
            self.assertEqual(strict_tile_max, SPC_RISK_LABELS.index("MRGL"))
            self.assertEqual(map_max, SPC_RISK_LABELS.index("ENH"))
            self.assertLessEqual(strict_hail_peak, 0.149)
            self.assertIn(0.30, hail_thresholds)
            self.assertEqual(hour_metadata["categoryCounts"].get("ENH", 0), 0)
            self.assertEqual(hour_metadata["riskMapCategoryCounts"]["ENH"], 16)

    def test_generated_artifacts_keep_strict_water_regions_none(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        shape = (12, 12)
        cases = {
            "gulfOfMexico": (
                np.linspace(24.0, 25.0, shape[0]),
                np.linspace(-94.0, -90.0, shape[1]),
            ),
            "floridaGulf": (
                np.linspace(24.2, 25.0, shape[0]),
                np.linspace(-85.0, -83.0, shape[1]),
            ),
            "atlanticOcean": (
                np.linspace(30.0, 32.0, shape[0]),
                np.linspace(-78.0, -76.0, shape[1]),
            ),
        }

        fields = small_fields(shape)
        fields["cape"] = np.full(shape, 2500.0)
        fields["cape_ml"] = np.full(shape, 1700.0)
        fields["cape_mu"] = np.full(shape, 2600.0)
        fields["cin_ml"] = np.full(shape, -55.0)
        fields["td2m"] = np.full(shape, 296.15)
        fields["t2m"] = np.full(shape, 304.15)
        fields["u500"] = np.full(shape, 44.0)
        fields["v500"] = np.full(shape, 1.0)
        fields["srh01"] = np.full(shape, 80.0)
        fields["srh03"] = np.full(shape, 160.0)

        def fake_detect(_session, _now):
            return cycle

        def fake_predict(features):
            probs = np.full(features.shape, 0.35)
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        model_status_payload = {
            "active": True,
            "version": "unit",
            "featureSchemaHash": "hash",
            "productionCapable": True,
            "trainingRows": 6000,
            "datasetQuality": {
                "trainingRows": 6000,
                "minimumRecommendedRows": 5000,
                "status": "production",
            },
        }

        for case_name, (lats, lons) in cases.items():
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as tmp, patch(
                "backend.ml.outlook_pipeline.model_status",
                return_value=model_status_payload,
            ):
                def fake_fetch(_ref, _session):
                    return lats, lons, fields

                output_dir = Path(tmp) / "latest_incremental"
                run_incremental_pipeline(
                    output_dir=output_dir,
                    forecast_hours=[0],
                    now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                    tile_stride=1,
                    detect_cycle_fn=fake_detect,
                    fetch_hour_fn=fake_fetch,
                    predictor_fn=fake_predict,
                )

                hour_dir = output_dir / "hours" / "f00"
                hour_metadata = json.loads((hour_dir / "metadata.json").read_text(encoding="utf-8"))
                risk_geojson = json.loads((hour_dir / "risk_polygons.geojson").read_text(encoding="utf-8"))
                hazard_geojson = json.loads((hour_dir / "hazard_probability_shapes.geojson").read_text(encoding="utf-8"))
                tile = json.loads((hour_dir / "probability_tile.json").read_text(encoding="utf-8"))

                self.assertEqual(hour_metadata["categoryCounts"], {"NONE": shape[0] * shape[1]})
                self.assertEqual(hour_metadata["riskMapCategoryCounts"], {"NONE": shape[0] * shape[1]})
                self.assertEqual(risk_geojson["features"], [])
                self.assertEqual(hazard_geojson["features"], [])
                self.assertEqual(max(max(row) for row in tile["categoryOrdinal"]), SPC_RISK_LABELS.index("NONE"))

    def test_latest_metadata_route_prefers_complete_incremental_snapshot(self) -> None:
        import backend.server as server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest_dir = root / "latest"
            incremental_dir = root / "latest_incremental"
            complete_dir = root / "latest_incremental_complete"
            latest_dir.mkdir(parents=True)
            (complete_dir / "hours" / "f00").mkdir(parents=True)
            (latest_dir / "metadata.json").write_text(json.dumps({
                "cycle": "HRRR 06Z 20260504",
            }), encoding="utf-8")
            (complete_dir / "index.json").write_text(json.dumps({
                "status": "complete",
                "cycle": "HRRR 06Z 20260517",
                "cycleTimeISO": "2026-05-17T06:00:00Z",
                "generatedAtISO": "2026-05-17T10:18:46Z",
                "readyForecastHours": list(range(49)),
                "featureSchemaHash": "hash",
                "gridStride": 3,
                "tileStride": 1,
                "riskLabels": list(SPC_RISK_LABELS),
            }), encoding="utf-8")

            with patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}), patch.object(
                server,
                "ARTIFACT_DIR",
                latest_dir,
            ), patch.object(
                server,
                "INCREMENTAL_ARTIFACT_DIR",
                incremental_dir,
            ), patch.object(
                server,
                "INCREMENTAL_COMPLETE_ARTIFACT_DIR",
                complete_dir,
            ):
                response = server.app.test_client().get("/api/outlook/latest")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["cycle"], "HRRR 06Z 20260517")
        self.assertEqual(payload["generatedAtISO"], "2026-05-17T10:18:46Z")

    def test_outlook_trends_route_compares_previous_cycle_cache(self) -> None:
        import backend.server as server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current_dir = root / "latest_incremental"
            previous_dir = root / "latest_incremental.previous"
            for artifact_dir, cycle, center_lon, category_counts, probabilities in (
                (
                    previous_dir,
                    "HRRR 06Z 20260517",
                    -98.0,
                    {"NONE": 10, "TSTM": 2000, "MRGL": 900, "SLGT": 600},
                    {"tornado": 0.02, "hail": 0.12, "wind": 0.10},
                ),
                (
                    current_dir,
                    "HRRR 12Z 20260517",
                    -96.5,
                    {"NONE": 10, "TSTM": 2000, "MRGL": 900, "SLGT": 600, "ENH": 1300},
                    {"tornado": 0.07, "hail": 0.22, "wind": 0.26},
                ),
            ):
                hour_dir = artifact_dir / "hours" / "f00"
                hour_dir.mkdir(parents=True)
                (artifact_dir / "index.json").write_text(json.dumps({
                    "status": "complete",
                    "cycle": cycle,
                    "cycleTimeISO": "2026-05-17T12:00:00Z",
                    "readyForecastHours": [0],
                    "requestedForecastHours": [0],
                    "model": {"active": True},
                }), encoding="utf-8")
                (hour_dir / "metadata.json").write_text(json.dumps({
                    "forecastHour": 0,
                    "validTimeISO": "2026-05-17T12:00:00Z",
                    "categoryCounts": category_counts,
                    "region": {
                        "label": "Test risk center",
                        "centerLat": 35.0,
                        "centerLon": center_lon,
                        "bbox": [center_lon - 1, 34.0, center_lon + 1, 36.0],
                    },
                    "probabilityStats": {
                        "categoryConsistencyProbabilityMax": probabilities,
                    },
                }), encoding="utf-8")
                (hour_dir / "risk_polygons.geojson").write_text(json.dumps({
                    "type": "FeatureCollection",
                    "features": [],
                }), encoding="utf-8")

            with (
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", current_dir),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", None),
                patch.object(server, "ARTIFACT_DIR", root / "latest"),
            ):
                response = server.app.test_client().get("/api/outlook/trends?hour=0&region=conus")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["active"])
        self.assertEqual(payload["trendCategory"], "UPGRADED")
        self.assertEqual(payload["previousCategory"], "SLGT")
        self.assertEqual(payload["currentCategory"], "ENH")
        self.assertAlmostEqual(payload["tornadoDelta"], 0.05)
        self.assertGreater(payload["spatial"]["distanceMiles"], 80)

    def test_incremental_previous_cycle_cache_copies_current_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "latest_incremental"
            hour_dir = output_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            (output_dir / "index.json").write_text(json.dumps({
                "cycle": "HRRR 06Z 20260517",
                "readyForecastHours": [0],
            }), encoding="utf-8")
            (hour_dir / "metadata.json").write_text(json.dumps({
                "forecastHour": 0,
                "categoryCounts": {"SLGT": 700},
            }), encoding="utf-8")

            previous_dir = _cache_previous_incremental_cycle(output_dir)

            self.assertEqual(previous_dir, output_dir.with_name("latest_incremental.previous"))
            self.assertTrue((previous_dir / "index.json").exists())
            self.assertTrue((previous_dir / "hours" / "f00" / "metadata.json").exists())

    def test_probability_tiles_route_uses_latest_bulk_artifact_when_present(self) -> None:
        import backend.server as server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest_dir = root / "latest"
            incremental_dir = root / "latest_incremental"
            complete_dir = root / "latest_incremental_complete"
            latest_dir.mkdir(parents=True)
            complete_dir.mkdir(parents=True)
            (latest_dir / "probability_tiles.json").write_text(json.dumps({
                "cycle": "HRRR 06Z 20260504",
                "hours": [{"forecastHour": 0, "tile": {"categoryLabel": [["NONE"]]}}],
            }), encoding="utf-8")
            (complete_dir / "index.json").write_text(json.dumps({
                "status": "complete",
                "cycle": "HRRR 06Z 20260517",
                "readyForecastHours": list(range(49)),
            }), encoding="utf-8")

            with (
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
                patch.object(server, "ARTIFACT_DIR", latest_dir),
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", incremental_dir),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", complete_dir),
                patch.object(server, "_incremental_probability_tiles", side_effect=AssertionError("bulk assembly should not run")),
            ):
                response = server.app.test_client().get("/api/outlook/probability-tiles")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["cycle"], "HRRR 06Z 20260504")
        self.assertEqual(len(payload["hours"]), 1)

    def test_incremental_pipeline_processes_forecast_hours_in_parallel(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.05)
                return lats, lons, small_fields()
            finally:
                with lock:
                    active -= 1

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            index = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0, 1, 2, 3],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                hour_workers=3,
                range_workers=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )
            disk_index = json.loads((output_dir / "index.json").read_text(encoding="utf-8"))
            for hour in [0, 1, 2, 3]:
                hour_dir = output_dir / "hours" / f"f{hour:02d}"
                self.assertTrue((hour_dir / "risk_polygons.geojson").exists())
                self.assertTrue((hour_dir / "probability_tile.json").exists())
                self.assertTrue((hour_dir / "upper_air_overlay.json").exists())
                self.assertTrue((hour_dir / "metadata.json").exists())

        self.assertGreater(max_active, 1)
        self.assertEqual(index["readyForecastHours"], [0, 1, 2, 3])
        self.assertEqual(disk_index["readyForecastHours"], [0, 1, 2, 3])
        self.assertEqual(disk_index["failedForecastHours"], [])
        self.assertEqual(disk_index["pendingForecastHours"], [])

    def test_incremental_pipeline_skips_ready_hours_unless_forced(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        fetched_hours: list[int] = []

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            fetched_hours.append(ref.forecast_hour)
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                hour_workers=2,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )
            after_first = list(fetched_hours)
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                hour_workers=2,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )
            after_skip = list(fetched_hours)
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                hour_workers=2,
                force=True,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

        self.assertEqual(sorted(after_first), [0, 1])
        self.assertEqual(after_skip, after_first)
        self.assertEqual(sorted(fetched_hours), [0, 0, 1])

    def test_incremental_pipeline_finalizes_merged_shard_hours_without_refetching(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            return lats, lons, small_fields()

        def failing_fetch(ref, _session):
            raise AssertionError(f"downloaded shard hour F{ref.forecast_hour:02d} should not be refetched")

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            root = Path(tmp)
            shard0 = root / "shard0"
            shard1 = root / "shard1"
            merged = root / "latest_incremental"

            run_incremental_pipeline(
                output_dir=shard0,
                forecast_hours=[0, 1],
                process_forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
                verify_spc=False,
            )
            run_incremental_pipeline(
                output_dir=shard1,
                forecast_hours=[0, 1],
                process_forecast_hours=[1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
                verify_spc=False,
            )

            (merged / "hours").mkdir(parents=True)
            shutil.copytree(shard0 / "hours" / "f00", merged / "hours" / "f00")
            shutil.copytree(shard1 / "hours" / "f01", merged / "hours" / "f01")

            index = run_incremental_pipeline(
                output_dir=merged,
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=failing_fetch,
                predictor_fn=fake_predict,
                verify_spc=False,
            )

            complete_index = json.loads((root / "latest_incremental_complete" / "index.json").read_text(encoding="utf-8"))

        self.assertEqual(index["status"], "complete")
        self.assertEqual(index["readyForecastHours"], [0, 1])
        self.assertEqual(index["pendingForecastHours"], [])
        self.assertEqual(complete_index["status"], "complete")
        self.assertEqual(complete_index["readyForecastHours"], [0, 1])

    def test_incremental_pipeline_publishes_complete_snapshot_for_fallback(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            complete_index = json.loads((Path(tmp) / "latest_incremental_complete" / "index.json").read_text(encoding="utf-8"))

        self.assertEqual(complete_index["status"], "complete")
        self.assertEqual(complete_index["readyForecastHours"], [0, 1])

    def test_incremental_pipeline_writes_spc_verification_for_complete_snapshot(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            return lats, lons, small_fields()

        def fake_predict(features):
            return {
                "tornado": np.zeros(features.shape, dtype=float),
                "hail": np.zeros(features.shape, dtype=float),
                "wind": np.full(features.shape, 0.08, dtype=float),
            }

        def fake_spc(_session, output_dir):
            self.assertIsNotNone(output_dir)
            assert output_dir is not None
            self.assertTrue((output_dir / "hours" / "f00" / "probability_tile.json").exists())
            return {
                "day1Url": "https://spc.example/day1",
                "geojsonZipUrl": "https://spc.example/day1.zip",
                "fetchedAtISO": "2024-05-04T13:15:00Z",
                "categoryGeojson": fake_spc_geojson(),
            }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0, 1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
                verify_spc=True,
                spc_fetch_fn=fake_spc,
            )

            index = json.loads((output_dir / "index.json").read_text(encoding="utf-8"))
            summary = json.loads((output_dir / "verification_summary.json").read_text(encoding="utf-8"))
            complete_dir = Path(tmp) / "latest_incremental_complete"
            complete_index = json.loads((complete_dir / "index.json").read_text(encoding="utf-8"))
            complete_summary = json.loads((complete_dir / "verification_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(index["spcVerification"]["spcFetchedAtISO"], "2024-05-04T13:15:00Z")
        self.assertTrue(index["spcVerification"]["spcFetchedAfterPredictionArtifacts"])
        self.assertEqual(summary["verificationGridSource"], "incremental_probability_tiles")
        self.assertEqual(summary["verificationForecastHours"], [0, 1])
        self.assertEqual(complete_index["spcVerification"]["spcFetchedAtISO"], "2024-05-04T13:15:00Z")
        self.assertEqual(complete_summary["spcFetchedAtISO"], "2024-05-04T13:15:00Z")

    def test_static_export_prefers_incremental_spc_verification_artifacts(self) -> None:
        module_path = Path(__file__).resolve().parents[2] / "scripts" / "export-static-api.py"
        spec = importlib.util.spec_from_file_location("export_static_api_module", module_path)
        assert spec is not None and spec.loader is not None
        export_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(export_module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "latest_incremental_complete"
            legacy_dir = root / "latest"
            output_dir = root / "out"
            artifact_dir.mkdir()
            legacy_dir.mkdir()
            (artifact_dir / "verification_summary.json").write_text(json.dumps({"source": "incremental"}), encoding="utf-8")
            (legacy_dir / "verification_summary.json").write_text(json.dumps({"source": "legacy"}), encoding="utf-8")

            copied = export_module.copy_first_existing(
                [artifact_dir / "verification_summary.json", legacy_dir / "verification_summary.json"],
                output_dir / "outlook" / "verification.json",
            )
            payload = json.loads((output_dir / "outlook" / "verification.json").read_text(encoding="utf-8"))

        self.assertEqual(copied, artifact_dir / "verification_summary.json")
        self.assertEqual(payload["source"], "incremental")

    def test_complete_snapshot_publish_avoids_directory_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "latest_incremental"
            hour_dir = output_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            index = {
                "status": "complete",
                "readyForecastHours": [0],
                "requestedForecastHours": [0],
                "cycle": "new",
            }
            (output_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
            for name in ("metadata.json", "probability_tile.json", "risk_polygons.geojson", "upper_air_overlay.json"):
                (hour_dir / name).write_text(json.dumps({"name": name}), encoding="utf-8")

            complete_dir = root / "latest_incremental_complete"
            complete_dir.mkdir()
            (complete_dir / "index.json").write_text(json.dumps({"cycle": "old"}), encoding="utf-8")

            with patch("backend.ml.outlook_pipeline.shutil.move", side_effect=AssertionError("directory move should not run")):
                _publish_complete_incremental_snapshot(output_dir, index, [0])

            complete_index = json.loads((complete_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(complete_index["cycle"], "new")
            self.assertTrue((complete_dir / "hours" / "f00" / "probability_tile.json").exists())

    def test_complete_snapshot_publish_preserves_00z_cache_for_merged_d1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "latest_incremental"
            hour_dir = output_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            index_00z = {
                "status": "complete",
                "readyForecastHours": [0],
                "requestedForecastHours": [0],
                "cycle": "HRRR 00Z 20260608",
                "cycleTimeISO": "2026-06-08T00:00:00Z",
                "cyclePolicy": {"model": "HRRR"},
            }
            (output_dir / "index.json").write_text(json.dumps(index_00z), encoding="utf-8")
            (output_dir / "metadata.json").write_text(json.dumps(index_00z), encoding="utf-8")
            (hour_dir / "probability_tile.json").write_text(json.dumps({"cycle": "00z"}), encoding="utf-8")

            _publish_complete_incremental_snapshot(output_dir, index_00z, [0])

            cache_dir = _merged_d1_00z_cache_dir(output_dir)
            cache_index = json.loads((cache_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(cache_index["cycleTimeISO"], "2026-06-08T00:00:00Z")
            self.assertTrue((cache_dir / "hours" / "f00" / "probability_tile.json").exists())

            index_06z = {
                **index_00z,
                "cycle": "HRRR 06Z 20260608",
                "cycleTimeISO": "2026-06-08T06:00:00Z",
            }
            (output_dir / "index.json").write_text(json.dumps(index_06z), encoding="utf-8")
            (output_dir / "metadata.json").write_text(json.dumps(index_06z), encoding="utf-8")
            (hour_dir / "probability_tile.json").write_text(json.dumps({"cycle": "06z"}), encoding="utf-8")

            _publish_complete_incremental_snapshot(output_dir, index_06z, [0])

            cache_index_after_06z = json.loads((cache_dir / "index.json").read_text(encoding="utf-8"))
            cache_tile_after_06z = json.loads((cache_dir / "hours" / "f00" / "probability_tile.json").read_text(encoding="utf-8"))
            self.assertEqual(cache_index_after_06z["cycleTimeISO"], "2026-06-08T00:00:00Z")
            self.assertEqual(cache_tile_after_06z["cycle"], "00z")

    def test_gcs_incremental_publish_uses_artifact_layout_and_index_last(self) -> None:
        uploads: list[str] = []

        class FakeBlob:
            def __init__(self, name: str):
                self.name = name

            def upload_from_filename(self, _filename: str) -> None:
                uploads.append(self.name)

        class FakeBucket:
            def blob(self, name: str) -> FakeBlob:
                return FakeBlob(name)

        class FakeClient:
            def bucket(self, _name: str) -> FakeBucket:
                return FakeBucket()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline._get_gcs_storage_client",
            return_value=FakeClient(),
        ):
            root = Path(tmp)
            output_dir = root / "latest_incremental"
            hour_dir = output_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            index = {
                "status": "complete",
                "readyForecastHours": [0],
                "requestedForecastHours": [0],
                "cycle": "new",
            }
            (output_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
            for name in ("metadata.json", "probability_tile.json", "risk_polygons.geojson", "upper_air_overlay.json"):
                (hour_dir / name).write_text(json.dumps({"name": name}), encoding="utf-8")
            _publish_complete_incremental_snapshot(output_dir, index, [0])
            zeroz_cache = _merged_d1_00z_cache_dir(output_dir)
            zeroz_hour_dir = zeroz_cache / "hours" / "f00"
            zeroz_hour_dir.mkdir(parents=True)
            (zeroz_cache / "index.json").write_text(json.dumps({"cycle": "HRRR 00Z 20260608"}), encoding="utf-8")
            (zeroz_hour_dir / "probability_tile.json").write_text(json.dumps({"cycle": "00z"}), encoding="utf-8")

            result = _publish_incremental_artifacts_to_gcs(output_dir, index, [0], "bucket", "prod/artifacts")

        current_hour_key = "prod/artifacts/latest_incremental/hours/f00/probability_tile.json"
        current_index_key = "prod/artifacts/latest_incremental/index.json"
        complete_hour_key = "prod/artifacts/latest_incremental_complete/hours/f00/probability_tile.json"
        complete_index_key = "prod/artifacts/latest_incremental_complete/index.json"
        zeroz_hour_key = "prod/artifacts/latest_incremental_hrrr_00z/hours/f00/probability_tile.json"
        zeroz_index_key = "prod/artifacts/latest_incremental_hrrr_00z/index.json"
        self.assertEqual(result["currentFiles"], 5)
        self.assertEqual(result["completeFiles"], 5)
        self.assertEqual(result["mergedD1ZeroZFiles"], 2)
        self.assertIn(current_hour_key, uploads)
        self.assertIn(complete_hour_key, uploads)
        self.assertIn(zeroz_hour_key, uploads)
        self.assertLess(uploads.index(current_hour_key), uploads.index(current_index_key))
        self.assertLess(uploads.index(complete_hour_key), uploads.index(complete_index_key))
        self.assertLess(uploads.index(zeroz_hour_key), uploads.index(zeroz_index_key))

    def test_gcs_shard_publish_uploads_only_processed_hour_dirs(self) -> None:
        uploads: list[str] = []

        class FakeBlob:
            def __init__(self, name: str):
                self.name = name

            def upload_from_filename(self, _filename: str) -> None:
                uploads.append(self.name)

        class FakeBucket:
            def blob(self, name: str) -> FakeBlob:
                return FakeBlob(name)

        class FakeClient:
            def bucket(self, _name: str) -> FakeBucket:
                return FakeBucket()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline._get_gcs_storage_client",
            return_value=FakeClient(),
        ):
            root = Path(tmp)
            output_dir = root / "latest_incremental"
            for hour in (0, 1):
                hour_dir = output_dir / "hours" / f"f{hour:02d}"
                hour_dir.mkdir(parents=True)
                for name in ("metadata.json", "probability_tile.json", "risk_polygons.geojson", "upper_air_overlay.json"):
                    (hour_dir / name).write_text(json.dumps({"hour": hour, "name": name}), encoding="utf-8")
            index = {"status": "partial", "artifactGenerationId": "exec-1"}

            result = _publish_incremental_shard_artifacts_to_gcs(output_dir, index, [1], "bucket", "prod/artifacts")

        self.assertEqual(result["currentFiles"], 4)
        self.assertTrue(all("/hours/f01/" in name for name in uploads))
        self.assertNotIn("prod/artifacts/latest_incremental/hours/f00/metadata.json", uploads)
        self.assertNotIn("prod/artifacts/latest_incremental/index.json", uploads)

    def test_incremental_hour_ready_can_require_current_generation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "latest_incremental"
            hour_dir = output_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            metadata = {
                "forecastHour": 0,
                "validTimeISO": "2024-05-04T12:00:00Z",
                "status": "ready",
                "artifactGenerationId": "exec-new",
                "region": {"centerLat": 35.0, "centerLon": -97.0},
                "ingredients": {},
                "ingredientSample": {"gridRow": 1, "gridCol": 1},
            }
            for name in ("probability_tile.json", "risk_polygons.geojson", "upper_air_overlay.json"):
                (hour_dir / name).write_text("{}", encoding="utf-8")
            (hour_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

            self.assertTrue(_incremental_hour_ready(output_dir, 0, cycle_time_iso="2024-05-04T12:00:00Z", artifact_generation_id="exec-new"))
            self.assertFalse(_incremental_hour_ready(output_dir, 0, cycle_time_iso="2024-05-04T12:00:00Z", artifact_generation_id="exec-old"))

    def test_gcs_hydrate_restores_incremental_artifact_layout(self) -> None:
        class FakeBlob:
            def __init__(self, name: str, data: str):
                self.name = name
                self.data = data

            def download_to_filename(self, filename: str) -> None:
                Path(filename).write_text(self.data, encoding="utf-8")

        class FakeBucket:
            def __init__(self):
                self.blobs = [
                    FakeBlob("prod/latest_incremental/index.json", '{"status":"complete"}'),
                    FakeBlob("prod/latest_incremental/hours/f00/metadata.json", '{"forecastHour":0}'),
                    FakeBlob("prod/latest_incremental_complete/index.json", '{"status":"complete"}'),
                    FakeBlob("prod/latest_incremental_hrrr_00z/index.json", '{"cycle":"HRRR 00Z 20260608"}'),
                ]

            def list_blobs(self, prefix: str):
                return [blob for blob in self.blobs if blob.name.startswith(prefix)]

        class FakeClient:
            def bucket(self, _name: str) -> FakeBucket:
                return FakeBucket()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline._get_gcs_storage_client",
            return_value=FakeClient(),
        ):
            output_dir = Path(tmp) / "latest_incremental"
            result = _hydrate_incremental_artifacts_from_gcs(output_dir, "bucket", "prod")

            self.assertTrue((output_dir / "index.json").exists())
            self.assertTrue((output_dir / "hours" / "f00" / "metadata.json").exists())
            self.assertTrue((Path(tmp) / "latest_incremental_complete" / "index.json").exists())
            self.assertTrue((Path(tmp) / "latest_incremental_hrrr_00z" / "index.json").exists())

        self.assertEqual(result["currentFiles"], 2)
        self.assertEqual(result["completeFiles"], 1)
        self.assertEqual(result["mergedD1ZeroZFiles"], 1)

    def test_gcs_hydrate_skips_objects_deleted_during_concurrent_publish(self) -> None:
        class NotFound(Exception):
            pass

        class FakeBlob:
            def __init__(self, name: str, data: str | None):
                self.name = name
                self.data = data

            def download_to_filename(self, filename: str) -> None:
                if self.data is None:
                    raise NotFound("missing")
                Path(filename).write_text(self.data, encoding="utf-8")

        class FakeBucket:
            def __init__(self):
                self.blobs = [
                    FakeBlob("prod/latest_incremental/hours/f00/metadata.json", '{"forecastHour":0}'),
                    FakeBlob("prod/latest_incremental/hours/f00/risk_polygons.geojson", None),
                ]

            def list_blobs(self, prefix: str):
                return [blob for blob in self.blobs if blob.name.startswith(prefix)]

        class FakeClient:
            def bucket(self, _name: str) -> FakeBucket:
                return FakeBucket()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline._get_gcs_storage_client",
            return_value=FakeClient(),
        ):
            output_dir = Path(tmp) / "latest_incremental"
            result = _hydrate_incremental_artifacts_from_gcs(output_dir, "bucket", "prod")

            self.assertTrue((output_dir / "hours" / "f00" / "metadata.json").exists())

        self.assertEqual(result["currentFiles"], 1)

    def test_incremental_pipeline_can_publish_finished_artifacts_to_gcs(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            return {"tornado": probs, "hail": probs, "wind": probs}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ), patch(
            "backend.ml.outlook_pipeline._publish_incremental_artifacts_to_gcs",
            return_value={"enabled": True},
        ) as publish_mock:
            output_dir = Path(tmp) / "latest_incremental"
            index = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
                publish_gcs_bucket="bucket",
                publish_gcs_prefix="prod/artifacts",
            )

        self.assertEqual(index["status"], "complete")
        publish_mock.assert_called_once()

    def test_incremental_pipeline_skips_gcs_publish_when_nothing_changed(self) -> None:
        cycle = HrrrCycle("20240504", 12)

        def fake_detect(_session, _now):
            return cycle

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ), patch(
            "backend.ml.outlook_pipeline._publish_incremental_artifacts_to_gcs",
            side_effect=AssertionError("unchanged artifacts should not be republished"),
        ):
            root = Path(tmp)
            output_dir = root / "latest_incremental"
            complete_dir = root / "latest_incremental_complete"
            hour_dir = output_dir / "hours" / "f00"
            complete_hour_dir = complete_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            complete_hour_dir.mkdir(parents=True)
            index = {
                "cycle": cycle.label,
                "status": "complete",
                "readyForecastHours": [0],
                "requestedForecastHours": [0],
                "model": {"active": True},
            }
            (output_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
            (complete_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
            for directory in (hour_dir, complete_hour_dir):
                (directory / "risk_polygons.geojson").write_text("{}", encoding="utf-8")
                (directory / "probability_tile.json").write_text("{}", encoding="utf-8")
                (directory / "upper_air_overlay.json").write_text("{}", encoding="utf-8")
                (directory / "metadata.json").write_text(json.dumps({
                    "forecastHour": 0,
                    "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
                    "validTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
                    "region": {"centerLat": 35.0, "centerLon": -97.0},
                    "ingredients": {},
                    "ingredientSample": {"gridRow": 0, "gridCol": 0},
                }), encoding="utf-8")

            result = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                detect_cycle_fn=fake_detect,
                publish_gcs_bucket="bucket",
            )

        self.assertEqual(result["readyForecastHours"], [0])

    def test_incremental_pipeline_merges_existing_ready_hours(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        fetched_hours: list[int] = []

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            fetched_hours.append(ref.forecast_hour)
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[1:4, 1:4] = 0.20
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            first = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )
            second = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[1],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            self.assertEqual(first["readyForecastHours"], [0])
            self.assertEqual(second["readyForecastHours"], [0, 1])
            self.assertEqual(second["requestedForecastHours"], [0, 1])
            self.assertEqual(fetched_hours, [0, 1])
            self.assertTrue((output_dir / "hours" / "f00" / "probability_tile.json").exists())
            self.assertTrue((output_dir / "hours" / "f00" / "upper_air_overlay.json").exists())
            self.assertTrue((output_dir / "hours" / "f01" / "probability_tile.json").exists())
            self.assertTrue((output_dir / "hours" / "f01" / "upper_air_overlay.json").exists())

    def test_incremental_pipeline_regenerates_old_hours_missing_upper_air_overlay(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        fetched_hours: list[int] = []

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            fetched_hours.append(ref.forecast_hour)
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[1:4, 1:4] = 0.20
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )
            (output_dir / "hours" / "f00" / "upper_air_overlay.json").unlink()
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            self.assertEqual(fetched_hours, [0, 0])
            self.assertTrue((output_dir / "hours" / "f00" / "upper_air_overlay.json").exists())

    def test_incremental_pipeline_regenerates_old_hours_missing_focus_metadata(self) -> None:
        cycle = HrrrCycle("20240504", 12)
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        fetched_hours: list[int] = []

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            fetched_hours.append(ref.forecast_hour)
            return lats, lons, small_fields()

        def fake_predict(features):
            probs = np.zeros(features.shape, dtype=float)
            probs[1:4, 1:4] = 0.20
            return {"tornado": probs * 0.0, "hail": probs, "wind": probs * 0.0}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            output_dir = Path(tmp) / "latest_incremental"
            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )
            metadata_path = output_dir / "hours" / "f00" / "metadata.json"
            stale_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            stale_metadata.pop("region")
            stale_metadata.pop("ingredients")
            stale_metadata.pop("ingredientSample")
            metadata_path.write_text(json.dumps(stale_metadata), encoding="utf-8")

            run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=[0],
                now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                tile_stride=1,
                detect_cycle_fn=fake_detect,
                fetch_hour_fn=fake_fetch,
                predictor_fn=fake_predict,
            )

            regenerated = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(fetched_hours, [0, 0])
            self.assertIn("region", regenerated)
            self.assertIn("ingredients", regenerated)
            self.assertIn("ingredientSample", regenerated)

    def test_pipeline_failure_preserves_previous_latest_artifacts(self) -> None:
        cycle = HrrrCycle("20240504", 12)

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(_ref, _session):
            raise RuntimeError("hour unavailable")

        with tempfile.TemporaryDirectory() as tmp, patch(
            "backend.ml.outlook_pipeline.model_status",
            return_value={"active": True, "version": "unit", "featureSchemaHash": "hash"},
        ):
            latest = Path(tmp) / "latest"
            latest.mkdir()
            (latest / "metadata.json").write_text(json.dumps({"cycle": "old"}), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                run_pipeline(
                    output_dir=latest,
                    forecast_hours=[0, 1],
                    now=datetime(2024, 5, 4, 13, tzinfo=timezone.utc),
                    verify_spc=False,
                    preview=False,
                    min_successful_hours=2,
                    detect_cycle_fn=fake_detect,
                    fetch_hour_fn=fake_fetch,
                )

            self.assertEqual(json.loads((latest / "metadata.json").read_text(encoding="utf-8"))["cycle"], "old")
            failed = json.loads((Path(tmp) / "latest.failed.json").read_text(encoding="utf-8"))
            self.assertTrue(failed["previousLatestPreserved"])
            self.assertEqual(len(failed["failedHours"]), 2)

    def test_publish_restores_previous_latest_if_new_move_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "latest"
            working = root / "latest.tmp"
            latest.mkdir()
            working.mkdir()
            (latest / "metadata.json").write_text(json.dumps({"cycle": "old"}), encoding="utf-8")
            (working / "metadata.json").write_text(json.dumps({"cycle": "new"}), encoding="utf-8")
            real_move = __import__("shutil").move
            move_calls = {"count": 0}

            def flaky_move(src, dst):
                move_calls["count"] += 1
                if move_calls["count"] == 2:
                    raise RuntimeError("publish move failed")
                return real_move(src, dst)

            with patch("backend.ml.outlook_pipeline.shutil.move", side_effect=flaky_move):
                with self.assertRaises(RuntimeError):
                    _publish_working_dir(working, latest)

            self.assertTrue((latest / "metadata.json").exists())
            self.assertEqual(json.loads((latest / "metadata.json").read_text(encoding="utf-8"))["cycle"], "old")

    def test_server_serves_latest_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "latest"
            incremental_dir = root / "latest_incremental"
            complete_dir = root / "latest_incremental_complete"
            artifact_dir.mkdir()
            (artifact_dir / "metadata.json").write_text(json.dumps({"cycle": "unit"}), encoding="utf-8")
            from backend import server

            with (
                patch.object(server, "ARTIFACT_DIR", artifact_dir),
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", incremental_dir),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", complete_dir),
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
            ):
                client = server.app.test_client()
                response = client.get("/api/outlook/latest")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["cycle"], "unit")
            self.assertIn("max-age=", response.headers.get("Cache-Control", ""))

    def test_server_missing_artifact_response_does_not_expose_raw_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "latest"
            incremental_dir = root / "latest_incremental"
            artifact_dir.mkdir()
            incremental_dir.mkdir()
            from backend import server

            with (
                patch.object(server, "ARTIFACT_DIR", artifact_dir),
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", incremental_dir),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", root / "latest_incremental_complete"),
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
            ):
                client = server.app.test_client()
                response = client.get("/api/outlook/latest")

            payload = response.get_json()
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(payload["code"], "outlook_not_ready")
            self.assertNotIn("artifactDir", payload)
            self.assertNotIn(str(root), body)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_server_artifact_only_forecast_missing_does_not_live_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incremental_dir = root / "latest_incremental"
            incremental_dir.mkdir()
            from backend import server

            with (
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", incremental_dir),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", root / "latest_incremental_complete"),
                patch.dict(os.environ, {
                    "AUTOOUTLOOK_FORECAST_SOURCE": "artifact",
                    "AUTOOUTLOOK_ENABLE_LIVE_BUILD": "false",
                    "AUTOOUTLOOK_ARTIFACT_BUCKET": "",
                }),
                patch.object(server, "build_bundle", side_effect=AssertionError("live build should not run")) as build_mock,
            ):
                client = server.app.test_client()
                response = client.get("/api/forecast")

            payload = response.get_json()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(payload["code"], "outlook_not_ready")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            build_mock.assert_not_called()

    def test_server_artifact_bucket_keys_match_artifact_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from backend import server

            with (
                patch.object(server, "ARTIFACT_DIR", root / "latest"),
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", root / "latest_incremental"),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", root / "latest_incremental_complete"),
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_PREFIX": "prod/artifacts"}),
            ):
                key = server._artifact_storage_key(root / "latest_incremental" / "hours" / "f03" / "metadata.json")

            self.assertEqual(key, "prod/artifacts/latest_incremental/hours/f03/metadata.json")

    def test_server_serves_incremental_hour_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            hour_dir = artifact_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            (artifact_dir / "index.json").write_text(json.dumps({"status": "running", "readyForecastHours": [0]}), encoding="utf-8")
            (hour_dir / "risk_polygons.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
            (hour_dir / "probability_tile.json").write_text(json.dumps({"forecastHour": 0}), encoding="utf-8")
            (hour_dir / "metadata.json").write_text(json.dumps({"forecastHour": 0, "status": "ready"}), encoding="utf-8")
            from backend import server

            with (
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", artifact_dir),
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
            ):
                client = server.app.test_client()
                index_response = client.get("/api/outlook/incremental")
                tile_response = client.get("/api/outlook/incremental/hour/0/probability-tile")
                meta_response = client.get("/api/outlook/incremental/hour/0/metadata")

            self.assertEqual(index_response.status_code, 200)
            self.assertEqual(tile_response.status_code, 200)
            self.assertEqual(meta_response.status_code, 200)
            self.assertEqual(tile_response.get_json()["forecastHour"], 0)

    def test_server_does_not_serve_stale_incremental_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            stale_hour_dir = artifact_dir / "hours" / "f01"
            stale_hour_dir.mkdir(parents=True)
            (artifact_dir / "index.json").write_text(
                json.dumps({"status": "running", "cycle": "HRRR 00Z 20260505", "readyForecastHours": [0]}),
                encoding="utf-8",
            )
            (stale_hour_dir / "probability_tile.json").write_text(json.dumps({"forecastHour": 1}), encoding="utf-8")
            from backend import server

            with (
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", artifact_dir),
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
            ):
                client = server.app.test_client()
                response = client.get("/api/outlook/incremental/hour/1/probability-tile")

            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()["code"], "incremental_hour_pending")

    def test_server_falls_back_to_last_complete_incremental_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current_dir = root / "latest_incremental"
            complete_dir = root / "latest_incremental_complete"
            current_dir.mkdir()
            complete_hour_dir = complete_dir / "hours" / "f06"
            complete_hour_dir.mkdir(parents=True)
            (current_dir / "index.json").write_text(json.dumps({
                "cycle": "HRRR 00Z 20260507",
                "status": "partial",
                "readyForecastHours": [0, 1, 2, 3, 4, 5],
                "requestedForecastHours": list(range(49)),
                "model": {"active": True},
            }), encoding="utf-8")
            (complete_dir / "index.json").write_text(json.dumps({
                "cycle": "HRRR 18Z 20260506",
                "status": "complete",
                "readyForecastHours": list(range(49)),
                "requestedForecastHours": list(range(49)),
                "model": {"active": True},
            }), encoding="utf-8")
            (complete_hour_dir / "probability_tile.json").write_text(json.dumps({"forecastHour": 6}), encoding="utf-8")
            from backend import server

            with (
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", current_dir),
                patch.object(server, "INCREMENTAL_COMPLETE_ARTIFACT_DIR", complete_dir),
                patch.dict(os.environ, {"AUTOOUTLOOK_ARTIFACT_BUCKET": ""}),
            ):
                client = server.app.test_client()
                index_response = client.get("/api/outlook/incremental")
                tile_response = client.get("/api/outlook/incremental/hour/6/probability-tile")

            self.assertEqual(index_response.status_code, 200)
            self.assertEqual(index_response.get_json()["cycle"], "HRRR 18Z 20260506")
            self.assertEqual(tile_response.status_code, 200)
            self.assertEqual(tile_response.get_json()["forecastHour"], 6)

    def test_server_can_serve_forecast_from_incremental_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            hour_dir = artifact_dir / "hours" / "f00"
            hour_dir.mkdir(parents=True)
            (artifact_dir / "index.json").write_text(json.dumps({
                "cycle": "HRRR 00Z 20260505",
                "cycleTimeISO": "2026-05-05T00:00:00Z",
                "status": "complete",
                "readyForecastHours": [0],
                "model": {"active": True, "version": "unit", "featureSchemaHash": "unit"},
            }), encoding="utf-8")
            (hour_dir / "metadata.json").write_text(json.dumps({
                "forecastHour": 0,
                "validTimeISO": "2026-05-05T00:00:00Z",
                "categoryCounts": {"NONE": 10, "TSTM": 100, "MRGL": 120},
                "region": {
                    "label": "Exact coordinate sample",
                    "centerLat": 31.25,
                    "centerLon": -88.75,
                    "bbox": [-90.0, 30.0, -87.5, 32.5],
                    "states": [],
                },
                "ingredients": {
                    "mlcape": 2222.0,
                    "mucape": 2666.0,
                    "sbcape": 2444.0,
                    "cin": -77.0,
                    "sfcDewpointF": 70.0,
                    "pwatIn": 1.62,
                    "lclM": 980.0,
                    "moistureDepthM": 2430.0,
                    "srh01": 134.0,
                    "srh03": 228.0,
                    "shear06Kt": 47.0,
                    "stormRelWindKt": 23.5,
                    "frontSignal": "strong",
                    "initiationConf": 1.0,
                    "stormMode": "linear",
                    "capStrength": "weak",
                    "stp": 1.4,
                    "scp": 3.2,
                    "ehi": 1.8,
                    "ship": 1.1,
                    "tornadoComposite": 2.0,
                },
                "probabilityStats": {
                    "categoryConsistencyProbabilityMax": {
                        "tornado": 0.02,
                        "hail": 0.12,
                        "wind": 0.08,
                    },
                },
            }), encoding="utf-8")
            (hour_dir / "upper_air_overlay.json").write_text(json.dumps({
                "upperAirLines": [{
                    "level": "500mb",
                    "value": 5700,
                    "coords": [[-101, 34], [-100, 35], [-99, 36], [-98, 37], [-97, 38], [-96, 39], [-95, 40], [-94, 41]],
                }],
                "upperAirVectors": [{
                    "level": "500mb",
                    "lon": -99,
                    "lat": 36,
                    "uKt": 30,
                    "vKt": -10,
                    "speedKt": 31.6,
                }],
                "metadata": {
                    "domain": "CONUS",
                    "level": "500mb",
                    "hasHeightContours": True,
                    "hasWindVectors": True,
                    "heightContourCount": 1,
                    "windVectorCount": 1,
                },
            }), encoding="utf-8")
            (hour_dir / "risk_polygons.geojson").write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"category": "MRGL"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-100, 35], [-98, 35], [-98, 37], [-100, 37], [-100, 35]]],
                    },
                }],
            }), encoding="utf-8")
            from backend import server

            with (
                patch.object(server, "INCREMENTAL_ARTIFACT_DIR", artifact_dir),
                patch.dict(os.environ, {
                    "AUTOOUTLOOK_FORECAST_SOURCE": "artifacts",
                    "AUTOOUTLOOK_ARTIFACT_BUCKET": "",
                }),
                patch.object(server, "build_bundle", side_effect=AssertionError("should not build live bundle")),
            ):
                client = server.app.test_client()
                response = client.get("/api/forecast")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["cycle"], "HRRR 00Z 20260505")
            self.assertEqual(payload["providerId"], "backend")
            self.assertEqual(payload["hours"][0]["forecastHour"], 0)
            self.assertEqual(payload["hours"][0]["mlHazards"]["hail"], 0.12)
            self.assertEqual(payload["hours"][0]["region"]["label"], "Exact coordinate sample")
            self.assertEqual(payload["hours"][0]["region"]["centerLat"], 31.25)
            self.assertEqual(payload["hours"][0]["region"]["centerLon"], -88.75)
            self.assertEqual(payload["hours"][0]["ingredients"]["mlcape"], 2222.0)
            self.assertEqual(payload["hours"][0]["ingredients"]["shear06Kt"], 47.0)
            self.assertEqual(payload["hours"][0]["ingredients"]["stormMode"], "discrete")
            self.assertEqual(payload["hours"][0]["ingredients"]["capStrength"], "moderate")
            self.assertLess(payload["hours"][0]["ingredients"]["initiationConf"], 1.0)
            self.assertEqual(payload["hours"][0]["upperAirOverlay"]["domain"], "CONUS")
            self.assertTrue(payload["hours"][0]["upperAirOverlay"]["hasHeightContours"])
            self.assertTrue(payload["hours"][0]["upperAirOverlay"]["hasWindVectors"])
            self.assertEqual(len(payload["hours"][0]["upperAirLines"]), 1)
            self.assertEqual(len(payload["hours"][0]["upperAirVectors"]), 1)




if __name__ == "__main__":
    unittest.main()
