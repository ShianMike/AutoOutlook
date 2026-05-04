"""Scheduled HRRR-to-SPC-style gridded outlook artifact pipeline."""
from __future__ import annotations

import argparse
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import requests

from backend.hrrr_selected import (
    HrrrCycle,
    HrrrHourRef,
    SELECTED_HRRR_TERMS,
    fetch_selected_hrrr_hour,
    hour_ref,
    latest_available_hrrr_cycle,
)
from backend.ml.features import FEATURE_NAMES, feature_schema_hash
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    GriddedFeatures,
    category_counts,
    category_grid_from_probabilities,
    feature_stats,
    gridded_features_from_fields,
    merge_feature_collections,
    predict_hazard_grids,
    probability_tile,
    risk_polygons_from_grid,
)
from backend.ml.inference import model_status
from backend.ml.spc_verification import compare_prediction_to_spc, fetch_current_spc_day1_category

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "latest"
FORECAST_HOURS = tuple(range(49))

FetchHourFn = Callable[[HrrrHourRef, requests.Session], tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]]
PredictorFn = Callable[[GriddedFeatures], dict[str, np.ndarray] | None]
SpcFetchFn = Callable[[requests.Session, Path | None], dict[str, Any]]


def run_pipeline(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    forecast_hours: Iterable[int] = FORECAST_HOURS,
    now: datetime | None = None,
    max_workers: int = 3,
    tile_stride: int = 5,
    verify_spc: bool = True,
    preview: bool = True,
    detect_cycle_fn: Callable[[requests.Session, datetime | None], HrrrCycle] | None = None,
    fetch_hour_fn: FetchHourFn | None = None,
    predictor_fn: PredictorFn | None = None,
    spc_fetch_fn: SpcFetchFn | None = None,
) -> dict[str, Any]:
    """Generate deployable prediction artifacts, then optionally verify against SPC."""
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    output_dir = Path(output_dir)
    working_dir = output_dir.with_name(f"{output_dir.name}.tmp")
    if working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0"
    try:
        detect = detect_cycle_fn or (lambda sess, dt: latest_available_hrrr_cycle(sess, dt))
        fetch_hour = fetch_hour_fn or fetch_selected_hrrr_hour
        predictor = predictor_fn or predict_hazard_grids
        spc_fetch = spc_fetch_fn or fetch_current_spc_day1_category

        cycle = detect(session, now)
        model = model_status()
        if not model.get("active"):
            raise RuntimeError(f"ML model inactive; refusing deployable outlook generation: {model.get('reason', 'unknown')}")

        hours = sorted({int(hour) for hour in forecast_hours})
        raw_hours = _fetch_hours(cycle, hours, session, fetch_hour, max_workers=max_workers)
        hour_artifacts = []
        hourly_collections: list[dict[str, Any]] = []
        category_grids: list[np.ndarray] = []
        probability_grids: list[dict[str, np.ndarray]] = []
        feature_grids: list[dict[str, np.ndarray]] = []
        base_lats: np.ndarray | None = None
        base_lons: np.ndarray | None = None

        for forecast_hour in hours:
            lats, lons, fields = raw_hours[forecast_hour]
            features = gridded_features_from_fields(fields, forecast_hour)
            probabilities = predictor(features)
            if probabilities is None:
                raise RuntimeError("ML hazard model returned no gridded probabilities")
            category_grid = category_grid_from_probabilities(probabilities, features)
            valid_time_iso = _valid_iso(cycle, forecast_hour)
            polygons = risk_polygons_from_grid(lats, lons, category_grid, forecast_hour, valid_time_iso)
            tile = probability_tile(lats, lons, probabilities, category_grid, forecast_hour, valid_time_iso, stride=tile_stride)

            if base_lats is None:
                base_lats = np.asarray(lats, dtype=float)
                base_lons = np.asarray(lons, dtype=float)
            if np.asarray(category_grid).shape == category_grid.shape:
                category_grids.append(category_grid)
                probability_grids.append(probabilities)
                feature_grids.append(features.raw)
            hourly_collections.append(polygons)
            hour_artifacts.append({
                "forecastHour": forecast_hour,
                "validTimeISO": valid_time_iso,
                "categoryCounts": category_counts(category_grid),
                "featureStats": feature_stats(features),
                "tile": tile,
            })

        if base_lats is None or base_lons is None or not category_grids:
            raise RuntimeError("No HRRR forecast hours produced gridded category output")

        aggregate_grid = np.maximum.reduce(category_grids)
        aggregate_probabilities = _aggregate_probabilities(probability_grids)
        aggregate_features = _aggregate_feature_grids(feature_grids)
        aggregate_polygons = risk_polygons_from_grid(
            base_lats,
            base_lons,
            aggregate_grid,
            forecast_hour=-1,
            valid_time_iso=f"{_valid_iso(cycle, min(hours))}/{_valid_iso(cycle, max(hours))}",
        )
        all_hourly_polygons = merge_feature_collections(hourly_collections)
        probability_tiles = {
            "cycle": cycle.label,
            "featureSchemaHash": feature_schema_hash(),
            "riskLabels": list(SPC_RISK_LABELS),
            "hours": [{k: v for k, v in artifact.items() if k != "featureStats"} for artifact in hour_artifacts],
        }

        _write_json(working_dir / "risk_polygons.geojson", all_hourly_polygons)
        _write_json(working_dir / "aggregate_risk_polygons.geojson", aggregate_polygons)
        _write_json(working_dir / "probability_tiles.json", probability_tiles)

        preview_file = None
        if preview:
            preview_file = _render_preview(working_dir / "preview.png", base_lats, base_lons, aggregate_grid)

        verification_summary = None
        if verify_spc:
            spc = spc_fetch(session, working_dir)
            verification_grid = _aggregate_for_spc_window(cycle, hours, category_grids, spc.get("categoryGeojson"))
            verification_summary = compare_prediction_to_spc(
                base_lats,
                base_lons,
                verification_grid,
                spc["categoryGeojson"],
                aggregate_features,
            )
            verification_summary["spcDay1Url"] = spc.get("day1Url")
            verification_summary["spcGeojsonZipUrl"] = spc.get("geojsonZipUrl")
            verification_summary["spcFetchedAtISO"] = spc.get("fetchedAtISO")
            verification_summary["spcFetchedAfterPredictionArtifacts"] = True
            _write_json(working_dir / "verification_summary.json", verification_summary)

        metadata = {
            "generatedAtISO": _now_iso(),
            "cycle": cycle.label,
            "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
            "forecastHours": hours,
            "selectedHrrrTerms": list(SELECTED_HRRR_TERMS),
            "featureNames": list(FEATURE_NAMES),
            "featureSchemaHash": feature_schema_hash(),
            "model": model,
            "riskLabels": list(SPC_RISK_LABELS),
            "artifacts": {
                "riskPolygons": "risk_polygons.geojson",
                "aggregateRiskPolygons": "aggregate_risk_polygons.geojson",
                "probabilityTiles": "probability_tiles.json",
                "metadata": "metadata.json",
                "preview": preview_file.name if preview_file else None,
                "verificationSummary": "verification_summary.json" if verification_summary else None,
            },
            "aggregateCategoryCounts": category_counts(aggregate_grid),
            "hours": [
                {k: v for k, v in artifact.items() if k not in ("tile",)}
                for artifact in hour_artifacts
            ],
            "spcVerification": verification_summary,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "leakageGuard": "Current official SPC outlook is fetched only after model prediction artifacts are written.",
        }
        _write_json(working_dir / "metadata.json", metadata)
        _publish_working_dir(working_dir, output_dir)
        return metadata
    finally:
        session.close()
        if working_dir.exists():
            shutil.rmtree(working_dir, ignore_errors=True)


