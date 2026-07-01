"""Integration tests for archiving the SPC hazard outlook.

Covers the archive copy step and the archive endpoint's not-found behavior
(Requirements 3.5, 3.6):

- ``update_archive_for_date`` writes ``spc-hazard-shapes.geojson`` for an ENH+
  day, normalized through the same path the live endpoint uses so the archived
  artifact matches the served contract (all four hazard keys present).
- ``GET /api/outlook/enh-plus-archive-spc-hazard-shapes`` returns a distinct
  404 when the artifact is absent for the requested date.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import backend.server as server
from backend.ml.enh_plus_archive import archive_available_dates, update_archive_for_date
from backend.ml.merged_outlook import SPC_HAZARD_TYPES


HAZARD_KEYS = set(SPC_HAZARD_TYPES)
_EVENT_DATE = date(2026, 4, 27)


def _polygon() -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[[-100.0, 34.0], [-95.0, 34.0], [-95.0, 38.0], [-100.0, 34.0]]],
    }


def _spc_category_geojson() -> dict[str, Any]:
    """A minimal SPC categorical overlay with no VALID/EXPIRE window.

    Leaving out ``EXPIRE_ISO`` skips the convective-window gate in
    ``update_archive_for_date`` so the ENH+ day is archived directly.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _polygon(),
                "properties": {"DN": 4, "LABEL": "ENH"},
            }
        ],
    }


def _spc_hazard_geojson() -> dict[str, Any]:
    """A raw SPC hazard GeoJSON spanning two hazard types (tornado + hail)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _polygon(),
                "properties": {"hazard": "tornado", "probability": 0.05, "significantSevere": False},
            },
            {
                "type": "Feature",
                "geometry": _polygon(),
                "properties": {"hazard": "hail", "probability": 0.30, "significantSevere": True},
            },
        ],
    }


def _write_merged_dir(root: Path) -> Path:
    """Write a merged D1 dir for an ENH+ day including the SPC hazard GeoJSON."""
    merged = root / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    (merged / "merged_verification_summary.json").write_text(
        json.dumps({"predictedCategories": {"ENH": 1500}}), encoding="utf-8"
    )
    for name in (
        "merged_risk_polygons.geojson",
        "merged_hazard_probability_shapes.geojson",
        "merged_probability_tile.json",
    ):
        (merged / name).write_text(
            json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
        )
    (merged / "spc_day1_cat.geojson").write_text(
        json.dumps(_spc_category_geojson()), encoding="utf-8"
    )
    (merged / "spc_day1_hazards.geojson").write_text(
        json.dumps(_spc_hazard_geojson()), encoding="utf-8"
    )
    return merged


class TestArchiveSpcHazardCopy(unittest.TestCase):
    def test_update_archive_writes_normalized_spc_hazard_shapes(self) -> None:
        # Requirement 3.5: an ENH+ day's SPC hazard outlook is archived.
        # Requirement 3.8: it is normalized to the live served contract.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root)
            archive = root / "enh_plus_archive"

            entry = update_archive_for_date(
                archive, merged, _EVENT_DATE, report_fetch_fn=lambda _d: []
            )

            self.assertIsNotNone(entry)
            self.assertEqual(archive_available_dates(archive), [_EVENT_DATE.isoformat()])

            hazard_path = archive / _EVENT_DATE.isoformat() / "spc-hazard-shapes.geojson"
            self.assertTrue(hazard_path.is_file())

            stored = json.loads(hazard_path.read_text(encoding="utf-8"))
            # Normalized to the served contract: all four hazard keys present.
            self.assertEqual(stored["type"], "FeatureCollection")
            self.assertEqual(set(stored["properties"]["availableHazards"]), HAZARD_KEYS)
            self.assertEqual(set(stored["properties"]["hazardsPresent"].keys()), HAZARD_KEYS)
            # The two source hazards are present; the untouched ones are empty.
            self.assertTrue(stored["properties"]["hazardsPresent"]["tornado"])
            self.assertTrue(stored["properties"]["hazardsPresent"]["hail"])
            self.assertFalse(stored["properties"]["hazardsPresent"]["wind"])
            self.assertFalse(stored["properties"]["hazardsPresent"]["thunder"])
            # Every emitted feature carries the normalized contract fields.
            self.assertTrue(stored["features"])
            for feature in stored["features"]:
                props = feature["properties"]
                self.assertIn(props["hazard"], HAZARD_KEYS)
                self.assertGreaterEqual(props["probabilityPercent"], 0.0)
                self.assertLessEqual(props["probabilityPercent"], 100.0)
                self.assertIsInstance(props["significantSevere"], bool)


class TestArchiveSpcHazardEndpointNotFound(unittest.TestCase):
    def test_endpoint_returns_404_when_artifact_absent(self) -> None:
        # Requirement 3.6: a distinct not-found response when the archived SPC
        # hazard outlook does not exist for the requested date.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create the archive date dir but WITHOUT the hazard artifact.
            (root / "backend" / "artifacts" / "enh_plus_archive" / _EVENT_DATE.isoformat()).mkdir(
                parents=True, exist_ok=True
            )
            with patch.object(server, "PROJECT_ROOT", root):
                client = server.app.test_client()
                response = client.get(
                    f"/api/outlook/enh-plus-archive-spc-hazard-shapes?date={_EVENT_DATE.isoformat()}"
                )

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        # Distinct not-found response; no probability shapes are returned.
        self.assertEqual(payload["code"], "outlook_not_ready")
        self.assertNotIn("features", payload)


if __name__ == "__main__":
    unittest.main()
