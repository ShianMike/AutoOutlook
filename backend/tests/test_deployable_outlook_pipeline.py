from __future__ import annotations

import json
import argparse
import os
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
from backend.hrrr_filter import _messages_to_fields
from backend.hrrr_selected import (
    REQUIRED_HRRR_TERMS,
    HrrrCycle,
    HrrrCycleDetection,
    SelectedHrrrValidationError,
    _fetch_range,
    _request_with_backoff,
    descriptor_matches_selected,
    downsample_hrrr_grid,
    latest_available_hrrr_cycle_with_metadata,
    parse_idx,
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
    _hydrate_incremental_artifacts_from_gcs,
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

    def request(self, method: str, url: str, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


class DeployableOutlookPipelineTests(unittest.TestCase):
    def test_selected_hrrr_terms_filter_only_requested_records(self) -> None:
        idx_text = "\n".join([
            "1:0:d=2024050412:CAPE:surface:anl:",
            "2:100:d=2024050412:REFC:entire atmosphere:anl:",
            "3:200:d=2024050412:HLCY:3000-0 m above ground:anl:",
            "4:300:d=2024050412:TMP:850 mb:anl:",
            "5:400:d=2024050412:VGRD:500 mb:anl:",
        ])
        records = parse_idx(idx_text)

        self.assertTrue(descriptor_matches_selected(records[0][2]))
        self.assertFalse(descriptor_matches_selected(records[1][2]))
        self.assertEqual(selected_ranges(records, 500), [(0, 99), (200, 299), (400, 499)])

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
        validate_decoded_hrrr_fields(ds_lats, ds_lons, ds_fields)

        self.assertEqual(ds_lats.shape, (2,))
        self.assertEqual(ds_lons.shape, (3,))
        self.assertEqual(ds_fields["cape"].shape, (2, 3))

    def test_hrrr_validation_rejects_implausible_required_fields(self) -> None:
        lats = np.linspace(25.0, 50.0, 5)
        lons = np.linspace(-125.0, -70.0, 5)
        fields = small_fields()
        fields["t2m"] = np.full((5, 5), 999.0)

        with self.assertRaises(SelectedHrrrValidationError):
            validate_decoded_hrrr_fields(lats, lons, fields)

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

        self.assertEqual(features.shape, (5, 5))
        self.assertEqual(features.matrix.shape, (25, 13))
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
        lats = np.linspace(25.5, 27.5, 5)
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
        lons = np.linspace(-83.0, -81.0, 5)

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
        lats = np.linspace(27.0, 28.4, 5)
        lons = np.linspace(-97.8, -96.0, 5)

        processed = postprocess_category_grid(category_grid, probabilities, features, lats, lons)

        self.assertTrue(np.all(processed.category_grid == SPC_RISK_LABELS.index("NONE")))
        self.assertGreater(processed.report["downgradedCells"]["southTexasGulfCoast"], 0)

    def test_postprocess_caps_texas_mexico_border_corridor_to_mrgl(self) -> None:
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
        lats = np.linspace(30.8, 32.1, 5)
        lons = np.linspace(-99.0, -97.0, 5)

        processed = postprocess_category_grid(category_grid, probabilities, features, lats, lons)

        self.assertTrue(np.all(processed.category_grid == SPC_RISK_LABELS.index("MRGL")))
        self.assertGreater(processed.report["downgradedCells"]["texasMexicoBorder"], 0)

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

    def test_offshore_probability_suppression_covers_south_texas_gulf_coast(self) -> None:
        probabilities = {
            "tornado": np.full((3, 3), 0.10),
            "hail": np.full((3, 3), 0.30),
            "wind": np.full((3, 3), 0.30),
        }
        lats = np.linspace(27.6, 28.3, 3)
        lons = np.linspace(-97.5, -96.2, 3)

        capped = apply_offshore_probability_suppression(probabilities, lats, lons)

        self.assertTrue(np.all(capped.probabilities["tornado"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["hail"] == 0.0))
        self.assertTrue(np.all(capped.probabilities["wind"] == 0.0))
        self.assertGreater(capped.report["offshoreProbabilitySuppressedCells"]["southTexasGulfCoast"], 0)

    def test_probability_tile_suppresses_strict_zone_by_tile_center(self) -> None:
        lats = np.linspace(28.2, 28.8, 3)
        lons = np.linspace(-98.0, -97.4, 3)
        category = np.full((3, 3), SPC_RISK_LABELS.index("MRGL"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((3, 3), 0.04),
            "hail": np.full((3, 3), 0.12),
            "wind": np.full((3, 3), 0.12),
        }

        tile = probability_tile(lats, lons, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=3)

        self.assertEqual(tile["categoryLabel"], [["NONE"]])
        self.assertEqual(tile["probabilities"]["tornado"], [[0.0]])
        self.assertEqual(tile["probabilities"]["hail"], [[0.0]])
        self.assertEqual(tile["probabilities"]["wind"], [[0.0]])

    def test_probability_tile_caps_texas_mexico_border_to_mrgl(self) -> None:
        lats = np.linspace(30.8, 32.1, 3)
        lons = np.linspace(-99.0, -97.0, 3)
        category = np.full((3, 3), SPC_RISK_LABELS.index("SLGT"), dtype=np.int16)
        probabilities = {
            "tornado": np.full((3, 3), 0.05),
            "hail": np.full((3, 3), 0.15),
            "wind": np.full((3, 3), 0.15),
        }

        tile = probability_tile(lats, lons, probabilities, category, 0, "2024-05-04T12:00:00Z", stride=3)

        self.assertEqual(tile["categoryLabel"], [["MRGL"]])
        self.assertEqual(tile["probabilities"]["tornado"], [[0.049]])
        self.assertEqual(tile["probabilities"]["hail"], [[0.149]])
        self.assertEqual(tile["probabilities"]["wind"], [[0.149]])

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

    def test_risk_polygon_display_bands_have_metric_gaps(self) -> None:
        lats = np.linspace(28.0, 40.0, 100)
        lons = np.linspace(-104.0, -86.0, 120)
        category = np.zeros((100, 120), dtype=np.int16)
        category[12:88, 10:110] = SPC_RISK_LABELS.index("TSTM")
        category[26:74, 28:92] = SPC_RISK_LABELS.index("MRGL")
        category[40:60, 45:75] = SPC_RISK_LABELS.index("SLGT")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        features = {feature["properties"]["category"]: feature for feature in geojson["features"]}
        tstm = projected_geojson_geometry(features["TSTM"]["geometry"])
        mrgl = projected_geojson_geometry(features["MRGL"]["geometry"])
        slgt = projected_geojson_geometry(features["SLGT"]["geometry"])

        self.assertGreaterEqual(tstm.distance(mrgl), 12_000.0)
        self.assertGreaterEqual(mrgl.distance(slgt), 12_000.0)
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayGeometry"], "band_with_metric_gap")
        self.assertEqual(features["TSTM"]["properties"]["vectorization"]["displayBandGapKm"], 15.0)

    def test_risk_polygon_display_bands_add_minimum_lower_support_width(self) -> None:
        lats = np.linspace(28.0, 40.0, 100)
        lons = np.linspace(-104.0, -86.0, 120)
        category = np.zeros((100, 120), dtype=np.int16)
        category[28:72, 34:86] = SPC_RISK_LABELS.index("ENH")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=10)
        features = {feature["properties"]["category"]: feature for feature in geojson["features"]}
        tstm = projected_geojson_geometry(features["TSTM"]["geometry"])
        mrgl = projected_geojson_geometry(features["MRGL"]["geometry"])
        slgt = projected_geojson_geometry(features["SLGT"]["geometry"])
        enh = projected_geojson_geometry(features["ENH"]["geometry"])

        mrgl_support = mrgl.buffer(50_000.0, quad_segs=8).difference(mrgl.buffer(20_000.0, quad_segs=8))
        slgt_support = slgt.buffer(45_000.0, quad_segs=8).difference(slgt.buffer(20_000.0, quad_segs=8))
        enh_support = enh.buffer(40_000.0, quad_segs=8).difference(enh.buffer(20_000.0, quad_segs=8))

        self.assertGreater(area_coverage_fraction(tstm, mrgl_support), 0.40)
        self.assertGreater(area_coverage_fraction(mrgl, slgt_support), 0.40)
        self.assertGreater(area_coverage_fraction(slgt, enh_support), 0.40)
        self.assertGreaterEqual(features["TSTM"]["properties"]["vectorization"]["displayMinimumSupportKm"], 45.0)
        self.assertGreaterEqual(features["MRGL"]["properties"]["vectorization"]["displayMinimumSupportKm"], 40.0)
        self.assertGreaterEqual(features["SLGT"]["properties"]["vectorization"]["displayMinimumSupportKm"], 35.0)

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
        self.assertEqual(tstm["properties"]["vectorization"]["displayGeometry"], "band_with_metric_gap")

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

    def test_hazard_probability_display_bands_have_metric_gaps(self) -> None:
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

        self.assertGreaterEqual(hail_5.distance(hail_15), 12_000.0)
        self.assertGreaterEqual(hail_15.distance(hail_30), 12_000.0)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayGeometry"], "band_with_metric_gap")
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayBandGapKm"], 15.0)

    def test_hazard_probability_display_bands_add_minimum_lower_support_width(self) -> None:
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
        support = hail_15.buffer(50_000.0, quad_segs=8).difference(hail_15.buffer(20_000.0, quad_segs=8))

        self.assertGreater(area_coverage_fraction(hail_5, support), 0.40)
        self.assertEqual(hail["5%"]["properties"]["vectorization"]["displayBandGapKm"], 15.0)
        self.assertGreaterEqual(hail["5%"]["properties"]["vectorization"]["displayMinimumSupportKm"], 45.0)

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
                np.linspace(25.5, 27.5, shape[0]),
                np.linspace(-94.0, -90.0, shape[1]),
            ),
            "southTexasGulfCoast": (
                np.linspace(27.2, 28.2, shape[0]),
                np.linspace(-97.6, -96.4, shape[1]),
            ),
            "floridaGulf": (
                np.linspace(24.2, 25.0, shape[0]),
                np.linspace(-83.0, -81.0, shape[1]),
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

            result = _publish_incremental_artifacts_to_gcs(output_dir, index, [0], "bucket", "prod/artifacts")

        current_hour_key = "prod/artifacts/latest_incremental/hours/f00/probability_tile.json"
        current_index_key = "prod/artifacts/latest_incremental/index.json"
        complete_hour_key = "prod/artifacts/latest_incremental_complete/hours/f00/probability_tile.json"
        complete_index_key = "prod/artifacts/latest_incremental_complete/index.json"
        self.assertEqual(result["currentFiles"], 5)
        self.assertEqual(result["completeFiles"], 5)
        self.assertIn(current_hour_key, uploads)
        self.assertIn(complete_hour_key, uploads)
        self.assertLess(uploads.index(current_hour_key), uploads.index(current_index_key))
        self.assertLess(uploads.index(complete_hour_key), uploads.index(complete_index_key))

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

        self.assertEqual(result["currentFiles"], 2)
        self.assertEqual(result["completeFiles"], 1)

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
