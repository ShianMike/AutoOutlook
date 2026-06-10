from __future__ import annotations

import json
import pickle
import random
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend import metpy_diagnostics as diag
from backend.bundle_builder import _classify_cap, _classify_storm_mode, _ingredients_at_point
from backend.ml.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, HAZARD_KEYS, feature_row, feature_schema_hash
from backend.ml.gather_archive import (
    NEGATIVE_POINTS,
    SPC_DAILY_REPORT_BASE_URL,
    SPC_INDEX_URL,
    _candidate_points,
    _dedupe_feature_label_rows,
    _ingredients_at,
    _parse_spc_datetime,
    _parse_spc_daily_report_csv,
    iter_hrrr_refs,
    load_spc_reports,
)
from backend.ml.add_intensity_labels import enrich_frame_with_intensity_labels
from backend.ml.inference import (
    MIN_XGBOOST_TRAINING_ROWS,
    model_status,
    predict_ml_hazard_matrix,
    predict_ml_hazards,
    reset_model_cache,
)
from backend.ml.merge_archive_training_data import merge_archive
from backend.ml.reports import intensity_labels_for_sample, labels_for_sample, report_matches_sample
from backend.ml.spc_categories import (
    category_conversion_from_probability_and_cig,
    category_from_probability_and_cig,
    category_ordinal,
    normalize_category,
)
from backend.ml.validate_models import (
    average_precision_score,
    category_for_probability,
    metrics_for_hazard,
    roc_auc_score,
)


class DummyProbabilityModel:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, x):  # noqa: ANN001
        return [[1.0 - self.probability, self.probability] for _ in range(len(x))]


def fake_joblib_load(path: str | Path) -> DummyProbabilityModel:
    with Path(path).open("rb") as fh:
        return pickle.load(fh)


class FakeHttpResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpSession:
    def __init__(self, responses: dict[str, FakeHttpResponse]) -> None:
        self.responses = responses
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int = 30) -> FakeHttpResponse:  # noqa: ARG002
        self.requested_urls.append(url)
        return self.responses.get(url, FakeHttpResponse("", 404))


