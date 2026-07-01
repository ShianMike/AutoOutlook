"""Integration tests for the live SPC hazard endpoint.

Exercises ``GET /api/outlook/spc-hazard-shapes`` through the Flask test client
and asserts the distinct success / not-found / server-error status contract
(Requirements 2.1, 2.4, 2.5):

- **200**: the normalized collection carries all four hazard keys.
- **404** ``spc_hazard_unavailable``: no cached SPC hazard outlook exists; the
  body carries no ``features``.
- **500** ``spc_hazard_failed``: reading/parsing failed; the body carries no
  partial ``features``.

The 404 (missing) and 500 (parse failure) statuses are mutually distinct and
neither error response returns partial shapes.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import backend.server as server


HAZARD_KEYS = {"tornado", "hail", "wind", "thunder"}
_EVENT_DATE = "2026-04-27"


def _hazard_geojson() -> dict[str, Any]:
    """A minimal but valid SPC hazard GeoJSON spanning two hazard types."""
    polygon = {
        "type": "Polygon",
        "coordinates": [[[-100.0, 34.0], [-95.0, 34.0], [-95.0, 38.0], [-100.0, 34.0]]],
    }
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": polygon,
                "properties": {"hazard": "tornado", "probability": 0.05, "significantSevere": False},
            },
            {
                "type": "Feature",
                "geometry": polygon,
                "properties": {"hazard": "hail", "probability": 0.30, "significantSevere": True},
            },
        ],
    }


class _HazardEndpointFixture:
    """Patch the server so the endpoint reads a temp artifact tree."""

    def __init__(self, hazard_file_contents: str | None, dates: list[str]):
        self._hazard_file_contents = hazard_file_contents
        self._dates = dates
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._patches: list[Any] = []

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        if self._hazard_file_contents is not None:
            merged_dir = root / "backend" / "artifacts" / f"merged_hrrr_{_EVENT_DATE}"
            merged_dir.mkdir(parents=True, exist_ok=True)
            (merged_dir / "spc_day1_hazards.geojson").write_text(
                self._hazard_file_contents, encoding="utf-8"
            )
        self._patches = [
            patch.object(server, "PROJECT_ROOT", root),
            patch.object(server, "_available_merge_dates_list", return_value=self._dates),
        ]
        for p in self._patches:
            p.start()
        return server.app.test_client()

    def __exit__(self, *exc: Any) -> None:
        for p in self._patches:
            p.stop()
        if self._tmp is not None:
            self._tmp.cleanup()


class TestSpcHazardEndpointStatusContract(unittest.TestCase):
    def test_200_returns_all_four_hazard_keys(self) -> None:
        with _HazardEndpointFixture(json.dumps(_hazard_geojson()), [_EVENT_DATE]) as client:
            response = client.get("/api/outlook/spc-hazard-shapes")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["type"], "FeatureCollection")
        # All four hazard keys are always present (Requirement 2.3).
        self.assertEqual(set(payload["properties"]["availableHazards"]), HAZARD_KEYS)
        self.assertEqual(set(payload["properties"]["hazardsPresent"].keys()), HAZARD_KEYS)
        # A successful response carries the normalized shapes.
        self.assertTrue(payload["features"])
        returned_hazards = {f["properties"]["hazard"] for f in payload["features"]}
        self.assertTrue(returned_hazards.issubset(HAZARD_KEYS))

    def test_404_when_outlook_missing(self) -> None:
        # No cached hazard file discoverable for the current outlook.
        with _HazardEndpointFixture(None, []) as client:
            response = client.get("/api/outlook/spc-hazard-shapes")

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertEqual(payload["code"], "spc_hazard_unavailable")
        # A not-found response never carries probability shapes (Requirement 2.4).
        self.assertNotIn("features", payload)

    def test_500_on_parse_failure(self) -> None:
        # The file exists but is not valid JSON -> reading/parsing fails.
        with _HazardEndpointFixture("{ this is not valid json", [_EVENT_DATE]) as client:
            response = client.get("/api/outlook/spc-hazard-shapes")

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertEqual(payload["code"], "spc_hazard_failed")
        # A server-error response never returns partial shapes (Requirement 2.5).
        self.assertNotIn("features", payload)

    def test_404_and_500_are_distinct_and_neither_returns_partial_shapes(self) -> None:
        with _HazardEndpointFixture(None, []) as client:
            missing = client.get("/api/outlook/spc-hazard-shapes")
        with _HazardEndpointFixture("{ this is not valid json", [_EVENT_DATE]) as client:
            failure = client.get("/api/outlook/spc-hazard-shapes")

        # Missing (404) is distinct from parse failure (500), and both differ
        # from a success (200) (Requirements 2.4, 2.5).
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(failure.status_code, 500)
        self.assertNotEqual(missing.status_code, failure.status_code)

        missing_payload = missing.get_json()
        failure_payload = failure.get_json()
        self.assertEqual(missing_payload["code"], "spc_hazard_unavailable")
        self.assertEqual(failure_payload["code"], "spc_hazard_failed")
        # Neither error shape includes any (partial) probability features.
        self.assertNotIn("features", missing_payload)
        self.assertNotIn("features", failure_payload)


if __name__ == "__main__":
    unittest.main()