def _fetch_hours(
    cycle: HrrrCycle,
    hours: list[int],
    session: requests.Session,
    fetch_hour: FetchHourFn,
    max_workers: int,
) -> dict[int, tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]]:
    out: dict[int, tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        future_to_hour = {
            executor.submit(fetch_hour, hour_ref(cycle, hour), session): hour
            for hour in hours
        }
        for future in as_completed(future_to_hour):
            hour = future_to_hour[future]
            out[hour] = future.result()
    return out


def _aggregate_probabilities(probability_grids: list[Mapping[str, np.ndarray]]) -> dict[str, np.ndarray]:
    hazards = ("tornado", "hail", "wind")
    return {
        hazard: np.maximum.reduce([np.asarray(grid[hazard], dtype=float) for grid in probability_grids])
        for hazard in hazards
    }


def _aggregate_feature_grids(feature_grids: list[Mapping[str, np.ndarray]]) -> dict[str, np.ndarray]:
    if not feature_grids:
        return {}
    out: dict[str, np.ndarray] = {}
    for key in feature_grids[0]:
        stacks = [np.asarray(grid[key], dtype=float) for grid in feature_grids if key in grid]
        if stacks:
            out[key] = np.nanmean(np.stack(stacks), axis=0)
    return out


def _aggregate_for_spc_window(
    cycle: HrrrCycle,
    hours: list[int],
    category_grids: list[np.ndarray],
    spc_geojson: Mapping[str, Any] | None,
) -> np.ndarray:
    if not spc_geojson:
        return np.maximum.reduce(category_grids)
    props = next((feature.get("properties", {}) for feature in spc_geojson.get("features", [])), {})
    valid = _parse_iso(props.get("VALID_ISO"))
    expire = _parse_iso(props.get("EXPIRE_ISO"))
    if valid is None or expire is None:
        return np.maximum.reduce(category_grids)
    selected: list[np.ndarray] = []
    for hour, grid in zip(hours, category_grids, strict=False):
        valid_time = cycle.cycle_time + timedelta(hours=hour)
        if valid <= valid_time < expire:
            selected.append(grid)
    return np.maximum.reduce(selected or category_grids)


def _render_preview(path: Path, lats: np.ndarray, lons: np.ndarray, category_grid: np.ndarray) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap
    except Exception:
        return None
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        extent = [float(np.nanmin(lon_arr)), float(np.nanmax(lon_arr)), float(np.nanmin(lat_arr)), float(np.nanmax(lat_arr))]
    else:
        extent = [float(np.nanmin(lon_arr)), float(np.nanmax(lon_arr)), float(np.nanmin(lat_arr)), float(np.nanmax(lat_arr))]
    colors = ["#ffffff", "#c1e9c1", "#66a366", "#f6e35f", "#f2a154", "#e36969", "#d34bd6"]
    fig, ax = plt.subplots(figsize=(9, 5), dpi=120)
    ax.imshow(category_grid, origin="lower", extent=extent, cmap=ListedColormap(colors), vmin=0, vmax=6, alpha=0.82)
    ax.set_title("AutoOutlook HRRR/XGBoost SPC-style risk")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_xlim(-125, -66)
    ax.set_ylim(24, 50)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _publish_working_dir(working_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(working_dir), str(output_dir))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _valid_iso(cycle: HrrrCycle, forecast_hour: int) -> str:
    valid = cycle.cycle_time + timedelta(hours=int(forecast_hour))
    return valid.isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--forecast-hours", type=int, nargs="+", default=list(FORECAST_HOURS))
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--tile-stride", type=int, default=5)
    parser.add_argument("--no-spc-verify", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Run forever on a schedule.")
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    while True:
        metadata = run_pipeline(
            output_dir=args.output_dir,
            forecast_hours=args.forecast_hours,
            max_workers=args.max_workers,
            tile_stride=args.tile_stride,
            verify_spc=not args.no_spc_verify,
            preview=not args.no_preview,
        )
        print(json.dumps({
            "outputDir": str(args.output_dir),
            "cycle": metadata["cycle"],
            "generatedAtISO": metadata["generatedAtISO"],
            "latencyMs": metadata["latencyMs"],
        }, indent=2))
        if not args.loop:
            return
        time.sleep(max(60.0, args.interval_minutes * 60.0))


if __name__ == "__main__":
    main()
