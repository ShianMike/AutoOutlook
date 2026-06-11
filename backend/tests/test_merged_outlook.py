"""Tests for merged multi-cycle outlook D1 verification."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from backend.ml.merged_outlook import (
    _merge_cig_shape_collections,
    _spc_geojson_max_category_ordinal,
    available_merged_d1_dates,
    merge_cycles_for_spc_window,
    resolve_cycle_dirs_for_merged_d1_date,
    resolve_merge_cycle_dirs,
    spc_d1_window,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_spc_geojson(
    valid_iso: str = "2026-06-03T12:00:00Z",
    expire_iso: str = "2026-06-04T12:00:00Z",
    label: str = "SLGT",
    dn: int = 4,
    polygon: list[list[list[float]]] | None = None,
) -> dict[str, Any]:
    if polygon is None:
        polygon = [[[-100.0, 34.0], [-95.0, 34.0], [-95.0, 38.0], [-100.0, 38.0], [-100.0, 34.0]]]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": polygon},
                "properties": {
                    "DN": dn,
                    "LABEL": label,
                    "VALID_ISO": valid_iso,
                    "EXPIRE_ISO": expire_iso,
                    "ISSUE_ISO": valid_iso,
                },
            }
        ],
    }


class TestArchivedSpcSelection(unittest.TestCase):
    def test_category_ordinal_recognizes_moderate_label(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"properties": {"LABEL": "TSTM"}},
                {"properties": {"LABEL": "ENH"}},
                {"properties": {"LABEL": "MDT"}},
            ],
        }

        self.assertEqual(_spc_geojson_max_category_ordinal(geojson), 5)


def _make_probability_tile(
    category_grid: list[list[int]] | np.ndarray,
    lat_min: float = 30.0,
    lat_max: float = 45.0,
    lon_min: float = -105.0,
    lon_max: float = -85.0,
    stride: int = 2,
) -> dict[str, Any]:
    grid = np.asarray(category_grid, dtype=int)
    rows, cols = grid.shape
    lats = np.linspace(lat_min, lat_max, rows)
    lons = np.linspace(lon_min, lon_max, cols)
    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    return {
        "stride": stride,
        "categoryOrdinal": grid.tolist(),
        "lats": lat_grid.tolist(),
        "lons": lon_grid.tolist(),
        "probabilities": {
            "tornado": np.zeros_like(grid, dtype=float).tolist(),
            "hail": np.zeros_like(grid, dtype=float).tolist(),
            "wind": np.zeros_like(grid, dtype=float).tolist(),
        },
    }


def _write_cycle_artifacts(
    root: Path,
    cycle_name: str,
    cycle_time_iso: str,
    ready_hours: list[int],
    grid: list[list[int]] | np.ndarray,
    status: str = "complete",
    tile_stride: int = 2,
) -> Path:
    cycle_dir = root / cycle_name
    cycle_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "cycle": f"HRRR {cycle_name}",
        "cycleTimeISO": cycle_time_iso,
        "status": status,
        "readyForecastHours": ready_hours,
        "mode": "incremental",
    }
    (cycle_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    for hour in ready_hours:
        hour_dir = cycle_dir / "hours" / f"f{hour:02d}"
        hour_dir.mkdir(parents=True, exist_ok=True)
        tile = _make_probability_tile(grid, stride=tile_stride)
        (hour_dir / "probability_tile.json").write_text(json.dumps(tile), encoding="utf-8")
    return cycle_dir


def _mock_spc_fetch(geojson: dict[str, Any] | None = None):
    spc_geojson = geojson or _make_spc_geojson()
    def fetcher(session, output_dir):
        return {
            "day1Url": "https://spc.example/day1",
            "geojsonZipUrl": "https://spc.example/day1.zip",
            "fetchedAtISO": _now_iso(),
            "categoryGeojson": spc_geojson,
        }
    return fetcher


class TestSpcD1Window(unittest.TestCase):
    def test_extracts_from_geojson(self) -> None:
        geojson = _make_spc_geojson("2026-06-03T12:00:00Z", "2026-06-04T12:00:00Z")
        valid, expire = spc_d1_window(geojson)
        self.assertEqual(valid, datetime(2026, 6, 3, 12, tzinfo=timezone.utc))
        self.assertEqual(expire, datetime(2026, 6, 4, 12, tzinfo=timezone.utc))

    def test_defaults_to_today_12z(self) -> None:
        now = datetime(2026, 6, 3, 18, 0, tzinfo=timezone.utc)
        valid, expire = spc_d1_window(None, now=now)
        self.assertEqual(valid, datetime(2026, 6, 3, 12, tzinfo=timezone.utc))
        self.assertEqual(expire, datetime(2026, 6, 4, 12, tzinfo=timezone.utc))

    def test_defaults_before_12z(self) -> None:
        now = datetime(2026, 6, 3, 8, 0, tzinfo=timezone.utc)
        valid, expire = spc_d1_window(None, now=now)
        self.assertEqual(valid, datetime(2026, 6, 2, 12, tzinfo=timezone.utc))
        self.assertEqual(expire, datetime(2026, 6, 3, 12, tzinfo=timezone.utc))

    def test_empty_geojson_uses_default(self) -> None:
        valid, expire = spc_d1_window(
            {"type": "FeatureCollection", "features": []},
            now=datetime(2026, 6, 3, 18, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(valid, datetime(2026, 6, 3, 12, tzinfo=timezone.utc))


class TestResolveMergeCycleDirs(unittest.TestCase):
    def test_finds_completed_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", [0, 6, 12, 18], np.ones((5, 5), dtype=int))
            _write_cycle_artifacts(root, "12z", "2026-06-03T12:00:00Z", [0, 6, 12, 18], np.ones((5, 5), dtype=int))
            dirs = resolve_merge_cycle_dirs(root)
            self.assertEqual(len(dirs), 2)
            self.assertEqual(dirs[0].name, "12z")
            self.assertEqual(dirs[1].name, "00z")

    def test_excludes_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", [0, 6, 12, 18], np.ones((5, 5), dtype=int), status="failed")
            _write_cycle_artifacts(root, "12z", "2026-06-03T12:00:00Z", [0, 6, 12, 18], np.ones((5, 5), dtype=int))
            dirs = resolve_merge_cycle_dirs(root)
            self.assertEqual(len(dirs), 1)
            self.assertEqual(dirs[0].name, "12z")

    def test_excludes_sparse_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", [0, 6, 12, 18], np.ones((5, 5), dtype=int))
            _write_cycle_artifacts(root, "12z", "2026-06-03T12:00:00Z", [0], np.ones((5, 5), dtype=int))
            dirs = resolve_merge_cycle_dirs(root)
            self.assertEqual(len(dirs), 1)

    def test_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(resolve_merge_cycle_dirs(Path(tmp)), [])

    def test_nonexistent_root(self) -> None:
        self.assertEqual(resolve_merge_cycle_dirs(Path("/nonexistent/path")), [])


class TestMergedD1DateSelection(unittest.TestCase):
    def test_available_dates_use_cycle_dates_not_next_day_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            _write_cycle_artifacts(root, "18z", "2026-06-07T18:00:00Z", list(range(49)), grid)

            dates = available_merged_d1_dates(root)

            self.assertEqual(dates, ["2026-06-07"])
            self.assertNotIn("2026-06-08", dates)
            self.assertNotIn("2026-06-09", dates)

    def test_prefers_same_day_00z_cycle_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            cycle_00z = _write_cycle_artifacts(root, "00z", "2026-06-08T00:00:00Z", list(range(49)), grid)
            _write_cycle_artifacts(root, "18z", "2026-06-08T18:00:00Z", list(range(49)), grid)

            dirs = resolve_cycle_dirs_for_merged_d1_date(
                root,
                date(2026, 6, 8),
            )

            self.assertEqual(dirs, [cycle_00z])

    def test_later_same_day_cycles_do_not_expose_partial_next_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            cycle_00z = _write_cycle_artifacts(root, "00z", "2026-06-11T00:00:00Z", list(range(49)), grid)
            _write_cycle_artifacts(root, "12z", "2026-06-11T12:00:00Z", list(range(49)), grid)

            dates = available_merged_d1_dates(root)
            current_dirs = resolve_cycle_dirs_for_merged_d1_date(root, date(2026, 6, 11))
            next_dirs = resolve_cycle_dirs_for_merged_d1_date(root, date(2026, 6, 12))

            self.assertEqual(dates, ["2026-06-11"])
            self.assertEqual(current_dirs, [cycle_00z])
            self.assertEqual(next_dirs, [])

    def test_available_dates_can_include_previous_same_day_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            cycle_10_00z = _write_cycle_artifacts(root, "20260610_00z", "2026-06-10T00:00:00Z", list(range(49)), grid)
            cycle_11_00z = _write_cycle_artifacts(root, "20260611_00z", "2026-06-11T00:00:00Z", list(range(49)), grid)
            _write_cycle_artifacts(root, "20260611_12z", "2026-06-11T12:00:00Z", list(range(49)), grid)

            dates = available_merged_d1_dates(root)

            self.assertEqual(dates, ["2026-06-11", "2026-06-10"])
            self.assertEqual(resolve_cycle_dirs_for_merged_d1_date(root, date(2026, 6, 11)), [cycle_11_00z])
            self.assertEqual(resolve_cycle_dirs_for_merged_d1_date(root, date(2026, 6, 10)), [cycle_10_00z])


class TestMergeCyclesForSpcWindow(unittest.TestCase):
    def test_merged_cig_corridors_join_nearby_hourly_areas_and_drop_tiny_islands(self) -> None:
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-99.0, 34.0],
                            [-97.8, 34.0],
                            [-97.8, 37.0],
                            [-99.0, 37.0],
                            [-99.0, 34.0],
                        ]],
                    },
                    "properties": {
                        "hazard": "tornado",
                        "cig": 1,
                        "forecastHour": 12,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-97.1, 34.4],
                            [-95.9, 34.4],
                            [-95.9, 37.4],
                            [-97.1, 37.4],
                            [-97.1, 34.4],
                        ]],
                    },
                    "properties": {
                        "hazard": "tornado",
                        "cig": 1,
                        "forecastHour": 13,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-88.0, 30.0],
                            [-87.9, 30.0],
                            [-87.9, 30.1],
                            [-88.0, 30.1],
                            [-88.0, 30.0],
                        ]],
                    },
                    "properties": {
                        "hazard": "tornado",
                        "cig": 1,
                        "forecastHour": 14,
                    },
                },
            ],
        }

        merged = _merge_cig_shape_collections(
            [collection],
            "2026-06-03T12:00:00Z",
        )

        self.assertEqual(len(merged["features"]), 1)
        feature = merged["features"][0]
        self.assertEqual(feature["properties"]["componentCount"], 1)
        self.assertEqual(feature["properties"]["sourceFeatureCount"], 3)
        self.assertEqual(feature["properties"]["sourceForecastHours"], [12, 13, 14])
        self.assertEqual(
            feature["properties"]["vectorization"]["mergedCorridorGeometry"],
            "joined_hourly_corridor",
        )

    def test_merged_cig_levels_use_spc_style_cumulative_contours(self) -> None:
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-102.0, 32.0],
                            [-94.0, 32.0],
                            [-94.0, 40.0],
                            [-102.0, 40.0],
                            [-102.0, 32.0],
                        ]],
                    },
                    "properties": {
                        "hazard": "wind",
                        "cig": 1,
                        "forecastHour": 12,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-100.0, 34.0],
                            [-96.0, 34.0],
                            [-96.0, 38.0],
                            [-100.0, 38.0],
                            [-100.0, 34.0],
                        ]],
                    },
                    "properties": {
                        "hazard": "wind",
                        "cig": 2,
                        "forecastHour": 12,
                    },
                },
            ],
        }

        merged = _merge_cig_shape_collections(
            [collection],
            "2026-06-03T12:00:00Z",
        )

        self.assertEqual(len(merged["features"]), 2)
        by_level = {
            feature["properties"]["cig"]: feature
            for feature in merged["features"]
        }
        from pyproj import Transformer
        from shapely.geometry import shape
        from shapely.ops import transform

        to_projected = Transformer.from_crs(
            "EPSG:4326",
            "EPSG:5070",
            always_xy=True,
        ).transform
        lower = transform(to_projected, shape(by_level[1]["geometry"]))
        higher = transform(to_projected, shape(by_level[2]["geometry"]))
        lower_hatch = transform(
            to_projected,
            shape(by_level[1]["properties"]["hatchGeometry"]),
        )
        overlap_ratio = lower.intersection(higher).area / min(lower.area, higher.area)
        self.assertGreater(overlap_ratio, 0.999)
        self.assertTrue(lower.covers(higher))
        self.assertLess(lower_hatch.intersection(higher).area, 1.0)
        self.assertEqual(
            by_level[1]["properties"]["vectorization"]["displayGeometry"],
            "spc_cumulative_contour",
        )
        self.assertEqual(
            by_level[2]["properties"]["vectorization"]["displayGeometry"],
            "spc_cumulative_contour",
        )
        self.assertEqual(
            by_level[1]["properties"]["vectorization"]["hatchGeometry"],
            "spc_exclusive_hatch",
        )

    def test_merges_two_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid_a = np.array([[0, 1, 2], [1, 0, 1], [0, 0, 0], [0, 0, 0], [0, 0, 0]])
            grid_b = np.array([[1, 0, 3], [0, 2, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]])
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", list(range(13, 37)), grid_a)
            dir_b = _write_cycle_artifacts(root, "12z", "2026-06-03T12:00:00Z", list(range(0, 25)), grid_b)
            output = root / "output"
            result = merge_cycles_for_spc_window(
                [dir_a, dir_b],
                spc_fetch_fn=_mock_spc_fetch(),
                output_dir=output,
            )
            self.assertNotIn("error", result)
            self.assertEqual(len(result["mergedCycles"]), 2)
            self.assertEqual(result["mergeMethod"], "maximum")
            self.assertEqual(result["d1WindowValidISO"], "2026-06-03T12:00:00Z")
            self.assertEqual(result["d1WindowExpireISO"], "2026-06-04T12:00:00Z")
            self.assertGreater(len(result["contributingHours"]), 0)
            self.assertTrue((output / "merged_verification_summary.json").exists())
            self.assertTrue((output / "merged_d1_index.json").exists())
            self.assertTrue((output / "spc_day1_cat.geojson").exists())

    def test_preserves_source_tile_stride_in_merged_tile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.array([[0, 1, 2], [1, 3, 4], [0, 2, 0], [0, 0, 0], [0, 0, 0]])
            cycle_dir = _write_cycle_artifacts(
                root,
                "00z",
                "2026-06-03T00:00:00Z",
                list(range(12, 19)),
                grid,
                tile_stride=2,
            )
            output = root / "output"
            result = merge_cycles_for_spc_window(
                [cycle_dir],
                spc_fetch_fn=_mock_spc_fetch(),
                output_dir=output,
            )
            merged_tile = json.loads((output / "merged_probability_tile.json").read_text(encoding="utf-8"))
            merged_index = json.loads((output / "merged_d1_index.json").read_text(encoding="utf-8"))
            self.assertEqual(result["tileStride"], 2)
            self.assertEqual(merged_tile["stride"], 2)
            self.assertEqual(merged_index["tileStride"], 2)

    def test_preserves_cig_shapes_in_merged_tile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.array([[0, 1, 2], [1, 3, 4], [0, 2, 0], [0, 0, 0], [0, 0, 0]])
            cycle_dir = _write_cycle_artifacts(
                root,
                "00z",
                "2026-06-03T00:00:00Z",
                [12],
                grid,
            )
            tile_path = cycle_dir / "hours" / "f12" / "probability_tile.json"
            tile = json.loads(tile_path.read_text(encoding="utf-8"))
            tile["cigShapes"] = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [-100.0, 34.0],
                                [-99.0, 34.0],
                                [-99.0, 35.0],
                                [-100.0, 35.0],
                                [-100.0, 34.0],
                            ]],
                        },
                        "properties": {
                            "hazard": "wind",
                            "cig": 2,
                            "label": "WIND CIG2",
                            "forecastHour": 12,
                            "validTimeISO": "2026-06-03T12:00:00Z",
                        },
                    },
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [-99.5, 34.5],
                                [-98.5, 34.5],
                                [-98.5, 35.5],
                                [-99.5, 35.5],
                                [-99.5, 34.5],
                            ]],
                        },
                        "properties": {
                            "hazard": "wind",
                            "cig": 2,
                            "label": "WIND CIG2",
                            "forecastHour": 13,
                            "validTimeISO": "2026-06-03T13:00:00Z",
                        },
                    },
                ],
            }
            tile_path.write_text(json.dumps(tile), encoding="utf-8")

            output = root / "output"
            merge_cycles_for_spc_window(
                [cycle_dir],
                spc_fetch_fn=_mock_spc_fetch(),
                output_dir=output,
            )

            merged_tile = json.loads((output / "merged_probability_tile.json").read_text(encoding="utf-8"))
            cig_features = merged_tile["cigShapes"]["features"]
            self.assertEqual(len(cig_features), 1)
            self.assertEqual(cig_features[0]["properties"]["hazard"], "wind")
            self.assertEqual(cig_features[0]["properties"]["cig"], 2)
            self.assertEqual(cig_features[0]["properties"]["forecastHour"], 0)
            self.assertEqual(cig_features[0]["properties"]["sourceForecastHours"], [12, 13])
            self.assertEqual(cig_features[0]["properties"]["sourceFeatureCount"], 2)
            self.assertEqual(
                cig_features[0]["properties"]["validTimeISO"],
                "2026-06-03T12:00:00Z",
            )

    def test_accepts_custom_event_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            cycle_dir = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", list(range(12, 30)), grid)
            result = merge_cycles_for_spc_window(
                [cycle_dir],
                spc_fetch_fn=_mock_spc_fetch(),
                window_valid=datetime(2026, 6, 3, 17, tzinfo=timezone.utc),
                window_expire=datetime(2026, 6, 4, 4, tzinfo=timezone.utc),
                target_date="2026-06-03",
            )
            contributing_hours = [item["forecastHour"] for item in result["contributingHours"]]
            self.assertEqual(result["d1WindowValidISO"], "2026-06-03T17:00:00Z")
            self.assertEqual(result["d1WindowExpireISO"], "2026-06-04T04:00:00Z")
            self.assertEqual(contributing_hours, list(range(17, 29)))

    def test_filters_hours_outside_d1_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            # Hours 0, 6, 10 are before 12Z; hours 12, 18 are within D1 window
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", [0, 6, 10, 12, 18], grid)
            result = merge_cycles_for_spc_window(
                [dir_a],
                spc_fetch_fn=_mock_spc_fetch(),
            )
            for hour_info in result["contributingHours"]:
                dt = datetime.fromisoformat(hour_info["validTimeISO"].replace("Z", "+00:00"))
                self.assertGreaterEqual(dt, datetime(2026, 6, 3, 12, tzinfo=timezone.utc))
                self.assertLess(dt, datetime(2026, 6, 4, 12, tzinfo=timezone.utc))
            # Only hours 12 and 18 should contribute (valid at 12Z and 18Z)
            contributing_hours = [h["forecastHour"] for h in result["contributingHours"]]
            self.assertNotIn(0, contributing_hours)
            self.assertNotIn(6, contributing_hours)
            self.assertNotIn(10, contributing_hours)
            self.assertIn(12, contributing_hours)
            self.assertIn(18, contributing_hours)

    def test_no_qualifying_hours_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", [0, 1, 2, 3], grid)
            with self.assertRaises(ValueError):
                merge_cycles_for_spc_window([dir_a], spc_fetch_fn=_mock_spc_fetch())

    def test_grid_shape_mismatch_skips_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid_5x5 = np.ones((5, 5), dtype=int)
            grid_3x3 = np.ones((3, 3), dtype=int)
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", list(range(12, 25)), grid_5x5)
            dir_b = root / "12z"
            dir_b.mkdir(parents=True)
            (dir_b / "index.json").write_text(json.dumps({
                "cycle": "HRRR 12z",
                "cycleTimeISO": "2026-06-03T12:00:00Z",
                "status": "complete",
                "readyForecastHours": [0, 6, 12],
            }), encoding="utf-8")
            for h in [0, 6, 12]:
                hour_dir = dir_b / "hours" / f"f{h:02d}"
                hour_dir.mkdir(parents=True)
                (hour_dir / "probability_tile.json").write_text(
                    json.dumps(_make_probability_tile(grid_3x3)),
                    encoding="utf-8",
                )
            result = merge_cycles_for_spc_window(
                [dir_a, dir_b],
                spc_fetch_fn=_mock_spc_fetch(),
            )
            self.assertNotIn("error", result)
            self.assertGreaterEqual(len(result["mergedCycles"]), 1)

    def test_writes_error_summary_on_spc_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", list(range(12, 25)), grid)

            def bad_fetch(session, output_dir):
                raise ConnectionError("SPC is down")

            output = root / "output"
            with self.assertRaises(ConnectionError):
                merge_cycles_for_spc_window([dir_a], spc_fetch_fn=bad_fetch, output_dir=output)
            self.assertTrue((output / "merged_verification_summary.json").exists())
            error_data = json.loads((output / "merged_verification_summary.json").read_text())
            self.assertIn("error", error_data)

    def test_overlapping_valid_times_take_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid_low = np.array([[1, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]])
            grid_high = np.array([[3, 2, 1], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]])
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", list(range(12, 25)), grid_low)
            dir_b = _write_cycle_artifacts(root, "12z", "2026-06-03T12:00:00Z", list(range(0, 13)), grid_high)
            result = merge_cycles_for_spc_window(
                [dir_a, dir_b],
                spc_fetch_fn=_mock_spc_fetch(),
            )
            pred = result.get("predictedCategories", {})
            has_nonzero = any(pred.get(cat, 0) > 0 for cat in ("SLGT", "ENH", "MRGL", "TSTM"))
            self.assertTrue(has_nonzero, f"Expected non-zero predicted categories but got {pred}")

    def test_resolve_cycle_dirs_for_window(self) -> None:
        from backend.ml.merged_outlook import resolve_cycle_dirs_for_window
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            # Cycle 1: 00Z on 2026-06-03, hours 0-48. Valid window 12Z 06-03 to 12Z 06-04 (hours 12-36)
            dir_a = _write_cycle_artifacts(root, "00z_hrrr", "2026-06-03T00:00:00Z", [12, 18], grid)
            # Cycle 2: 12Z on 2026-06-03, hours 0-24. Valid window 12Z 06-03 to 12Z 06-04 (hours 0-24)
            dir_b = _write_cycle_artifacts(root, "12z_hrrr", "2026-06-03T12:00:00Z", [0, 6], grid)
            # Cycle 3: non-HRRR cycle, should be ignored for HRRR model
            dir_c = _write_cycle_artifacts(root, "12z_other_model", "2026-06-03T12:00:00Z", [0, 6], grid)
            # Update its model name in index
            index = json.loads((dir_c / "index.json").read_text(encoding="utf-8"))
            index["cyclePolicy"] = {"model": "other"}
            (dir_c / "index.json").write_text(json.dumps(index), encoding="utf-8")

            d1_valid = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
            d1_expire = d1_valid + timedelta(days=1)

            dirs = resolve_cycle_dirs_for_window(root, d1_valid, d1_expire, "hrrr")
            self.assertEqual(len(dirs), 2)
            self.assertIn(dir_a, dirs)
            self.assertIn(dir_b, dirs)
            self.assertNotIn(dir_c, dirs)

    def test_merge_cycles_with_target_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = np.ones((5, 5), dtype=int)
            dir_a = _write_cycle_artifacts(root, "00z", "2026-06-03T00:00:00Z", [12, 18], grid)
            
            # Place a dummy spc_day1_cat.geojson in the cycle dir
            spc_geo = _make_spc_geojson(valid_iso="2026-06-03T12:00:00Z", expire_iso="2026-06-04T12:00:00Z")
            (dir_a / "spc_day1_cat.geojson").write_text(json.dumps(spc_geo), encoding="utf-8")

            # Run merge with target date
            result = merge_cycles_for_spc_window(
                [dir_a],
                target_date="2026-06-03"
            )
            self.assertEqual(result["d1WindowValidISO"], "2026-06-03T12:00:00Z")
            self.assertEqual(result["d1WindowExpireISO"], "2026-06-04T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
