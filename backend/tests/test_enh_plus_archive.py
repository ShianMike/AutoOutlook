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
    remove_archived_date,
    repair_archived_spc_window,
    update_archive_for_date,
)


def _spc_geojson_windowed(label: str, dn: int, valid_iso: str, expire_iso: str) -> dict[str, Any]:
    gj = _spc_geojson(label, dn)
    gj["features"][0]["properties"]["VALID_ISO"] = valid_iso
    gj["features"][0]["properties"]["EXPIRE_ISO"] = expire_iso
    return gj


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


class TestSpcWindowGate(unittest.TestCase):
    def test_wrong_window_spc_is_not_archived(self) -> None:
        # Captured early: SPC overlay is the prior day's overnight issuance
        # (expires 12Z on the event date, not 12Z the next day).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root, {"ENH": 1500}, None)
            (merged / "spc_day1_cat.geojson").write_text(
                json.dumps(_spc_geojson_windowed("MDT", 5, "2026-06-18T01:00:00+00:00", "2026-06-18T12:00:00+00:00")),
                encoding="utf-8",
            )
            archive = root / "enh_plus_archive"
            entry = update_archive_for_date(archive, merged, date(2026, 6, 18), report_fetch_fn=lambda _d: [])
            self.assertIsNone(entry)
            self.assertFalse((archive / "2026-06-18").exists())

    def test_correct_window_spc_is_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = _write_merged_dir(root, {"ENH": 1500}, None)
            (merged / "spc_day1_cat.geojson").write_text(
                json.dumps(_spc_geojson_windowed("ENH", 4, "2026-06-18T12:00:00+00:00", "2026-06-19T12:00:00+00:00")),
                encoding="utf-8",
            )
            archive = root / "enh_plus_archive"
            entry = update_archive_for_date(archive, merged, date(2026, 6, 18), report_fetch_fn=lambda _d: [])
            self.assertIsNotNone(entry)
            self.assertEqual(entry["spcMaxCategory"], "ENH")
            self.assertEqual(archive_available_dates(archive), ["2026-06-18"])


