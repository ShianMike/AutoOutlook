from __future__ import annotations

import unittest
from datetime import date

from backend.ml.historical_event_verification import (
    DEFAULT_ENH_PLUS_EVENT_DATES,
    artifact_uses_model,
    ensure_enh_plus_spc_event,
    event_slug,
    event_window_for_date,
    fetch_spc_daily_storm_reports,
    filter_spc_reports_for_event_window,
    report_counts,
    resolve_event_dates,
    validate_event_date,
)


class HistoricalEventVerificationTests(unittest.TestCase):
    def test_archive_artifact_model_identity_must_match_active_model(self) -> None:
        expected = {
            "version": "xgb-hazards-20260611T072822Z",
            "artifactType": "xgboost_joblib",
            "featureSchemaVersion": "ml-features-v5-location-refc-temporal",
            "featureSchemaHash": "0cc2a457a6d14132",
        }
        current = {"model": dict(expected)}
        stale = {
            "model": {
                "version": "bootstrap-terms-20260602T032529Z",
                "artifactType": "calibrated_linear_terms_v1",
                "featureSchemaVersion": "ml-features-v4-parcel-cape-cin",
                "featureSchemaHash": "7f4a517976a78cf0",
            }
        }

        self.assertTrue(artifact_uses_model(current, expected))
        self.assertFalse(artifact_uses_model(stale, expected))

    def test_event_window_uses_00z_cycle_and_full_12z_to_12z_day1(self) -> None:
        window = event_window_for_date(date(2026, 4, 27))

        self.assertEqual(window.cycle_iso, "2026-04-27T00:00:00Z")
        self.assertEqual(window.start_iso, "2026-04-27T12:00:00Z")
        self.assertEqual(window.end_iso, "2026-04-28T12:00:00Z")
        self.assertEqual(window.forecast_hours, tuple(range(12, 37)))
        self.assertEqual(event_slug(date(2026, 4, 27)), "2026-04-27-hrrr00z-f12-f36")

    def test_event_dates_are_limited_to_march_2026_through_present(self) -> None:
        self.assertEqual(
            validate_event_date(date(2026, 3, 1), today=date(2026, 6, 4)),
            date(2026, 3, 1),
        )

        with self.assertRaisesRegex(ValueError, "cannot be earlier than 2026-03-01"):
            validate_event_date(date(2026, 2, 28), today=date(2026, 6, 4))

        with self.assertRaisesRegex(ValueError, "cannot be later than the current date"):
            validate_event_date(date(2026, 6, 5), today=date(2026, 6, 4))

    def test_default_event_dates_resolve_inside_allowed_window(self) -> None:
        self.assertEqual(
            resolve_event_dates(None, today=date(2026, 6, 13)),
            [
                date(2026, 3, 5),
                date(2026, 3, 6),
                date(2026, 3, 7),
                date(2026, 3, 15),
                date(2026, 3, 16),
                date(2026, 3, 26),
                date(2026, 4, 3),
                date(2026, 4, 4),
                date(2026, 4, 10),
                date(2026, 4, 14),
                date(2026, 4, 15),
                date(2026, 4, 17),
                date(2026, 4, 23),
                date(2026, 4, 24),
                date(2026, 4, 25),
                date(2026, 4, 27),
                date(2026, 4, 28),
                date(2026, 5, 10),
                date(2026, 5, 16),
                date(2026, 5, 17),
                date(2026, 5, 18),
                date(2026, 6, 6),
                date(2026, 6, 7),
                date(2026, 6, 8),
                date(2026, 6, 9),
                date(2026, 6, 10),
                date(2026, 6, 11),
                date(2026, 6, 12),
            ],
        )
        self.assertEqual(resolve_event_dates(None, today=date(2026, 6, 13)), list(DEFAULT_ENH_PLUS_EVENT_DATES))

    def test_spc_day1_must_be_enh_or_higher(self) -> None:
        enh_geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"LABEL": "SLGT"}, "geometry": None},
                {"type": "Feature", "properties": {"LABEL": "ENH"}, "geometry": None},
            ],
        }
        self.assertEqual(ensure_enh_plus_spc_event(enh_geojson, date(2026, 4, 27)), ("ENH", 4))

        slight_geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"LABEL": "TSTM"}, "geometry": None},
                {"type": "Feature", "properties": {"LABEL": "SLGT"}, "geometry": None},
            ],
        }
        with self.assertRaisesRegex(ValueError, "not an SPC ENH\\+ Day 1 event"):
            ensure_enh_plus_spc_event(slight_geojson, date(2026, 4, 28))

    def test_spc_daily_report_fetcher_parses_csv_and_counts_hazards(self) -> None:
        session = FakeReportSession(
            {
                "torn": "Time,F_Scale,Location,County,State,Lat,Lon,Comments\n1211,EF1,5 S Salisbury,Chariton,MO,39.35,-92.80,brief tornado\n",
                "hail": "Time,Size,Location,County,State,Lat,Lon,Comments\n1224,125,Goreville,Johnson,IL,37.56,-88.97,large hail\n",
                "wind": "Time,Speed,Location,County,State,Lat,Lon,Comments\n1207,UNK,6 SE Dalton,Chariton,MO,39.34,-92.89,tree damage\n1300,UNK,Ocean,Water,XX,10.0,-40.0,filtered\n",
            }
        )

        reports = fetch_spc_daily_storm_reports(date(2026, 4, 27), session=session)

        self.assertEqual(report_counts(reports), {"tornado": 1, "hail": 1, "wind": 1, "total": 3})
        self.assertEqual(session.requested_tokens, ["torn", "hail", "wind"])
        self.assertEqual(reports[0]["sourceUrl"], "https://www.spc.noaa.gov/climo/reports/260427_rpts_torn.csv")

    def test_spc_reports_filter_to_event_window(self) -> None:
        window = event_window_for_date(date(2026, 4, 27))
        reports = [
            {"type": "hail", "time": "1659", "lat": 40.0, "lon": -95.0},
            {"type": "hail", "time": "1700", "lat": 40.0, "lon": -95.0},
            {"type": "wind", "time": "2359", "lat": 41.0, "lon": -96.0},
            {"type": "tornado", "time": "0400", "lat": 39.0, "lon": -94.0},
            {"type": "wind", "time": "0401", "lat": 42.0, "lon": -97.0},
        ]

        filtered = filter_spc_reports_for_event_window(reports, window)

        self.assertEqual([item["time"] for item in filtered], ["1659", "1700", "2359", "0400", "0401"])
        self.assertEqual(
            [item["timeISO"] for item in filtered],
            [
                "2026-04-27T16:59:00Z",
                "2026-04-27T17:00:00Z",
                "2026-04-27T23:59:00Z",
                "2026-04-28T04:00:00Z",
                "2026-04-28T04:01:00Z",
            ],
        )


class FakeReportSession:
    def __init__(self, csv_by_token: dict[str, str]) -> None:
        self.csv_by_token = csv_by_token
        self.headers: dict[str, str] = {}
        self.requested_tokens: list[str] = []

    def get(self, url: str, timeout: int) -> "FakeReportResponse":
        del timeout
        token = url.rsplit("_rpts_", 1)[1].split(".", 1)[0]
        self.requested_tokens.append(token)
        return FakeReportResponse(self.csv_by_token[token])


class FakeReportResponse:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