class MlPipelineTests(unittest.TestCase):
    def test_feature_schema_order_and_hash_are_deterministic(self) -> None:
        ingredients = {
            "mlcape": 1500,
            "mucape": 2200,
            "sbcape": 1100,
            "cape3km": 450,
            "cape180": 1750,
            "cin": -75,
            "cinSb": -40,
            "cinMl": -75,
            "cinMu": -90,
            "cin180": -70,
            "sfcDewpointF": 68,
            "pwatIn": 1.35,
            "lclM": 900,
            "moistureDepthM": 2200,
            "srh01": 160,
            "srh03": 260,
            "shear06Kt": 45,
            "stormRelWindKt": 24,
            "initiationConf": 0.72,
            "frontSignal": "moderate",
            "capStrength": "weak",
            "stormMode": "discrete",
            "stp": 2.3,
            "scp": 5.1,
            "ehi": 1.7,
            "ship": 1.2,
            "tornadoComposite": 0.54,
            "lapseRate700500CPerKm": 7.0,
            "freezingLevelM": 3200.0,
            "surfacePressurePa": 100500.0,
        }
        row = feature_row(ingredients, 18)

        self.assertEqual(tuple(row.keys()), FEATURE_NAMES)
        self.assertEqual(len(row), len(FEATURE_NAMES))
        self.assertEqual(row["forecastHour"], 18.0)
        self.assertNotIn("initiationConf", row)
        self.assertEqual(row["cape3km"], 450.0)
        self.assertEqual(row["cape180"], 1750.0)
        self.assertEqual(row["cinSb"], -40.0)
        self.assertEqual(row["cinMl"], -75.0)
        self.assertEqual(row["cinMu"], -90.0)
        self.assertEqual(row["cin180"], -70.0)
        self.assertEqual(row["stp"], 2.3)
        self.assertEqual(row["scp"], 5.1)
        self.assertEqual(row["ehi"], 1.7)
        self.assertEqual(row["ship"], 1.2)
        self.assertEqual(row["lapseRate700500CPerKm"], 7.0)
        self.assertEqual(row["freezingLevelM"], 3200.0)
        self.assertEqual(row["surfacePressurePa"], 100500.0)
        self.assertNotIn("tornadoComposite", row)
        self.assertNotIn("frontSignalOrdinal", row)
        self.assertFalse(any(name.startswith("stormMode") for name in row))
        self.assertEqual(feature_schema_hash(), feature_schema_hash())

    def test_report_matching_uses_hazard_distance_and_utc_hour_window(self) -> None:
        sample_time = datetime(2024, 5, 6, 21, tzinfo=timezone.utc)
        report = {
            "hazard": "tornado",
            "time": datetime(2024, 5, 6, 21, 30, tzinfo=timezone.utc),
            "lat": 35.2,
            "lon": -97.5,
        }

        self.assertTrue(report_matches_sample(report, sample_time, 35.22, -97.44, "tornado"))
        self.assertFalse(report_matches_sample(report, sample_time, 35.22, -97.44, "hail"))
        self.assertFalse(report_matches_sample(report, sample_time, 34.0, -99.5, "tornado"))
        self.assertFalse(report_matches_sample(report, datetime(2024, 5, 6, 22, tzinfo=timezone.utc), 35.22, -97.44, "tornado"))

    def test_labels_for_sample_maps_each_hazard(self) -> None:
        sample_time = datetime(2024, 5, 6, 21, tzinfo=timezone.utc)
        reports = [
            {"hazard": "tornado", "time": sample_time, "lat": 35.2, "lon": -97.5},
            {"hazard": "wind", "time": sample_time, "lat": 35.2, "lon": -97.5},
        ]

        self.assertEqual(
            labels_for_sample(reports, sample_time, 35.22, -97.44),
            {"tornado": 1, "hail": 0, "wind": 1},
        )

    def test_daily_report_parser_preserves_normalized_magnitude_fields(self) -> None:
        report_date = datetime(2024, 5, 6, tzinfo=timezone.utc).date()

        hail = _parse_spc_daily_report_csv(
            "Time,Location,Lat,Lon,Mag\n2130,Norman,35.2,-97.5,200\n",
            "hail",
            report_date,
            "https://example.test/hail.csv",
        )
        wind = _parse_spc_daily_report_csv(
            "Time,Location,Lat,Lon,Speed,Unit\n2145,Norman,35.2,-97.5,70,mph\n",
            "wind",
            report_date,
            "https://example.test/wind.csv",
        )
        tornado = _parse_spc_daily_report_csv(
            "Time,Location,Lat,Lon,F_Scale\n2155,Norman,35.2,-97.5,EF2\n",
            "tornado",
            report_date,
            "https://example.test/torn.csv",
        )

        self.assertEqual(hail[0]["hailSizeIn"], 2.0)
        self.assertAlmostEqual(wind[0]["windSpeedKt"], 60.82832, places=4)
        self.assertEqual(tornado[0]["efScale"], 2)

    def test_intensity_labels_for_sample_maps_cig_threshold_targets(self) -> None:
        sample_time = datetime(2024, 5, 6, 21, tzinfo=timezone.utc)
        reports = [
            {"hazard": "tornado", "time": sample_time, "lat": 35.2, "lon": -97.5, "efScale": 3},
            {"hazard": "hail", "time": sample_time, "lat": 35.2, "lon": -97.5, "hailSizeIn": 3.75},
            {"hazard": "wind", "time": sample_time, "lat": 35.2, "lon": -97.5, "windSpeedKt": 78.0},
        ]

        labels = intensity_labels_for_sample(reports, sample_time, 35.22, -97.44)

        self.assertEqual(labels["tornado_ef2_plus"], 1)
        self.assertEqual(labels["tornado_ef3_plus"], 1)
        self.assertEqual(labels["hail_2in_plus"], 1)
        self.assertEqual(labels["hail_3_5in_plus"], 1)
        self.assertEqual(labels["wind_56kt_plus"], 1)
        self.assertEqual(labels["wind_65kt_plus"], 1)
        self.assertEqual(labels["wind_74kt_plus"], 1)
        self.assertEqual(labels["wind_83kt_plus"], 0)

    def test_enrich_frame_with_intensity_labels_adds_training_targets(self) -> None:
        pd = __import__("pandas")
        sample_time = datetime(2024, 5, 6, 21, tzinfo=timezone.utc)
        frame = pd.DataFrame(
            [
                {
                    "runDate": "20240506",
                    "validTimeISO": "2024-05-06T21:00:00Z",
                    "sampleLat": 35.22,
                    "sampleLon": -97.44,
                    "label_hail": 1,
                }
            ]
        )
        reports = [
            {
                "hazard": "hail",
                "time": sample_time.replace(minute=20),
                "lat": 35.2,
                "lon": -97.5,
                "hailSizeIn": 2.25,
            }
        ]

        enriched = enrich_frame_with_intensity_labels(frame, reports)

        self.assertEqual(int(enriched.loc[0, "label_hail_2in_plus"]), 1)
        self.assertEqual(int(enriched.loc[0, "label_hail_3_5in_plus"]), 0)
        self.assertEqual(int(enriched.loc[0, "label_tornado_ef2_plus"]), 0)

    def test_spc_datetime_parser_accepts_colon_and_hhmm_times(self) -> None:
        row_with_colons = {"yr": 2024, "mo": 5, "dy": 8, "time": "21:32:00"}
        row_with_hhmm = {"yr": 2024, "mo": 5, "dy": 8, "time": 2132}

        expected = datetime(2024, 5, 8, 21, 32, tzinfo=timezone.utc)
        self.assertEqual(_parse_spc_datetime(row_with_colons, "yr", "mo", "dy", "time"), expected)
        self.assertEqual(_parse_spc_datetime(row_with_hhmm, "yr", "mo", "dy", "time"), expected)

    def test_archive_ingredients_convert_composites_to_scalars(self) -> None:
        lats = np.array([35.0, 36.0])
        lons = np.array([-98.0, -97.0])
        fields = {
            "cape": np.array([[1000.0, 1200.0], [1500.0, 1800.0]]),
            "cape_ml": np.array([[850.0, 1000.0], [1300.0, 1500.0]]),
            "cape_mu": np.array([[1200.0, 1500.0], [1800.0, 2200.0]]),
            "cin_ml": np.array([[-50.0, -75.0], [-100.0, -125.0]]),
            "td2m": np.array([[291.0, 292.0], [293.0, 294.0]]),
            "t2m": np.array([[299.0, 300.0], [301.0, 302.0]]),
            "pwat": np.array([[22.0, 24.0], [26.0, 28.0]]),
            "u500": np.array([[25.0, 28.0], [30.0, 35.0]]),
            "v500": np.array([[8.0, 9.0], [10.0, 11.0]]),
            "u10": np.array([[5.0, 6.0], [7.0, 8.0]]),
            "v10": np.array([[1.0, 1.0], [2.0, 2.0]]),
            "srh01": np.array([[80.0, 100.0], [120.0, 140.0]]),
            "srh03": np.array([[140.0, 160.0], [180.0, 200.0]]),
        }

        ingredients = _ingredients_at(lats, lons, fields, 35.2, -97.8)

        self.assertIsInstance(ingredients["stp"], float)
        self.assertIsInstance(ingredients["scp"], float)
        self.assertIsInstance(ingredients["tornadoComposite"], float)

    def test_spc_composite_shear_and_cin_terms_are_unit_correct(self) -> None:
        weak_shear = diag.composites(
            cape=np.array([1500.0]),
            mlcape=np.array([1500.0]),
            mucape=np.array([1000.0]),
            shear_kt=np.array([15.0]),
            srh01=np.array([150.0]),
            srh03=np.array([50.0]),
            cin=np.array([-25.0]),
            td2m_K=np.array([293.15]),
        )
        self.assertEqual(float(weak_shear["stp"][0]), 0.0)
        self.assertEqual(float(weak_shear["scp"][0]), 0.0)

        inhibited = diag.composites(
            cape=np.array([1500.0]),
            mlcape=np.array([1500.0]),
            mucape=np.array([1000.0]),
            shear_kt=np.array([40.0]),
            srh01=np.array([150.0]),
            srh03=np.array([50.0]),
            cin=np.array([-150.0]),
            td2m_K=np.array([293.15]),
        )
        self.assertGreater(float(inhibited["stp"][0]), 0.25)
        self.assertLess(float(inhibited["stp"][0]), 0.45)

    def test_fixed_layer_stp_matches_spc_term_clipping(self) -> None:
        result = diag.composites(
            cape=np.array([3000.0]),
            mlcape=np.array([2500.0]),
            mucape=np.array([2800.0]),
            shear_kt=np.array([30.0 * diag.KT_PER_MS]),
            srh01=np.array([225.0]),
            srh03=np.array([300.0]),
            cin=np.array([-125.0]),
            td2m_K=np.array([293.15]),
            t2m_K=np.array([301.15]),
            lcl_m=np.array([1500.0]),
        )

        expected = 1.5 * 0.5 * 1.5 * 1.5 * 0.5
        self.assertAlmostEqual(float(result["stp"][0]), expected, places=6)

    def test_scp_uses_shear_gate_and_mu_cin_term(self) -> None:
        weak_shear = diag.composites(
            cape=np.array([2000.0]),
            mucape=np.array([2000.0]),
            shear_kt=np.array([9.5 * diag.KT_PER_MS]),
            srh01=np.array([120.0]),
            srh03=np.array([200.0]),
            cin=np.array([-20.0]),
            td2m_K=np.array([293.15]),
        )
        self.assertEqual(float(weak_shear["scp"][0]), 0.0)

        inhibited = diag.composites(
            cape=np.array([2000.0]),
            mucape=np.array([2000.0]),
            shear_kt=np.array([20.0 * diag.KT_PER_MS]),
            srh01=np.array([120.0]),
            srh03=np.array([200.0]),
            cin=np.array([-20.0]),
            cin_mu=np.array([-80.0]),
            td2m_K=np.array([293.15]),
        )
        self.assertAlmostEqual(float(inhibited["scp"][0]), 4.0, places=6)

    def test_ship_matches_spc_formula_when_required_fields_are_supplied(self) -> None:
        td_k = 293.15
        surface_pressure_pa = 100000.0
        t500_k = 253.15
        shear_ms = 20.0
        result = diag.composites(
            cape=np.array([2600.0]),
            mucape=np.array([2600.0]),
            shear_kt=np.array([shear_ms * diag.KT_PER_MS]),
            srh01=np.array([100.0]),
            srh03=np.array([160.0]),
            cin=np.array([-20.0]),
            td2m_K=np.array([td_k]),
            t2m_K=np.array([293.15]),
            surface_pressure_pa=np.array([surface_pressure_pa]),
            t850_K=np.array([288.15]),
            t700_K=np.array([273.15]),
            t500_K=np.array([t500_k]),
            hgt850_m=np.array([1500.0]),
            hgt700_m=np.array([3000.0]),
            hgt500_m=np.array([5600.0]),
        )

        td_c = td_k - 273.15
        vapor_pressure_hpa = 6.112 * np.exp((17.67 * td_c) / (td_c + 243.5))
        mixing_ratio = 621.97 * vapor_pressure_hpa / ((surface_pressure_pa / 100.0) - vapor_pressure_hpa)
        expected = (
            2600.0
            * min(max(mixing_ratio, 11.0), 13.6)
            * (20.0 / 2.6)
            * 20.0
            * shear_ms
        ) / 42_000_000.0

        self.assertAlmostEqual(float(result["lapse_rate_700_500"][0]), 20.0 / 2.6, places=6)
        self.assertAlmostEqual(float(result["freezing_level_m"][0]), 3000.0, places=6)
        self.assertAlmostEqual(float(result["mixing_ratio_gkg"][0]), mixing_ratio, places=6)
        self.assertAlmostEqual(float(result["ship"][0]), expected, places=6)
        self.assertEqual(float(result["ship_available"][0]), 1.0)

    def test_ship_is_zero_when_hail_growth_zone_fields_are_missing(self) -> None:
        result = diag.composites(
            cape=np.array([2600.0]),
            mucape=np.array([2600.0]),
            shear_kt=np.array([40.0]),
            srh01=np.array([100.0]),
            srh03=np.array([160.0]),
            cin=np.array([-20.0]),
            td2m_K=np.array([293.15]),
            surface_pressure_pa=np.array([100000.0]),
        )

        self.assertEqual(float(result["ship"][0]), 0.0)
        self.assertEqual(float(result["ship_available"][0]), 0.0)

    def test_forcing_cap_and_mode_formulas_do_not_confuse_shear_with_forcing(self) -> None:
        self.assertEqual(_classify_cap(-10), "none")
        self.assertEqual(_classify_cap(-30), "weak")
        self.assertEqual(_classify_cap(-80), "moderate")
        self.assertEqual(_classify_cap(-175), "strong")

        self.assertEqual(_classify_storm_mode(20, 250, "strong"), "multicell")
        self.assertEqual(_classify_storm_mode(45, 180, "none"), "discrete")
        self.assertEqual(_classify_storm_mode(45, 180, "strong", "frontal"), "mixed")
        self.assertEqual(_classify_storm_mode(45, 180, "strong", "dryline"), "discrete")
        self.assertEqual(_classify_storm_mode(53, 335, "strong"), "discrete")
        self.assertEqual(_classify_storm_mode(53, 144, "strong"), "mixed")

        composites = {"stp": 1.0, "scp": 2.0, "ehi": 1.0, "ship": 1.0, "tor_comp": 1.0}
        ingredients = _ingredients_at_point(
            surface_cape=3000.0,
            mlcape=2500.0,
            mucape=3200.0,
            surface_cin=-60.0,
            mlcin=-175.0,
            mucin=-90.0,
            cape3km=650.0,
            cape180=2700.0,
            cin180=-130.0,
            td2m_K=294.15,
            t2m_K=304.15,
            pwat_kg_m2=35.0,
            shear_kt=45.0,
            srh01=120.0,
            srh03=180.0,
            sr_wind_kt=22.0,
            composites=composites,
        )
        self.assertEqual(ingredients["frontSignal"], "none")
        self.assertEqual(ingredients["stormMode"], "discrete")
        self.assertLess(ingredients["initiationConf"], 0.35)

    def test_hrrr_ref_iterator_can_target_known_valid_hours(self) -> None:
        refs = list(iter_hrrr_refs(
            [2024],
            [5],
            [12, 18],
            [0, 4],
            run_dates=["20240521"],
            valid_hours=["2024052116", "2024052118"],
        ))

        self.assertEqual(
            [(ref.run_date, ref.run_cycle, ref.forecast_hour) for ref in refs],
            [("20240521", 12, 4), ("20240521", 18, 0)],
        )

    def test_candidate_points_can_reserve_negative_controls(self) -> None:
        sample_time = datetime(2024, 5, 21, 16, tzinfo=timezone.utc)
        reports = [
            {"hazard": "wind", "time": sample_time, "lat": 34.0 + idx * 0.2, "lon": -100.0 - idx * 0.2}
            for idx in range(20)
        ]
        points = _candidate_points(
            reports,
            sample_time,
            max_points=8,
            rng=random.Random(7),
            negative_points_per_hour=3,
        )
        negative_keys = {(round(lat * 10), round(lon * 10)) for lat, lon in NEGATIVE_POINTS}
        selected_negative_count = sum((round(lat * 10), round(lon * 10)) in negative_keys for lat, lon in points)

        self.assertGreaterEqual(selected_negative_count, 3)
        self.assertEqual(len(points), 8)

    def test_spc_report_loader_daily_fallback_uses_requested_run_dates(self) -> None:
        index_html = """
        <a href="2024_torn.csv">2024 tornado</a>
        <a href="2024_hail.csv">2024 hail</a>
        <a href="2024_wind.csv">2024 wind</a>
        """
        jan_torn_url = f"{SPC_DAILY_REPORT_BASE_URL}/250101_rpts_torn.csv"
        feb_wind_url = f"{SPC_DAILY_REPORT_BASE_URL}/260228_rpts_wind.csv"
        session = FakeHttpSession(
            {
                SPC_INDEX_URL: FakeHttpResponse(index_html),
                jan_torn_url: FakeHttpResponse("Time,Location,Lat,Lon\n2355,Norman,35.2,-97.4\n"),
                feb_wind_url: FakeHttpResponse("Time,Location,Lat,Lon\n0005,Memphis,35.1,-90.0\n"),
            }
        )

        reports = load_spc_reports(
            session,
            [2025, 2026],
            list(range(1, 13)),
            run_dates=["20250101", "20260228"],
        )

        self.assertEqual([report["hazard"] for report in reports], ["tornado", "wind"])
        self.assertEqual(reports[0]["time"], datetime(2025, 1, 1, 23, 55, tzinfo=timezone.utc))
        self.assertEqual(reports[1]["time"], datetime(2026, 3, 1, 0, 5, tzinfo=timezone.utc))
        requested = "\n".join(session.requested_urls)
        self.assertIn("250101_rpts_torn.csv", requested)
        self.assertIn("260228_rpts_wind.csv", requested)
        self.assertNotIn("260301", requested)

    def test_feature_label_row_dedupe_drops_duplicates(self) -> None:
        base_row = {
            "forecastHour": 12.0,
            "mlcape": 1600.0,
            "mucape": 2100.0,
            "sbcape": 1400.0,
            "cape3km": 450.0,
            "cape180": 1700.0,
            "cin": -65.0,
            "cinSb": -45.0,
            "cinMl": -65.0,
            "cinMu": -85.0,
            "cin180": -60.0,
            "sfcDewpointF": 66.0,
            "pwatIn": 1.4,
            "lclM": 1000.0,
            "moistureDepthM": 2200.0,
            "srh01": 130.0,
            "srh03": 210.0,
            "shear06Kt": 42.0,
            "stormRelWindKt": 23.0,
            "stp": 1.4,
            "scp": 3.6,
            "ehi": 1.2,
            "ship": 0.8,
            "lapseRate700500CPerKm": 6.8,
            "freezingLevelM": 3200.0,
            "surfacePressurePa": 100000.0,
            "label_tornado": 1,
            "label_hail": 0,
            "label_wind": 1,
            "runDate": "20240521",
        }
        rows = [base_row, dict(base_row), {**base_row, "runDate": "20240522", "label_hail": 1}]
        deduped, dropped = _dedupe_feature_label_rows(rows)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(dropped, 1)

    def test_merge_archive_prefers_lightning_rows_for_duplicate_keys(self) -> None:
        try:
            import pandas as pd
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"pandas/pyarrow unavailable: {exc}")

        base = {name: 1.0 for name in FEATURE_NAMES}
        base.update(
            {
                "runDate": "20230128",
                "runCycle": 0,
                "forecastHour": 12.0,
                "sampleLat": 35.0,
                "sampleLon": -97.0,
                "label_tornado": 0,
                "label_hail": 1,
                "label_wind": 0,
            }
        )
        raw_row = {**base, "hrrrLightningAvailable": False, "hrrrLtng": None, "hrrrLightningFieldCount": 0}
        lightning_row = {**base, "hrrrLightningAvailable": True, "hrrrLtng": 4.5, "hrrrLightningFieldCount": 1}
        next_row = {**base, "runDate": "20230129", "sampleLat": 36.0, "hrrrLightningAvailable": False}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_path = tmp_path / "raw.parquet"
            lightning_path = tmp_path / "lightning.parquet"
            output = tmp_path / "merged.parquet"
            summary = tmp_path / "summary.json"
            pd.DataFrame([raw_row, next_row]).to_parquet(raw_path, index=False)
            pd.DataFrame([lightning_row]).to_parquet(lightning_path, index=False)

            result = merge_archive([raw_path, lightning_path], output, summary)
            merged = pd.read_parquet(output)

        self.assertEqual(result["rowsBeforeDedupe"], 3)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["duplicatesDropped"], 1)
        duplicate_key = merged.loc[merged["runDate"] == "20230128"].iloc[0]
        self.assertTrue(bool(duplicate_key["hrrrLightningAvailable"]))
        self.assertEqual(float(duplicate_key["hrrrLtng"]), 4.5)

    def test_spc_probability_cig_category_tables_match_2026_rules(self) -> None:
        self.assertEqual(category_from_probability_and_cig("tornado", 0.60, 0), "ENH")
        self.assertEqual(category_from_probability_and_cig("tornado", 0.60, 1), "HIGH")
        self.assertEqual(category_from_probability_and_cig("tornado", 0.45, "CIG1"), "MDT")
        self.assertEqual(category_from_probability_and_cig("tornado", 0.30, "CIG2"), "HIGH")
        self.assertEqual(category_from_probability_and_cig("tornado", 0.15, "CIG3"), "MDT")
        self.assertEqual(category_from_probability_and_cig("tornado", 10, "CIG1"), "ENH")
        self.assertEqual(category_from_probability_and_cig("tornado", 0.05, "CIG2"), "ENH")
        self.assertEqual(category_from_probability_and_cig("tornado", 0.02, None), "MRGL")

        self.assertEqual(category_from_probability_and_cig("wind", 0.90, 0), "ENH")
        self.assertEqual(category_from_probability_and_cig("wind", 0.75, "CIG1"), "MDT")
        self.assertEqual(category_from_probability_and_cig("wind", 0.60, "CIG2"), "HIGH")
        self.assertEqual(category_from_probability_and_cig("wind", 0.45, "CIG3"), "HIGH")
        self.assertEqual(category_from_probability_and_cig("wind", 0.30, 0), "SLGT")
        self.assertEqual(category_from_probability_and_cig("wind", 0.15, "CIG2"), "ENH")
        self.assertEqual(category_from_probability_and_cig("wind", 0.05, "CIG1"), "MRGL")

        self.assertEqual(category_from_probability_and_cig("hail", 0.60, 0), "ENH")
        self.assertEqual(category_from_probability_and_cig("hail", 0.60, "CIG1"), "MDT")
        self.assertEqual(category_from_probability_and_cig("hail", 0.45, "CIG2"), "MDT")
        self.assertEqual(category_from_probability_and_cig("hail", 0.30, 0), "SLGT")
        self.assertEqual(category_from_probability_and_cig("hail", 0.15, "CIG2"), "ENH")
        self.assertEqual(category_from_probability_and_cig("hail", 0.05, None), "MRGL")

    def test_spc_probability_cig_not_used_cells_are_reported_as_clamps(self) -> None:
        tornado = category_conversion_from_probability_and_cig("tornado", 0.05, "CIG3")
        self.assertEqual(tornado.category, "ENH")
        self.assertTrue(tornado.clamped)
        self.assertEqual(tornado.reason, "cig_not_used_for_probability_row")

        wind = category_conversion_from_probability_and_cig("wind", 0.30, "CIG3")
        self.assertEqual(wind.category, "ENH")
        self.assertTrue(wind.clamped)
        self.assertEqual(wind.reason, "cig_not_used_for_probability_row")

        hail = category_conversion_from_probability_and_cig("hail", 0.45, "CIG3")
        self.assertEqual(hail.category, "MDT")
        self.assertTrue(hail.clamped)
        self.assertEqual(hail.reason, "cig_above_supported_range")

    def test_spc_category_labels_use_mdt_and_normalize_legacy_mod(self) -> None:
        self.assertEqual(normalize_category("MOD"), "MDT")
        self.assertEqual(category_ordinal("MOD"), category_ordinal("MDT"))
        self.assertLess(category_ordinal("ENH"), category_ordinal("MDT"))
        self.assertEqual(category_from_probability_and_cig("hail", 0.0, "CIG2"), "NONE")
        self.assertEqual(category_from_probability_and_cig("hail", 0.01, "CIG2"), "TSTM")

    def test_missing_model_artifacts_disable_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("backend.ml.inference.MODEL_DIR", Path(tmp)):
                reset_model_cache()
                self.assertFalse(model_status()["active"])
                self.assertIsNone(predict_ml_hazards({"mlcape": 1000}, 0))
                reset_model_cache()

    def test_present_model_artifacts_enable_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            metadata = {
                "version": "unit-test",
                "trainedAtISO": "2026-05-02T00:00:00Z",
                "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
                "featureSchemaHash": feature_schema_hash(),
                "featureNames": list(FEATURE_NAMES),
                "trainingRows": MIN_XGBOOST_TRAINING_ROWS,
            }
            (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            for hazard in HAZARD_KEYS:
                with (model_dir / f"{hazard}_xgb.joblib").open("wb") as fh:
                    pickle.dump(DummyProbabilityModel(0.2), fh)

            fake_joblib = types.SimpleNamespace(load=fake_joblib_load)
            with patch("backend.ml.inference.MODEL_DIR", model_dir), patch.dict(sys.modules, {"joblib": fake_joblib}):
                reset_model_cache()
                status = model_status()
                self.assertTrue(status["active"])
                self.assertEqual(status["featureSchemaHash"], feature_schema_hash())
                self.assertEqual(
                    predict_ml_hazards({"mlcape": 1000, "shear06Kt": 35}, 12),
                    {"tornado": 0.2, "hail": 0.2, "wind": 0.2},
                )
                reset_model_cache()

    def test_small_xgboost_artifacts_do_not_drive_live_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            metadata = {
                "version": "tiny-trial",
                "artifactType": "xgboost_joblib",
                "trainedAtISO": "2026-05-02T00:00:00Z",
                "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
                "featureSchemaHash": feature_schema_hash(),
                "featureNames": list(FEATURE_NAMES),
                "trainingRows": MIN_XGBOOST_TRAINING_ROWS - 1,
            }
            (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            for hazard in HAZARD_KEYS:
                with (model_dir / f"{hazard}_xgb.joblib").open("wb") as fh:
                    pickle.dump(DummyProbabilityModel(0.2), fh)

            fake_joblib = types.SimpleNamespace(load=fake_joblib_load)
            with patch("backend.ml.inference.MODEL_DIR", model_dir), patch.dict(sys.modules, {"joblib": fake_joblib}):
                reset_model_cache()
                status = model_status()
                self.assertFalse(status["active"])
                self.assertIn("below required", status["reason"])
                self.assertIsNone(predict_ml_hazards({"mlcape": 1000, "shear06Kt": 35}, 12))
                reset_model_cache()

    def test_experimental_only_artifacts_require_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            metadata = {
                "version": "tiny-trial",
                "artifactType": "xgboost_joblib",
                "trainedAtISO": "2026-05-02T00:00:00Z",
                "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
                "featureSchemaHash": feature_schema_hash(),
                "featureNames": list(FEATURE_NAMES),
                "trainingRows": MIN_XGBOOST_TRAINING_ROWS,
                "allowSmallTrainingSet": True,
                "datasetQuality": {
                    "experimentalOnly": True,
                    "trainingRows": MIN_XGBOOST_TRAINING_ROWS,
                    "minimumRecommendedRows": MIN_XGBOOST_TRAINING_ROWS,
                },
            }
            (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            for hazard in HAZARD_KEYS:
                with (model_dir / f"{hazard}_xgb.joblib").open("wb") as fh:
                    pickle.dump(DummyProbabilityModel(0.2), fh)

            fake_joblib = types.SimpleNamespace(load=fake_joblib_load)
            with patch("backend.ml.inference.MODEL_DIR", model_dir), patch.dict(sys.modules, {"joblib": fake_joblib}):
                reset_model_cache()
                status = model_status()
                self.assertFalse(status["active"])
                self.assertIn("experimental/demo-only", status["reason"])
                self.assertIsNone(predict_ml_hazards({"mlcape": 1000, "shear06Kt": 35}, 12))
                reset_model_cache()

    def test_older_xgboost_subset_schema_can_drive_current_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            model_features = (
                "forecastHour",
                "mlcape",
                "mucape",
                "sbcape",
                "cin",
                "sfcDewpointF",
                "pwatIn",
                "lclM",
                "moistureDepthM",
                "srh01",
                "srh03",
                "shear06Kt",
                "stormRelWindKt",
            )
            metadata = {
                "version": "legacy-candidate",
                "artifactType": "xgboost_joblib",
                "trainedAtISO": "2026-05-02T00:00:00Z",
                "featureSchemaVersion": "ml-features-v2-pure-ai",
                "featureSchemaHash": "legacy-v2-hash",
                "featureNames": list(model_features),
                "trainingRows": MIN_XGBOOST_TRAINING_ROWS,
                "datasetQuality": {
                    "experimentalOnly": False,
                    "trainingRows": MIN_XGBOOST_TRAINING_ROWS,
                    "minimumRecommendedRows": MIN_XGBOOST_TRAINING_ROWS,
                },
            }
            (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            for hazard in HAZARD_KEYS:
                with (model_dir / f"{hazard}_xgb.joblib").open("wb") as fh:
                    pickle.dump(DummyProbabilityModel(0.2), fh)

            fake_joblib = types.SimpleNamespace(load=fake_joblib_load)
            with patch("backend.ml.inference.MODEL_DIR", model_dir), patch.dict(sys.modules, {"joblib": fake_joblib}):
                reset_model_cache()
                status = model_status()
                self.assertTrue(status["active"])
                self.assertEqual(status["featureCompatibilityMode"], "runtime_subset_features")
                self.assertEqual(status["runtimeFeatureSchemaHash"], feature_schema_hash())
                self.assertEqual(
                    predict_ml_hazards({"mlcape": 1000, "mucape": 1200, "shear06Kt": 35}, 12),
                    {"tornado": 0.2, "hail": 0.2, "wind": 0.2},
                )
                grid = np.zeros((2, len(FEATURE_NAMES)), dtype=float)
                self.assertEqual(
                    {hazard: values.tolist() for hazard, values in predict_ml_hazard_matrix(grid).items()},
                    {"tornado": [0.2, 0.2], "hail": [0.2, 0.2], "wind": [0.2, 0.2]},
                )
                reset_model_cache()

    def test_older_xgboost_schema_rejects_unavailable_runtime_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            metadata = {
                "version": "legacy-with-extra-feature",
                "artifactType": "xgboost_joblib",
                "trainedAtISO": "2026-05-02T00:00:00Z",
                "featureSchemaVersion": "ml-features-v1",
                "featureSchemaHash": "legacy-v1-hash",
                "featureNames": ["forecastHour", "mlcape", "initiationConf"],
                "trainingRows": MIN_XGBOOST_TRAINING_ROWS,
            }
            (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            for hazard in HAZARD_KEYS:
                with (model_dir / f"{hazard}_xgb.joblib").open("wb") as fh:
                    pickle.dump(DummyProbabilityModel(0.2), fh)

            fake_joblib = types.SimpleNamespace(load=fake_joblib_load)
            with patch("backend.ml.inference.MODEL_DIR", model_dir), patch.dict(sys.modules, {"joblib": fake_joblib}):
                reset_model_cache()
                status = model_status()
                self.assertFalse(status["active"])
                self.assertIn("unavailable runtime features", status["reason"])
                reset_model_cache()

    def test_validation_metrics_reward_ranked_probabilities(self) -> None:
        labels = [0, 0, 1, 1]
        probabilities = [0.01, 0.05, 0.30, 0.70]

        self.assertEqual(roc_auc_score(labels, probabilities), 1.0)
        self.assertEqual(average_precision_score(labels, probabilities), 1.0)
        metrics = metrics_for_hazard("hail", labels, probabilities)
        self.assertGreater(metrics["averagePrecisionLift"], 1.0)
        self.assertGreater(metrics["brierSkillScore"], 0.0)
        self.assertEqual(category_for_probability("tornado", 0.10), "ENH")
        self.assertEqual(category_for_probability("tornado", 0.15), "ENH")
        self.assertEqual(category_for_probability("tornado", 0.30), "MDT")
        self.assertEqual(category_for_probability("wind", 0.30), "ENH")
        self.assertEqual(category_for_probability("wind", 0.45), "ENH")
        self.assertEqual(category_for_probability("wind", 0.60), "MDT")


if __name__ == "__main__":
    unittest.main()
