"""Tests for the auto-accumulating ENH+ outlook archive."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from backend.ml.enh_plus_archive import (
    archive_available_dates,
    day_is_enh_plus,
    predicted_max_category,
    update_archive_for_date,
)


def _spc_geojson(label: str, dn: int) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[-100.0, 34.0], [-95.0, 34.0], [-95.0, 38.0], [-100.0, 34.0]]]},
                "properties": {"DN": dn, "LABEL": label},
            }
        ],
    }


def _write_merged_dir(root: Path, predicted: dict[str, int], spc_label: str | None, spc_dn: int = 0) -> Path:
    merged = root / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    (merged / "merged_verification_summary.json").write_text(
        json.dumps({"predictedCategories": predicted}), encoding="utf-8"
    )
    for name in (
        "merged_risk_polygons.geojson",
        "merged_hazard_probability_shapes.geojson",
        "merged_probability_tile.json",
    ):
        (merged / name).write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    if spc_label is not None:
        (merged / "spc_day1_cat.geojson").write_text(json.dumps(_spc_geojson(spc_label, spc_dn)), encoding="utf-8")
    return merged


class TestEnhPlusDetection(unittest.TestCase):
    def test_predicted_max_category_uses_thresholds(self) -> None:
        self.assertEqual(predicted_max_category({"ENH": 1500})[0], "ENH")
        # 900 ENH cells is below the 1200 threshold -> falls back to SLGT if it qualifies.
        self.assertEqual(predicted_max_category({"SLGT": 400, "ENH": 900})[0], "SLGT")

    def test_day_is_enh_plus_from_autooutlook(self) -> None:
        self.assertTrue(day_is_enh_plus({"predictedCategories": {"ENH": 1300}}, None))

    def test_day_is_enh_plus_from_spc(self) -> None:
        # AutoOutlook only SLGT, but SPC drew ENH -> still an ENH+ day.
        self.assertTrue(day_is_enh_plus({"predictedCategories": {"SLGT": 500}}, _spc_geojson("ENH", 4)))

    def test_day_below_enh_is_not_archived(self) -> None:
        self.assertFalse(day_is_enh_plus({"predictedCategories": {"SLGT": 500}}, _spc_geojson("SLGT", 3)))


class TestUpdateArchive(unittest.TestCase):
    def test_enh_plus_day_is_archived_with_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root, {"ENH": 1500}, "ENH", 4)
            archive = root / "enh_plus_archive"

            reports = [
                {"type": "tornado", "time": "1830", "lat": 36.0, "lon": -97.0},
                {"type": "hail", "time": "2000", "lat": 35.0, "lon": -98.0},
            ]
            entry = update_archive_for_date(
                archive, merged, date(2026, 4, 27), report_fetch_fn=lambda _d: reports,
            )

            self.assertIsNotNone(entry)
            self.assertEqual(entry["maxCategory"], "ENH")
            self.assertEqual(entry["reportCounts"]["tornado"], 1)
            self.assertEqual(entry["reportCounts"]["hail"], 1)

            date_dir = archive / "2026-04-27"
            self.assertTrue((date_dir / "verification.json").exists())
            self.assertTrue((date_dir / "risk-polygons.geojson").exists())
            self.assertTrue((date_dir / "spc-day1-category.geojson").exists())
            stored = json.loads((date_dir / "storm-reports.json").read_text(encoding="utf-8"))
            self.assertEqual(len(stored["reports"]), 2)
            self.assertEqual(archive_available_dates(archive), ["2026-04-27"])

    def test_non_enh_day_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root, {"SLGT": 400}, "SLGT", 3)
            archive = root / "enh_plus_archive"
            entry = update_archive_for_date(archive, merged, date(2026, 4, 27), report_fetch_fn=lambda _d: [])
            self.assertIsNone(entry)
            self.assertFalse((archive / "2026-04-27").exists())
            self.assertEqual(archive_available_dates(archive), [])

    def test_reports_update_over_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root, {"ENH": 1500}, "ENH", 4)
            archive = root / "enh_plus_archive"
            event = date(2026, 4, 27)

            update_archive_for_date(archive, merged, event, report_fetch_fn=lambda _d: [
                {"type": "tornado", "time": "1300", "lat": 36.0, "lon": -97.0},
            ])
            # A later run sees more reports for the same day.
            entry = update_archive_for_date(archive, merged, event, report_fetch_fn=lambda _d: [
                {"type": "tornado", "time": "1300", "lat": 36.0, "lon": -97.0},
                {"type": "wind", "time": "2100", "lat": 35.0, "lon": -96.0},
                {"type": "hail", "time": "2200", "lat": 34.0, "lon": -95.0},
            ])
            self.assertEqual(entry["reportCounts"]["total"], 3)
            stored = json.loads((archive / "2026-04-27" / "storm-reports.json").read_text(encoding="utf-8"))
            self.assertEqual(len(stored["reports"]), 3)
            # Still a single archive entry for the date (refreshed, not duplicated).
            self.assertEqual(archive_available_dates(archive), ["2026-04-27"])

    def test_reports_preserved_on_fetch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root, {"ENH": 1500}, "ENH", 4)
            archive = root / "enh_plus_archive"
            event = date(2026, 4, 27)

            update_archive_for_date(archive, merged, event, report_fetch_fn=lambda _d: [
                {"type": "tornado", "time": "1300", "lat": 36.0, "lon": -97.0},
            ])

            def boom(_d: date) -> list[dict[str, Any]]:
                raise ConnectionError("SPC reports unavailable")

            entry = update_archive_for_date(archive, merged, event, report_fetch_fn=boom)
            # Prior reports are kept rather than wiped on a transient failure.
            self.assertEqual(entry["reportCounts"]["tornado"], 1)


if __name__ == "__main__":
    unittest.main()
