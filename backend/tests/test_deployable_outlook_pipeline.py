from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.hrrr_selected import HrrrCycle, descriptor_matches_selected, parse_idx, selected_ranges
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    category_grid_from_probabilities,
    gridded_features_from_fields,
    risk_polygons_from_grid,
)
from backend.ml.outlook_pipeline import run_pipeline
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
        features = gridded_features_from_fields(small_fields((2, 3)), forecast_hour=0)
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

    def test_risk_polygons_are_geojson_features(self) -> None:
        lats = np.linspace(30.0, 34.0, 5)
        lons = np.linspace(-100.0, -96.0, 5)
        category = np.ones((5, 5), dtype=int)
        category[1:4, 1:4] = SPC_RISK_LABELS.index("MRGL")

        geojson = risk_polygons_from_grid(lats, lons, category, 0, "2024-05-04T12:00:00Z", min_cells=3)

        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertTrue(any(feature["properties"]["category"] == "MRGL" for feature in geojson["features"]))

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


if __name__ == "__main__":
    unittest.main()
