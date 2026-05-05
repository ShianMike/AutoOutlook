from __future__ import annotations

import json
import argparse
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.bundle_builder import HGT500_CONTOUR_LEVELS, _hgt500_lines_from_field, _wind500_vectors_from_fields, fetch_full_conus_500mb_overlay
from backend.hrrr_filter import _messages_to_fields
from backend.hrrr_selected import (
    REQUIRED_HRRR_TERMS,
    HrrrCycle,
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
    category_grid_from_probabilities,
    gridded_features_from_fields,
    postprocess_category_grid,
    probability_tile,
    risk_polygons_from_grid,
)
from backend.ml.outlook_pipeline import (
    ALL_FORECAST_HOURS,
    PRODUCTION_FORECAST_HOURS,
    _publish_working_dir,
    resolve_forecast_hours,
    resolve_cli_forecast_hours,
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

    def test_probability_categories_use_spc_style_mdt_label(self) -> None:
        fields = small_fields((2, 3))
        fields["cape_mu"] = np.full((2, 3), 3200.0)
        fields["cape_ml"] = np.full((2, 3), 2400.0)
        fields["u500"] = np.full((2, 3), 42.0)
        fields["td2m"] = np.full((2, 3), 297.0)
        fields["srh01"] = np.full((2, 3), 260.0)
        fields["srh03"] = np.full((2, 3), 340.0)
        features = gridded_features_from_fields(fields, forecast_hour=0)
        probabilities = {
            "tornado": np.array([[0.00, 0.02, 0.05], [0.10, 0.15, 0.30]]),
            "hail": np.zeros((2, 3)),
            "wind": np.zeros((2, 3)),
        }

        categories = category_grid_from_probabilities(probabilities, features)

        self.assertEqual([[SPC_RISK_LABELS[int(v)] for v in row] for row in categories], [
            ["TSTM", "MRGL", "SLGT"],
            ["ENH", "MDT", "HIGH"],
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

        self.assertLessEqual(float(np.nanmax(capped.probabilities["tornado"])), 0.149)
        self.assertLessEqual(float(np.nanmax(capped.probabilities["hail"])), 0.44)
        self.assertLessEqual(float(np.nanmax(capped.probabilities["wind"])), 0.44)
        categories = category_grid_from_probabilities(capped.probabilities, features, candidate_model)
        self.assertTrue(np.all(categories <= SPC_RISK_LABELS.index("ENH")))

    def test_category_probability_ceiling_keeps_tiles_consistent_with_final_categories(self) -> None:
        probabilities = {
            "tornado": np.array([[0.30, 0.30, 0.30]]),
            "hail": np.array([[0.60, 0.60, 0.60]]),
            "wind": np.array([[0.60, 0.60, 0.60]]),
        }
        final_categories = np.array([[
            SPC_RISK_LABELS.index("TSTM"),
            SPC_RISK_LABELS.index("MRGL"),
            SPC_RISK_LABELS.index("SLGT"),
        ]], dtype=np.int16)

        capped = apply_category_probability_ceiling(probabilities, final_categories)

        self.assertLess(float(capped.probabilities["tornado"][0, 0]), 0.02)
        self.assertLess(float(capped.probabilities["hail"][0, 1]), 0.15)
        self.assertLess(float(capped.probabilities["wind"][0, 2]), 0.30)
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

    def test_risk_polygons_are_geojson_features(self) -> None:
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        category = np.ones((5, 5), dtype=int)
        category[1:4, 1:4] = SPC_RISK_LABELS.index("MRGL")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=3)

        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertTrue(any(feature["properties"]["category"] == "MRGL" for feature in geojson["features"]))
        tstm_cells = [
            feature["properties"]["cellCount"]
            for feature in geojson["features"]
            if feature["properties"]["category"] == "TSTM"
        ]
        self.assertTrue(tstm_cells)
        self.assertLessEqual(max(tstm_cells), 16)
        for feature in geojson["features"]:
            ring = feature["geometry"]["coordinates"][0]
            area = 0.0
            for idx, (x0, y0) in enumerate(ring[:-1]):
                x1, y1 = ring[idx + 1]
                area += x0 * y1 - x1 * y0
            self.assertLessEqual(area / 2.0, 0.0)

    def test_probability_tile_downsampling_preserves_block_max_categories_and_probabilities(self) -> None:
        lats = np.linspace(30.0, 33.0, 4)
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

        def fake_detect(_session, _now):
            return cycle

        def fake_fetch(ref, _session):
            if ref.forecast_hour == 1:
                raise RuntimeError("missing hour")
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
            self.assertTrue((output_dir / "hours" / "f00" / "risk_polygons.geojson").exists())
            self.assertTrue((output_dir / "hours" / "f00" / "probability_tile.json").exists())
            self.assertEqual(
                json.loads((output_dir / "hours" / "f01" / "metadata.json").read_text(encoding="utf-8"))["status"],
                "failed",
            )

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
            self.assertTrue((output_dir / "hours" / "f01" / "probability_tile.json").exists())

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
            artifact_dir = Path(tmp)
            (artifact_dir / "metadata.json").write_text(json.dumps({"cycle": "unit"}), encoding="utf-8")
            from backend import server

            with patch.object(server, "ARTIFACT_DIR", artifact_dir):
                client = server.app.test_client()
                response = client.get("/api/outlook/latest")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["cycle"], "unit")

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

            with patch.object(server, "INCREMENTAL_ARTIFACT_DIR", artifact_dir):
                client = server.app.test_client()
                index_response = client.get("/api/outlook/incremental")
                tile_response = client.get("/api/outlook/incremental/hour/0/probability-tile")
                meta_response = client.get("/api/outlook/incremental/hour/0/metadata")

            self.assertEqual(index_response.status_code, 200)
            self.assertEqual(tile_response.status_code, 200)
            self.assertEqual(meta_response.status_code, 200)
            self.assertEqual(tile_response.get_json()["forecastHour"], 0)


if __name__ == "__main__":
    unittest.main()