class TestRepairArchivedSpcWindow(unittest.TestCase):
    def _seed_broken_day(self, archive: Path, event: date) -> None:
        date_dir = archive / event.isoformat()
        date_dir.mkdir(parents=True, exist_ok=True)
        # Wrong-day SPC overlay (prior day's MDT, expiring at 12Z on the event date).
        (date_dir / "spc-day1-category.geojson").write_text(
            json.dumps(_spc_geojson_windowed("MDT", 5, "2026-06-18T01:00:00+00:00", "2026-06-18T12:00:00+00:00")),
            encoding="utf-8",
        )
        (date_dir / "verification.json").write_text(
            json.dumps({"predictedCategories": {"SLGT": 400}, "spcExpireTimeISO": "2026-06-18T12:00:00Z"}),
            encoding="utf-8",
        )
        (archive / "index.json").write_text(
            json.dumps({
                "dates": [{
                    "date": event.isoformat(),
                    "maxCategory": "MDT",
                    "autoMaxCategory": "SLGT",
                    "spcMaxCategory": "MDT",
                    "reportCounts": {"tornado": 0, "hail": 0, "wind": 0, "total": 0},
                    "windowStartISO": "2026-06-18T12:00:00Z",
                    "windowEndISO": "2026-06-19T12:00:00Z",
                    "updatedAtISO": "2026-06-22T10:00:00Z",
                }],
                "generatedAtISO": "2026-06-22T10:00:00Z",
            }),
            encoding="utf-8",
        )

    def test_repair_rewrites_wrong_window_spc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "enh_plus_archive"
            event = date(2026, 6, 18)
            self._seed_broken_day(archive, event)

            correct = _spc_geojson_windowed("ENH", 4, "2026-06-18T12:00:00+00:00", "2026-06-19T12:00:00+00:00")
            entry = repair_archived_spc_window(
                archive, event, spc_fetch_fn=lambda _d: {"categoryGeojson": correct},
            )

            self.assertIsNotNone(entry)
            # SPC overlay now covers the correct convective day, with the real category.
            self.assertEqual(entry["spcMaxCategory"], "ENH")
            # AutoOutlook stays the (intentional) 00Z Day-1 forecast: SLGT.
            self.assertEqual(entry["autoMaxCategory"], "SLGT")
            self.assertEqual(entry["maxCategory"], "ENH")
            stored = json.loads((archive / "2026-06-18" / "spc-day1-category.geojson").read_text(encoding="utf-8"))
            self.assertEqual(stored["features"][0]["properties"]["EXPIRE_ISO"], "2026-06-19T12:00:00+00:00")

    def test_repair_is_noop_when_already_correct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "enh_plus_archive"
            event = date(2026, 6, 18)
            self._seed_broken_day(archive, event)
            # Replace overlay with an already-correct window.
            (archive / event.isoformat() / "spc-day1-category.geojson").write_text(
                json.dumps(_spc_geojson_windowed("ENH", 4, "2026-06-18T12:00:00+00:00", "2026-06-19T12:00:00+00:00")),
                encoding="utf-8",
            )

            calls: list[date] = []

            def fetch(d: date) -> dict[str, Any]:
                calls.append(d)
                return {"categoryGeojson": _spc_geojson_windowed("MDT", 5, "x", "y")}

            entry = repair_archived_spc_window(archive, event, spc_fetch_fn=fetch)
            self.assertIsNone(entry)
            self.assertEqual(calls, [])  # no network/fetch when already correct

    def test_repair_skips_when_fetch_still_wrong_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "enh_plus_archive"
            event = date(2026, 6, 18)
            self._seed_broken_day(archive, event)
            # Fetch returns a still-wrong window -> leave the archive untouched.
            wrong = _spc_geojson_windowed("MDT", 5, "2026-06-18T01:00:00+00:00", "2026-06-18T12:00:00+00:00")
            entry = repair_archived_spc_window(archive, event, spc_fetch_fn=lambda _d: {"categoryGeojson": wrong})
            self.assertIsNone(entry)
            stored = json.loads((archive / "2026-06-18" / "spc-day1-category.geojson").read_text(encoding="utf-8"))
            self.assertEqual(stored["features"][0]["properties"]["EXPIRE_ISO"], "2026-06-18T12:00:00+00:00")

    def test_repair_delists_day_below_enh_after_correction(self) -> None:
        # The frozen day looked MDT only because of the prior day's overlay; the
        # correct midday SPC is SLGT and AutoOutlook is SLGT -> drop from archive.
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "enh_plus_archive"
            event = date(2026, 6, 18)
            self._seed_broken_day(archive, event)  # verification predictedCategories = SLGT
            correct_slgt = _spc_geojson_windowed("SLGT", 3, "2026-06-18T12:00:00+00:00", "2026-06-19T12:00:00+00:00")

            entry = repair_archived_spc_window(
                archive, event, spc_fetch_fn=lambda _d: {"categoryGeojson": correct_slgt},
            )

            self.assertIsNotNone(entry)
            self.assertTrue(entry["removed"])
            self.assertEqual(entry["maxCategory"], "SLGT")
            self.assertFalse((archive / "2026-06-18").exists())
            self.assertEqual(archive_available_dates(archive), [])

    def test_remove_archived_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "enh_plus_archive"
            event = date(2026, 6, 18)
            self._seed_broken_day(archive, event)
            self.assertTrue(remove_archived_date(archive, event))
            self.assertFalse((archive / "2026-06-18").exists())
            self.assertEqual(archive_available_dates(archive), [])
            # Idempotent: removing again reports nothing removed.
            self.assertFalse(remove_archived_date(archive, event))


if __name__ == "__main__":
    unittest.main()
