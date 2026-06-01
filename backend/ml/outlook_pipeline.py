"""Scheduled HRRR-to-SPC-style gridded outlook artifact pipeline."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import requests

from backend import metpy_diagnostics as diag
from backend.hrrr_selected import (
    DEFAULT_CACHE_TTL_HOURS,
    DEFAULT_SELECTED_CACHE_DIR,
    HrrrCycle,
    HrrrCycleDetection,
    HrrrHourRef,
    OPTIONAL_HRRR_TERMS,
    REQUIRED_HRRR_TERMS,
    SELECTED_HRRR_TERMS,
    SelectedHrrrHour,
    fetch_selected_hrrr_hour_with_metadata,
    hour_ref,
    latest_available_hrrr_cycle_with_metadata,
)
from backend.bundle_builder import _hgt500_lines_from_field, _ingredients_at_point, _wind500_vectors_from_fields
from backend.ml.features import FEATURE_NAMES, feature_schema_hash
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    GriddedFeatures,
    apply_category_probability_ceiling,
    apply_environmental_probability_caps,
    apply_offshore_probability_suppression,
    apply_regional_strict_category_caps,
    category_counts,
    category_grid_from_probabilities,
    feature_stats,
    gridded_features_from_fields,
    hazard_probability_shapes_from_grids,
    merge_feature_collections,
    postprocess_category_grid,
    predict_hazard_grids,
    probability_tile,
    risk_polygons_from_grid,
)
from backend.ml.inference import model_status
from backend.ml.spc_verification import compare_prediction_to_spc, fetch_current_spc_day1_category


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return int(default)
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)


DEFAULT_ARTIFACT_ROOT = Path(os.environ.get(
    "AUTOOUTLOOK_ARTIFACT_ROOT",
    Path(__file__).resolve().parents[1] / "artifacts",
))
DEFAULT_OUTPUT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_ARTIFACT_DIR",
    DEFAULT_ARTIFACT_ROOT / "latest",
))
DEFAULT_INCREMENTAL_OUTPUT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_INCREMENTAL_ARTIFACT_DIR",
    DEFAULT_ARTIFACT_ROOT / "latest_incremental",
))
DEFAULT_INCREMENTAL_COMPLETE_OUTPUT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_INCREMENTAL_COMPLETE_ARTIFACT_DIR",
    DEFAULT_INCREMENTAL_OUTPUT_DIR.with_name(f"{DEFAULT_INCREMENTAL_OUTPUT_DIR.name}_complete"),
))
ALL_FORECAST_HOURS = tuple(range(49))
PRODUCTION_FORECAST_HOURS = tuple(list(range(19)) + list(range(21, 49, 3)))
FORECAST_HOURS = PRODUCTION_FORECAST_HOURS
CYCLE_POLICIES = ("complete-requested", "complete-48", "latest-startable")
DEFAULT_CYCLE_POLICY = "complete-requested"
DEFAULT_INCREMENTAL_CYCLE_POLICY = "latest-startable"
DEFAULT_HOUR_WORKERS = _env_int("AUTOOUTLOOK_HOUR_WORKERS", 4)
DEFAULT_RANGE_WORKERS = _env_int("AUTOOUTLOOK_RANGE_WORKERS", 6)
DEFAULT_GRID_STRIDE = _env_int("AUTOOUTLOOK_GRID_STRIDE", 4)
DEFAULT_GCS_LOCK_TTL_SECONDS = _env_int("AUTOOUTLOOK_RUN_LOCK_TTL_SECONDS", 5400)

FetchHourFn = Callable[[HrrrHourRef, requests.Session], tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]] | SelectedHrrrHour]
PredictorFn = Callable[[GriddedFeatures], dict[str, np.ndarray] | None]
SpcFetchFn = Callable[[requests.Session, Path | None], dict[str, Any]]
CycleDetectFn = Callable[[requests.Session, datetime | None], HrrrCycle | HrrrCycleDetection]


@dataclass(frozen=True)
class FetchedHour:
    lats: np.ndarray
    lons: np.ndarray
    fields: dict[str, np.ndarray]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IncrementalHourResult:
    forecast_hour: int
    hour_metadata: dict[str, Any]
    category_counts: dict[str, int]
    fetch_ms: int
    build_ms: int
    write_ms: int
    total_ms: int
    cache_hit: bool
    grid_shape: list[int] | None
    selected_byte_count: int | None


@dataclass(frozen=True)
class GcsRunLock:
    bucket_name: str
    blob_name: str
    generation: int | None


@dataclass(frozen=True)
class CloudRunTaskShard:
    index: int
    count: int


def run_pipeline(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    forecast_hours: Iterable[int] | None = None,
    now: datetime | None = None,
    max_workers: int | None = None,
    tile_stride: int | None = None,
    grid_stride: int | None = None,
    min_successful_hours: int = 8,
    cache_dir: Path | str | None = DEFAULT_SELECTED_CACHE_DIR,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    no_cache: bool = False,
    verify_spc: bool = True,
    preview: bool = True,
    cycle_policy: str = DEFAULT_CYCLE_POLICY,
    require_complete_hour: int | None = None,
    detect_cycle_fn: CycleDetectFn | None = None,
    fetch_hour_fn: FetchHourFn | None = None,
    predictor_fn: PredictorFn | None = None,
    spc_fetch_fn: SpcFetchFn | None = None,
    model_name: str = "hrrr",
) -> dict[str, Any]:
    """Generate deployable prediction artifacts, then optionally verify against SPC."""
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    output_dir = Path(output_dir)
    working_dir = output_dir.with_name(f"{output_dir.name}.tmp")
    if working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)
    hours = resolve_forecast_hours(forecast_hours, model_name=model_name)
    resolved_cycle_policy = resolve_cycle_policy(cycle_policy, incremental=False)
    required_forecast_hour = resolve_required_forecast_hour(hours, require_complete_hour, resolved_cycle_policy, model_name=model_name)
    effective_min_successful = min(max(1, int(min_successful_hours)), len(hours))
    range_workers = _resolve_worker_count(max_workers, DEFAULT_RANGE_WORKERS)
    grid_stride = _resolve_grid_stride(grid_stride)
    tile_stride = _resolve_tile_stride(tile_stride, grid_stride)
    failure_context: dict[str, Any] = {
        "requestedForecastHours": hours,
        "requiredForecastHourForCycle": required_forecast_hour,
        "requiredForecastHoursChecked": sorted({0, required_forecast_hour}),
        "cyclePolicy": resolved_cycle_policy,
        "minSuccessfulHours": int(min_successful_hours),
        "effectiveMinSuccessfulHours": effective_min_successful,
        "gridStride": grid_stride,
        "cache": _cache_metadata(cache_dir, cache_ttl_hours, no_cache),
    }

    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0"
    try:
        predictor = predictor_fn or predict_hazard_grids
        spc_fetch = spc_fetch_fn or fetch_current_spc_day1_category

        print(f"[cycle check] requested hours require f{required_forecast_hour:02d}", flush=True)
        detection = _detect_hrrr_cycle(session, now, required_forecast_hour, detect_cycle_fn, model_name=model_name)
        cycle, cycle_detection_metadata = _normalize_cycle_detection(
            detection,
            hours,
            required_forecast_hour,
            resolved_cycle_policy,
            require_complete_hour,
        )
        print(f"[cycle selected] {cycle.label}", flush=True)
        failure_context["cycle"] = cycle.label
        failure_context["cycleDetection"] = cycle_detection_metadata
        failure_context.update(_cycle_detection_artifact_fields(cycle_detection_metadata))
        model = model_status()
        failure_context["model"] = model
        if not model.get("active"):
            raise RuntimeError(f"ML model inactive; refusing deployable outlook generation: {model.get('reason', 'unknown')}")

        raw_hours, failed_hours = _fetch_hours(
            cycle,
            hours,
            session,
            fetch_hour_fn,
            max_workers=range_workers,
            cache_dir=cache_dir,
            cache_ttl_hours=cache_ttl_hours,
            no_cache=no_cache,
            grid_stride=grid_stride,
            model_name=model_name,
        )
        if len(raw_hours) < effective_min_successful:
            failure_context["failedHours"] = failed_hours
            failure_context["successfulForecastHours"] = sorted(raw_hours)
            raise RuntimeError(
                f"Only {len(raw_hours)} HRRR hours fetched successfully; "
                f"minimum required is {effective_min_successful}"
            )

        hour_artifacts = []
        hourly_collections: list[dict[str, Any]] = []
        category_grids: list[np.ndarray] = []
        risk_map_category_grids: list[np.ndarray] = []
        probability_grids: list[dict[str, np.ndarray]] = []
        feature_grids: list[dict[str, np.ndarray]] = []
        successful_hours: list[int] = []
        base_lats: np.ndarray | None = None
        base_lons: np.ndarray | None = None

        for forecast_hour in sorted(raw_hours):
            fetched = raw_hours[forecast_hour]
            try:
                built = _build_hour_artifact(
                    cycle,
                    forecast_hour,
                    fetched,
                    predictor,
                    model,
                    tile_stride,
                )
            except Exception as exc:  # noqa: BLE001
                failed_hours.append({
                    "forecastHour": forecast_hour,
                    "stage": "prediction",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            if base_lats is None:
                base_lats = np.asarray(fetched.lats, dtype=float)
                base_lons = np.asarray(fetched.lons, dtype=float)
            category_grids.append(built["categoryGrid"])
            risk_map_category_grids.append(built["riskMapCategoryGrid"])
            probability_grids.append(built["probabilities"])
            feature_grids.append(built["features"].raw)
            hourly_collections.append(built["polygons"])
            successful_hours.append(forecast_hour)
            hour_artifacts.append({
                "forecastHour": forecast_hour,
                "validTimeISO": built["validTimeISO"],
                "categoryCounts": category_counts(built["categoryGrid"]),
                "categoryCountsBeforeCaps": category_counts(built["categoryGridBeforeCaps"]),
                "categoryCountsAfterCaps": category_counts(built["categoryGridAfterCaps"]),
                "categoryCountsAfterSmoothing": category_counts(built["categoryGrid"]),
                "riskMapCategoryCounts": category_counts(built["riskMapCategoryGrid"]),
                "probabilityStats": built["probabilityReport"],
                "postProcessing": built["postProcessingReport"],
                "featureStats": feature_stats(built["features"]),
                "tile": built["tile"],
                "fetch": fetched.metadata,
            })

        if len(successful_hours) < effective_min_successful:
            failure_context["failedHours"] = failed_hours
            failure_context["successfulForecastHours"] = successful_hours
            raise RuntimeError(
                f"Only {len(successful_hours)} HRRR hours produced prediction output; "
                f"minimum required is {effective_min_successful}"
            )

        if base_lats is None or base_lons is None or not category_grids:
            raise RuntimeError("No HRRR forecast hours produced gridded category output")

        aggregate_grid = np.maximum.reduce(category_grids)
        aggregate_risk_map_grid = np.maximum.reduce(risk_map_category_grids)
        aggregate_probabilities = _aggregate_probabilities(probability_grids)
        aggregate_features = _aggregate_feature_grids(feature_grids)
        aggregate_polygons = risk_polygons_from_grid(
            base_lats,
            base_lons,
            aggregate_risk_map_grid,
            forecast_hour=-1,
            valid_time_iso=f"{_valid_iso(cycle, min(successful_hours))}/{_valid_iso(cycle, max(successful_hours))}",
        )
        all_hourly_polygons = merge_feature_collections(hourly_collections)
        probability_tiles = {
            "cycle": cycle.label,
            "featureSchemaHash": feature_schema_hash(),
            "riskLabels": list(SPC_RISK_LABELS),
            "gridStride": grid_stride,
            "tileStride": tile_stride,
            "environmentalCapsApplied": True,
            "categoryConsistencyCapsApplied": True,
            "hours": [{k: v for k, v in artifact.items() if k != "featureStats"} for artifact in hour_artifacts],
        }

        _write_json(working_dir / "risk_polygons.geojson", all_hourly_polygons)
        _write_json(working_dir / "aggregate_risk_polygons.geojson", aggregate_polygons)
        _write_json(working_dir / "probability_tiles.json", probability_tiles)

        preview_file = None
        if preview:
            preview_file = _render_preview(working_dir / "preview.png", base_lats, base_lons, aggregate_risk_map_grid)

        verification_summary = None
        if verify_spc:
            try:
                spc = spc_fetch(session, working_dir)
                verification_grid = _aggregate_for_spc_window(cycle, successful_hours, category_grids, spc.get("categoryGeojson"))
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
            except Exception as exc:  # noqa: BLE001
                verification_summary = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "spcFetchedAfterPredictionArtifacts": True,
                    "leakageGuard": "Current official SPC outlook is fetched only after prediction artifacts are generated.",
                }
            _write_json(working_dir / "verification_summary.json", verification_summary)

        metadata = {
            "generatedAtISO": _now_iso(),
            "cycle": cycle.label,
            "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
            "cycleMetadata": _cycle_metadata(cycle),
            "cycleDetection": cycle_detection_metadata,
            "forecastHours": successful_hours,
            "requestedForecastHours": hours,
            **_cycle_detection_artifact_fields(cycle_detection_metadata),
            "successfulForecastHours": successful_hours,
            "failedHours": failed_hours,
            "minSuccessfulHours": int(min_successful_hours),
            "effectiveMinSuccessfulHours": effective_min_successful,
            "selectedHrrrTerms": list(SELECTED_HRRR_TERMS),
            "requiredHrrrTerms": list(REQUIRED_HRRR_TERMS),
            "optionalHrrrTerms": list(OPTIONAL_HRRR_TERMS),
            "featureNames": list(FEATURE_NAMES),
            "featureSchemaHash": feature_schema_hash(),
            "model": model,
            "riskLabels": list(SPC_RISK_LABELS),
            "gridStride": grid_stride,
            "tileStride": tile_stride,
            "cache": _cache_metadata(cache_dir, cache_ttl_hours, no_cache, hour_artifacts),
            "artifacts": {
                "riskPolygons": "risk_polygons.geojson",
                "aggregateRiskPolygons": "aggregate_risk_polygons.geojson",
                "probabilityTiles": "probability_tiles.json",
                "metadata": "metadata.json",
                "preview": preview_file.name if preview_file else None,
                "verificationSummary": "verification_summary.json" if verification_summary else None,
            },
            "aggregateCategoryCounts": category_counts(aggregate_grid),
            "riskMapAggregateCategoryCounts": category_counts(aggregate_risk_map_grid),
            "postProcessing": _aggregate_postprocessing(hour_artifacts),
            "hours": [
                {k: v for k, v in artifact.items() if k not in ("tile",)}
                for artifact in hour_artifacts
            ],
            "hourFetchMetadata": [artifact["fetch"] for artifact in hour_artifacts],
            "spcVerification": verification_summary,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "leakageGuard": "Current official SPC outlook is fetched only after model prediction artifacts are written.",
        }
        _write_json(working_dir / "metadata.json", metadata)
        _publish_working_dir(working_dir, output_dir)
        return metadata
    except Exception as exc:
        _write_failed_run_metadata(output_dir, working_dir, failure_context, exc, started)
        raise
    finally:
        session.close()
        if working_dir.exists():
            shutil.rmtree(working_dir, ignore_errors=True)


def run_incremental_pipeline(
    output_dir: Path = DEFAULT_INCREMENTAL_OUTPUT_DIR,
    forecast_hours: Iterable[int] | None = None,
    process_forecast_hours: Iterable[int] | None = None,
    artifact_generation_id: str | None = None,
    now: datetime | None = None,
    max_workers: int | None = None,
    hour_workers: int | None = None,
    range_workers: int | None = None,
    tile_stride: int | None = None,
    grid_stride: int | None = None,
    cache_dir: Path | str | None = DEFAULT_SELECTED_CACHE_DIR,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    no_cache: bool = False,
    hour_delay_seconds: float = 0.0,
    stop_after_hour: int | None = None,
    continue_on_hour_failure: bool = True,
    force: bool = False,
    cycle_policy: str = DEFAULT_INCREMENTAL_CYCLE_POLICY,
    require_complete_hour: int | None = None,
    detect_cycle_fn: CycleDetectFn | None = None,
    fetch_hour_fn: FetchHourFn | None = None,
    predictor_fn: PredictorFn | None = None,
    verify_spc: bool = False,
    spc_fetch_fn: Callable[[requests.Session, Path | None], dict[str, Any]] | None = None,
    publish_gcs_bucket: str | None = None,
    publish_gcs_prefix: str = "",
    model_name: str = "hrrr",
) -> dict[str, Any]:
    """Publish per-hour artifacts as soon as each HRRR hour is processed."""
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "hours").mkdir(parents=True, exist_ok=True)
    hours = resolve_forecast_hours(forecast_hours, model_name=model_name)
    if process_forecast_hours is None:
        process_requested_hours = list(hours)
    else:
        process_requested_hours = _resolve_process_forecast_hours(process_forecast_hours, hours)
    requested_force_hours = set(process_requested_hours)
    resolved_cycle_policy = resolve_cycle_policy(cycle_policy, incremental=True)
    required_forecast_hour = resolve_required_forecast_hour(hours, require_complete_hour, resolved_cycle_policy, model_name=model_name)
    hour_workers = _resolve_worker_count(hour_workers, DEFAULT_HOUR_WORKERS)
    range_workers = _resolve_worker_count(range_workers if range_workers is not None else max_workers, DEFAULT_RANGE_WORKERS)
    grid_stride = _resolve_grid_stride(grid_stride)
    tile_stride = _resolve_tile_stride(tile_stride, grid_stride)
    artifact_generation_id = artifact_generation_id or os.environ.get("AUTOOUTLOOK_ARTIFACT_GENERATION_ID") or os.environ.get("CLOUD_RUN_EXECUTION") or ""
    sharded_run = set(process_requested_hours) != set(hours)

    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0 incremental"
    ready_hours: list[int] = []
    failed_hours: list[dict[str, Any]] = []
    try:
        predictor = predictor_fn or predict_hazard_grids
        print(f"[cycle check] requested hours require f{required_forecast_hour:02d}", flush=True)
        detection = _detect_hrrr_cycle(session, now, required_forecast_hour, detect_cycle_fn, model_name=model_name)
        cycle, cycle_detection_metadata = _normalize_cycle_detection(
            detection,
            hours,
            required_forecast_hour,
            resolved_cycle_policy,
            require_complete_hour,
        )
        print(f"[cycle selected] HRRR {cycle.run_cycle:02d}Z {cycle.run_date}", flush=True)
        model = model_status()
        if not model.get("active"):
            raise RuntimeError(f"ML model inactive; refusing incremental outlook generation: {model.get('reason', 'unknown')}")
        previous_index = _read_incremental_index_payload(output_dir)
        existing_index = previous_index if previous_index.get("cycle") == cycle.label else None
        previous_cycle = previous_index.get("cycle")
        if existing_index is None and previous_cycle and previous_cycle != cycle.label:
            print(
                f"[incremental reset] clearing stale hour artifacts from {previous_cycle} before writing {cycle.label}",
                flush=True,
            )
            _cache_previous_incremental_cycle(output_dir)
            _clear_incremental_hour_artifacts(output_dir)
        cycle_time_iso = cycle.cycle_time.isoformat().replace("+00:00", "Z")
        disk_ready = [
            hour
            for hour in hours
            if _incremental_hour_ready(output_dir, hour, cycle_time_iso=cycle_time_iso)
        ]
        if disk_ready:
            print(
                f"[incremental reuse] ready hours from disk: "
                f"{','.join(f'F{hour:02d}' for hour in disk_ready)}",
                flush=True,
            )
            ready_hours.extend(hour for hour in disk_ready if hour not in ready_hours)
        if existing_index is not None:
            existing_ready = [
                hour for hour in _int_list(existing_index.get("readyForecastHours"))
                if _incremental_hour_ready(output_dir, hour, cycle_time_iso=cycle_time_iso)
            ]
            existing_ready = sorted({
                *existing_ready,
                *disk_ready,
            })
            existing_failed = [
                item for item in existing_index.get("failedHours", [])
                if (
                    isinstance(item, Mapping)
                    and "forecastHour" in item
                    and int(item.get("forecastHour", -999)) not in existing_ready
                )
            ]
            ready_hours.extend(hour for hour in existing_ready if hour not in ready_hours)
            failed_hours.extend(dict(item) for item in existing_failed)
            hours = sorted({
                *hours,
                *_int_list(existing_index.get("requestedForecastHours")),
                *existing_ready,
                *_int_list(existing_index.get("failedForecastHours")),
            })

        artifact_changes = False
        spc_verification: dict[str, Any] | None = None

        def write_index(status: str) -> dict[str, Any]:
            ready = sorted({int(hour) for hour in ready_hours})
            failed = sorted({int(item["forecastHour"]) for item in failed_hours})
            failed_items = sorted(failed_hours, key=lambda item: int(item.get("forecastHour", -999)))
            pending = [hour for hour in hours if hour not in ready and hour not in failed]
            payload = {
                "cycle": cycle.label,
                "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
                "cycleMetadata": _cycle_metadata(cycle),
                "cycleDetection": cycle_detection_metadata,
                "generatedAtISO": _now_iso(),
                "mode": "incremental",
                "requestedForecastHours": hours,
                "processForecastHours": process_requested_hours,
                "artifactGenerationId": artifact_generation_id or None,
                "artifactChangesThisRun": artifact_changes,
                **_cycle_detection_artifact_fields(cycle_detection_metadata),
                "readyForecastHours": ready,
                "failedForecastHours": failed,
                "failedHours": failed_items,
                "pendingForecastHours": pending,
                "latestReadyForecastHour": ready[-1] if ready else None,
                "status": status,
                "gridStride": grid_stride,
                "tileStride": tile_stride,
                "hourWorkers": hour_workers,
                "rangeWorkers": range_workers,
                "featureSchemaHash": feature_schema_hash(),
                "featureNames": list(FEATURE_NAMES),
                "selectedHrrrTerms": list(SELECTED_HRRR_TERMS),
                "requiredHrrrTerms": list(REQUIRED_HRRR_TERMS),
                "optionalHrrrTerms": list(OPTIONAL_HRRR_TERMS),
                "riskLabels": list(SPC_RISK_LABELS),
                "model": model,
                "artifacts": {
                    "index": "index.json",
                    "metadata": "metadata.json",
                    "hours": "hours/fXX/{risk_polygons.geojson,hazard_probability_shapes.geojson,probability_tile.json,upper_air_overlay.json,metadata.json}",
                },
                "latencyMs": int((time.perf_counter() - started) * 1000),
            }
            if spc_verification is not None:
                payload["spcVerification"] = spc_verification
                payload["artifacts"] = {
                    **payload["artifacts"],
                    "verificationSummary": "verification_summary.json",
                    "spcDay1Category": "spc_day1_cat.geojson",
                }
            _write_json(output_dir / "index.json", payload)
            _write_json(output_dir / "metadata.json", payload)
            return payload

        index = write_index("running")
        process_hours: list[int] = []
        for forecast_hour in process_requested_hours:
            if stop_after_hour is not None and forecast_hour > stop_after_hour:
                continue
            should_force_hour = force and forecast_hour in requested_force_hours
            if not should_force_hour and forecast_hour in ready_hours and _incremental_hour_ready(output_dir, forecast_hour):
                print(f"[incremental skip] F{forecast_hour:02d} already ready", flush=True)
                continue
            failed_hours = [
                item for item in failed_hours
                if int(item.get("forecastHour", -999)) != int(forecast_hour)
            ]
            process_hours.append(forecast_hour)

        pending_from: int | None = None
        abort_exc: Exception | None = None
        if process_hours:
            worker_count = min(hour_workers, len(process_hours))
            print(
                f"[incremental workers] hours={worker_count} ranges={range_workers} "
                f"gridStride={grid_stride} tileStride={tile_stride}",
                flush=True,
            )
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_hour = {}
                for forecast_hour in process_hours:
                    future = executor.submit(
                        _process_incremental_hour,
                        output_dir,
                        cycle,
                        forecast_hour,
                        fetch_hour_fn,
                        predictor,
                        model,
                        range_workers,
                        cache_dir,
                        cache_ttl_hours,
                        no_cache,
                        grid_stride,
                        tile_stride,
                        artifact_generation_id,
                        model_name,
                    )
                    future_to_hour[future] = forecast_hour
                    if hour_delay_seconds > 0:
                        time.sleep(hour_delay_seconds)

                for future in as_completed(future_to_hour):
                    forecast_hour = future_to_hour[future]
                    if abort_exc is not None:
                        continue
                    if pending_from is not None and forecast_hour >= pending_from:
                        continue
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        if resolved_cycle_policy == "latest-startable" and isinstance(exc, FileNotFoundError):
                            pending_from = forecast_hour if pending_from is None else min(pending_from, forecast_hour)
                            print(
                                f"[incremental pending] F{forecast_hour:02d} HRRR hour not ready; "
                                "leaving this and later requested hours pending",
                                flush=True,
                            )
                            for pending_future, pending_hour in future_to_hour.items():
                                if pending_hour >= pending_from and not pending_future.done():
                                    pending_future.cancel()
                            index = write_index("partial")
                            continue
                        failure = {
                            "forecastHour": forecast_hour,
                            "stage": "incremental",
                            "error": f"{type(exc).__name__}: {exc}",
                            "generatedAtISO": _now_iso(),
                        }
                        failed_hours.append(failure)
                        _write_failed_incremental_hour(output_dir, forecast_hour, failure)
                        print(f"[incremental fail] F{forecast_hour:02d} {failure['error']}", flush=True)
                        index = write_index("running")
                        if not continue_on_hour_failure:
                            abort_exc = exc
                            for pending_future in future_to_hour:
                                if not pending_future.done():
                                    pending_future.cancel()
                        continue

                    ready_hours.append(result.forecast_hour)
                    print(
                        f"[incremental ok] F{result.forecast_hour:02d} "
                        f"fetch={result.fetch_ms}ms "
                        f"build={result.build_ms}ms "
                        f"write={result.write_ms}ms "
                        f"total={result.total_ms}ms "
                        f"cache={str(result.cache_hit).lower()} "
                        f"shape={result.grid_shape} "
                        f"bytes={result.selected_byte_count} "
                        f"cat={result.category_counts}",
                        flush=True,
                    )
                    index = write_index("running")

        artifact_changes = bool(process_hours)
        if abort_exc is not None:
            write_index("failed")
            raise abort_exc
        if pending_from is not None:
            ready_hours[:] = sorted({hour for hour in ready_hours if hour < pending_from})
            failed_hours[:] = [
                item
                for item in failed_hours
                if int(item.get("forecastHour", -999)) < pending_from
            ]
            for hour in hours:
                if hour >= pending_from:
                    _clear_incremental_hour_artifact(output_dir, hour)
            _write_pending_incremental_hour(output_dir, pending_from, FileNotFoundError(hour_ref(cycle, pending_from).idx_url))
            index = write_index("partial")
        else:
            all_requested_ready = set(hours).issubset({int(hour) for hour in ready_hours})
            status = "complete" if all_requested_ready and stop_after_hour is None and not failed_hours else "partial"
            index = write_index(status)
        if verify_spc and index.get("status") == "complete":
            spc_verification = _write_incremental_spc_verification(
                output_dir,
                index,
                cycle,
                hours,
                session,
                spc_fetch_fn or fetch_current_spc_day1_category,
            )
            artifact_changes = True
            index = write_index(str(index.get("status") or "complete"))
        complete_dir = _incremental_complete_output_dir(output_dir)
        if sharded_run:
            if publish_gcs_bucket and process_hours:
                _publish_incremental_shard_artifacts_to_gcs(
                    output_dir,
                    index,
                    process_hours,
                    publish_gcs_bucket,
                    publish_gcs_prefix,
                )
        else:
            complete_snapshot_ready = _incremental_index_covers_requested_hours(
                _read_incremental_index_payload(complete_dir),
                hours,
            )
            if artifact_changes or not complete_snapshot_ready:
                _publish_complete_incremental_snapshot(output_dir, index, hours)
                if publish_gcs_bucket:
                    _publish_incremental_artifacts_to_gcs(output_dir, index, hours, publish_gcs_bucket, publish_gcs_prefix)
            elif publish_gcs_bucket:
                print("[gcs publish skip] no artifact changes; existing complete snapshot is current", flush=True)
        return index
    except Exception:
        if output_dir.exists():
            failed = sorted({int(item["forecastHour"]) for item in failed_hours})
            failed_items = sorted(failed_hours, key=lambda item: int(item.get("forecastHour", -999)))
            ready = sorted({int(hour) for hour in ready_hours})
            _write_json(output_dir / "index.json", {
                "generatedAtISO": _now_iso(),
                "mode": "incremental",
                "requestedForecastHours": hours,
                "processForecastHours": process_requested_hours if "process_requested_hours" in locals() else hours,
                "artifactGenerationId": artifact_generation_id if "artifact_generation_id" in locals() and artifact_generation_id else None,
                "artifactChangesThisRun": artifact_changes if "artifact_changes" in locals() else None,
                **_cycle_detection_artifact_fields(cycle_detection_metadata if "cycle_detection_metadata" in locals() else {
                    "requestedForecastHours": hours,
                    "requiredForecastHourForCycle": required_forecast_hour,
                    "requiredForecastHoursChecked": sorted({0, required_forecast_hour}),
                }),
                "readyForecastHours": ready,
                "failedForecastHours": failed,
                "failedHours": failed_items,
                "pendingForecastHours": [hour for hour in hours if hour not in ready and hour not in failed],
                "status": "failed",
                "previousLatestPreserved": DEFAULT_OUTPUT_DIR.exists(),
                "gridStride": grid_stride if "grid_stride" in locals() else None,
                "tileStride": tile_stride if "tile_stride" in locals() else None,
                "hourWorkers": hour_workers if "hour_workers" in locals() else None,
                "rangeWorkers": range_workers if "range_workers" in locals() else None,
                "latencyMs": int((time.perf_counter() - started) * 1000),
            })
        raise
    finally:
        session.close()


def _fetch_hours(
    cycle: HrrrCycle,
    hours: list[int],
    session: requests.Session,
    fetch_hour: FetchHourFn | None,
    max_workers: int,
    cache_dir: Path | str | None,
    cache_ttl_hours: float,
    no_cache: bool,
    grid_stride: int,
    model_name: str = "hrrr",
) -> tuple[dict[int, FetchedHour], list[dict[str, Any]]]:
    out: dict[int, FetchedHour] = {}
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        future_to_hour = {
            executor.submit(
                _fetch_one_hour,
                hour_ref(cycle, hour),
                session,
                fetch_hour,
                max_workers,
                cache_dir,
                cache_ttl_hours,
                no_cache,
                grid_stride,
                model_name,
            ): hour
            for hour in hours
        }
        for future in as_completed(future_to_hour):
            hour = future_to_hour[future]
            try:
                out[hour] = future.result()
                meta = out[hour].metadata
                print(
                    f"[fetch ok] F{hour:02d} "
                    f"cache={meta.get('cacheHit')} "
                    f"shape={meta.get('gridShape')} "
                    f"bytes={meta.get('selectedByteCount')} "
                    f"latency={meta.get('fetchLatencyMs')}ms",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[fetch fail] F{hour:02d} {type(exc).__name__}: {exc}", flush=True)
                failures.append({
                    "forecastHour": hour,
                    "stage": "fetch",
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return out, sorted(failures, key=lambda item: int(item["forecastHour"]))


def _fetch_one_hour(
    ref: HrrrHourRef,
    session: requests.Session,
    fetch_hour: FetchHourFn | None,
    max_workers: int,
    cache_dir: Path | str | None,
    cache_ttl_hours: float,
    no_cache: bool,
    grid_stride: int,
    model_name: str = "hrrr",
) -> FetchedHour:
    if fetch_hour is None:
        if model_name == "ecmwf":
            from backend.ecmwf_selected import fetch_selected_ecmwf_hour
            result = fetch_selected_ecmwf_hour(
                run_date=ref.run_date,
                run_cycle=ref.run_cycle,
                forecast_hour=ref.forecast_hour,
                cache_dir=cache_dir,
            )
        else:
            result = fetch_selected_hrrr_hour_with_metadata(
                ref,
                session=session,
                max_workers=max_workers,
                cache_dir=cache_dir,
                cache_ttl_hours=cache_ttl_hours,
                no_cache=no_cache,
                grid_stride=grid_stride,
            )
    else:
        result = fetch_hour(ref, session)
    return _normalize_fetched_hour(ref, result)


def _process_incremental_hour(
    output_dir: Path,
    cycle: HrrrCycle,
    forecast_hour: int,
    fetch_hour_fn: FetchHourFn | None,
    predictor: PredictorFn,
    model: Mapping[str, Any],
    range_workers: int,
    cache_dir: Path | str | None,
    cache_ttl_hours: float,
    no_cache: bool,
    grid_stride: int,
    tile_stride: int,
    artifact_generation_id: str = "",
    model_name: str = "hrrr",
) -> IncrementalHourResult:
    hour_started = time.perf_counter()
    ref = hour_ref(cycle, forecast_hour)
    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0 incremental-hour"
    try:
        fetch_started = time.perf_counter()
        fetched = _fetch_one_hour(
            ref,
            session,
            fetch_hour_fn,
            max_workers=range_workers,
            cache_dir=cache_dir,
            cache_ttl_hours=cache_ttl_hours,
            no_cache=no_cache,
            grid_stride=grid_stride,
            model_name=model_name,
        )
        fetch_ms = int((time.perf_counter() - fetch_started) * 1000)

        build_started = time.perf_counter()
        built = _build_hour_artifact(cycle, forecast_hour, fetched, predictor, model, tile_stride)
        build_ms = int((time.perf_counter() - build_started) * 1000)

        write_started = time.perf_counter()
        hour_dir = output_dir / "hours" / f"f{forecast_hour:02d}"
        hour_dir.mkdir(parents=True, exist_ok=True)
        counts = category_counts(built["categoryGrid"])
        timing = {
            "fetchMs": fetch_ms,
            "buildMs": build_ms,
            "writeMs": 0,
            "totalMs": 0,
        }
        hour_metadata = {
            "forecastHour": forecast_hour,
            "validTimeISO": built["validTimeISO"],
            "status": "ready",
            "generatedAtISO": _now_iso(),
            "artifactGenerationId": artifact_generation_id or None,
            "latencyMs": 0,
            "timing": timing,
            "categoryCounts": counts,
            "categoryCountsBeforeCaps": category_counts(built["categoryGridBeforeCaps"]),
            "categoryCountsAfterCaps": category_counts(built["categoryGridAfterCaps"]),
            "categoryCountsAfterSmoothing": counts,
            "riskMapCategoryCounts": category_counts(built["riskMapCategoryGrid"]),
            "probabilityStats": built["probabilityReport"],
            "postProcessing": built["postProcessingReport"],
            "region": built["region"],
            "ingredients": built["ingredients"],
            "ingredientSample": built["ingredientSample"],
            "fetch": fetched.metadata,
            "artifacts": {
                "riskPolygons": "risk_polygons.geojson",
                "probabilityTile": "probability_tile.json",
                "hazardProbabilityShapes": "hazard_probability_shapes.geojson",
                "upperAirOverlay": "upper_air_overlay.json",
                "metadata": "metadata.json",
            },
            "upperAirOverlay": built["upperAirOverlay"]["metadata"],
        }
        _write_json(hour_dir / "risk_polygons.geojson", built["polygons"])
        _write_json(hour_dir / "hazard_probability_shapes.geojson", built["hazardProbabilityShapes"])
        _write_json(hour_dir / "probability_tile.json", built["tile"])
        _write_json(hour_dir / "upper_air_overlay.json", built["upperAirOverlay"])
        write_ms = int((time.perf_counter() - write_started) * 1000)
        total_ms = int((time.perf_counter() - hour_started) * 1000)
        timing.update({"writeMs": write_ms, "totalMs": total_ms})
        hour_metadata["latencyMs"] = total_ms
        _write_json(hour_dir / "metadata.json", hour_metadata)

        grid_shape = fetched.metadata.get("gridShape")
        return IncrementalHourResult(
            forecast_hour=forecast_hour,
            hour_metadata=hour_metadata,
            category_counts=counts,
            fetch_ms=fetch_ms,
            build_ms=build_ms,
            write_ms=write_ms,
            total_ms=total_ms,
            cache_hit=bool(fetched.metadata.get("cacheHit")),
            grid_shape=list(grid_shape) if isinstance(grid_shape, list) else None,
            selected_byte_count=_optional_int(fetched.metadata.get("selectedByteCount")),
        )
    finally:
        session.close()


def _write_pending_incremental_hour(output_dir: Path, forecast_hour: int, exc: Exception) -> None:
    hour_dir = output_dir / "hours" / f"f{forecast_hour:02d}"
    hour_dir.mkdir(parents=True, exist_ok=True)
    pending_metadata = {
        "forecastHour": forecast_hour,
        "status": "pending",
        "stage": "incremental",
        "reason": "hrrr_hour_not_ready",
        "error": f"{type(exc).__name__}: {exc}",
        "generatedAtISO": _now_iso(),
    }
    _write_json(hour_dir / "metadata.json", pending_metadata)


def _write_failed_incremental_hour(output_dir: Path, forecast_hour: int, failure: Mapping[str, Any]) -> None:
    hour_dir = output_dir / "hours" / f"f{forecast_hour:02d}"
    hour_dir.mkdir(parents=True, exist_ok=True)
    _write_json(hour_dir / "metadata.json", {**dict(failure), "status": "failed"})


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_process_forecast_hours(
    process_forecast_hours: Iterable[int],
    requested_forecast_hours: Iterable[int],
) -> list[int]:
    requested = set(resolve_forecast_hours(requested_forecast_hours))
    selected = sorted({int(hour) for hour in process_forecast_hours})
    invalid = [hour for hour in selected if hour < 0 or hour > 48]
    if invalid:
        raise ValueError(f"Forecast hours must be in 0..48: {invalid}")
    unknown = [hour for hour in selected if hour not in requested]
    if unknown:
        raise ValueError(f"Process forecast hours must be a subset of requested forecast hours: {unknown}")
    return selected


def _normalize_fetched_hour(
    ref: HrrrHourRef,
    result: tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]] | SelectedHrrrHour,
) -> FetchedHour:
    if isinstance(result, SelectedHrrrHour) or type(result).__name__ == "SelectedEcmwfHour":
        return FetchedHour(result.lats, result.lons, result.fields, result.metadata)
    lats, lons, fields = result
    return FetchedHour(
        np.asarray(lats, dtype=float),
        np.asarray(lons, dtype=float),
        {key: np.asarray(value, dtype=float) for key, value in fields.items()},
        {
            "forecastHour": ref.forecast_hour,
            "runDate": ref.run_date,
            "runCycle": ref.run_cycle,
            "validTimeISO": ref.valid_time.isoformat().replace("+00:00", "Z"),
            "source": "test_or_custom_fetcher",
            "cacheHit": False,
            "decodedFieldNames": sorted(fields),
            "gridShape": list(np.asarray(next(iter(fields.values()))).shape) if fields else [],
        },
    )


def _build_hour_artifact(
    cycle: HrrrCycle,
    forecast_hour: int,
    fetched: FetchedHour,
    predictor: PredictorFn,
    model: Mapping[str, Any],
    tile_stride: int,
) -> dict[str, Any]:
    features = gridded_features_from_fields(fetched.fields, forecast_hour, lats=fetched.lats, lons=fetched.lons)
    raw_probabilities = predictor(features)
    if raw_probabilities is None:
        raise RuntimeError("ML hazard model returned no gridded probabilities")
    category_before_caps = category_grid_from_probabilities(raw_probabilities, features, model)
    cap_result = apply_environmental_probability_caps(
        raw_probabilities,
        features,
        model,
        lats=fetched.lats,
        lons=fetched.lons,
    )
    category_after_caps = category_grid_from_probabilities(cap_result.probabilities, features, model)
    post_result = postprocess_category_grid(
        category_after_caps,
        cap_result.probabilities,
        features,
        fetched.lats,
        fetched.lons,
    )
    category_probability_result = apply_category_probability_ceiling(cap_result.probabilities, post_result.category_grid)
    final_probability_result = apply_offshore_probability_suppression(
        category_probability_result.probabilities,
        fetched.lats,
        fetched.lons,
    )
    initial_risk_map_probability_result = apply_offshore_probability_suppression(
        cap_result.probabilities,
        fetched.lats,
        fetched.lons,
    )
    risk_map_category_grid = category_grid_from_probabilities(initial_risk_map_probability_result.probabilities, features, model)
    risk_map_category_grid = apply_regional_strict_category_caps(risk_map_category_grid, fetched.lats, fetched.lons)
    risk_map_category_probability_result = apply_category_probability_ceiling(
        initial_risk_map_probability_result.probabilities,
        risk_map_category_grid,
    )
    risk_map_probability_result = apply_offshore_probability_suppression(
        risk_map_category_probability_result.probabilities,
        fetched.lats,
        fetched.lons,
    )
    probability_report = {
        **cap_result.report,
        "environmentalCappedProbabilityMax": cap_result.report.get("cappedProbabilityMax"),
        "cappedProbabilityMax": final_probability_result.report.get("offshoreSuppressedProbabilityMax"),
        **category_probability_result.report,
        **final_probability_result.report,
    }
    valid_time_iso = _valid_iso(cycle, forecast_hour)
    polygons = risk_polygons_from_grid(fetched.lats, fetched.lons, risk_map_category_grid, forecast_hour, valid_time_iso)
    hazard_shapes = hazard_probability_shapes_from_grids(
        fetched.lats,
        fetched.lons,
        risk_map_probability_result.probabilities,
        risk_map_category_grid,
        forecast_hour,
        valid_time_iso,
    )
    tile = probability_tile(
        fetched.lats,
        fetched.lons,
        final_probability_result.probabilities,
        post_result.category_grid,
        forecast_hour,
        valid_time_iso,
        stride=tile_stride,
    )
    tile["riskShapes"] = polygons
    tile["hazardProbabilityShapes"] = hazard_shapes
    upper_air_overlay = _upper_air_overlay_from_fetched(cycle, forecast_hour, fetched, valid_time_iso)
    region = _region_from_max_risk_grid(
        fetched.lats,
        fetched.lons,
        post_result.category_grid,
        final_probability_result.probabilities,
        polygons,
    )
    ingredients, ingredient_sample = _point_sampled_ingredients(features, fetched.lats, fetched.lons, region)
    return {
        "features": features,
        "rawProbabilities": raw_probabilities,
        "probabilities": final_probability_result.probabilities,
        "probabilityReport": probability_report,
        "categoryGridBeforeCaps": category_before_caps,
        "categoryGridAfterCaps": category_after_caps,
        "categoryGrid": post_result.category_grid,
        "riskMapCategoryGrid": risk_map_category_grid,
        "postProcessingReport": post_result.report,
        "validTimeISO": valid_time_iso,
        "polygons": polygons,
        "region": region,
        "ingredients": ingredients,
        "ingredientSample": ingredient_sample,
        "hazardProbabilityShapes": hazard_shapes,
        "tile": tile,
        "upperAirOverlay": upper_air_overlay,
    }


def _forecast_category_from_grid(grid: np.ndarray) -> str:
    counts = category_counts(grid)
    minimum_cells = {
        "NONE": 0,
        "TSTM": 1,
        "MRGL": 100,
        "SLGT": 500,
        "ENH": 1200,
        "MDT": 2500,
        "HIGH": 4500,
    }
    best = "NONE"
    for label in SPC_RISK_LABELS:
        if int(counts.get(label, 0) or 0) >= minimum_cells.get(label, 0):
            best = label
    return "TSTM" if best == "NONE" else best


def _frontend_category(category: str) -> str:
    return "MOD" if category == "MDT" else "TSTM" if category == "NONE" else category


def _region_from_max_risk_grid(
    lats: np.ndarray,
    lons: np.ndarray,
    category_grid: np.ndarray,
    probabilities: Mapping[str, np.ndarray],
    fallback_polygons: Mapping[str, Any],
) -> dict[str, Any]:
    category_arr = np.asarray(category_grid, dtype=np.int16)
    lat_grid, lon_grid = _lat_lon_grids(lats, lons)
    if category_arr.ndim != 2 or lat_grid.shape != category_arr.shape or lon_grid.shape != category_arr.shape:
        return _region_from_risk_polygons(fallback_polygons, _forecast_category_from_grid(category_arr))

    focus = _prioritized_focus_probability_grid(probabilities, category_arr.shape)
    if focus is None:
        return _region_from_risk_polygons(fallback_polygons, _forecast_category_from_grid(category_arr))
    focus_hazard, probability_peak = focus
    finite_prob = probability_peak[np.isfinite(probability_peak)]
    if finite_prob.size == 0 or float(np.nanmax(finite_prob)) <= 0:
        return _region_from_risk_polygons(fallback_polygons, _forecast_category_from_grid(category_arr))
    peak_value = float(np.nanmax(finite_prob))
    target_mask = probability_peak >= max(peak_value * 0.75, peak_value - 0.01)
    method = f"highest_{focus_hazard}_probability"

    seed = _max_probability_index(probability_peak, target_mask)
    if seed is None:
        return _region_from_risk_polygons(fallback_polygons, _forecast_category_from_grid(category_arr))

    component = _connected_component(target_mask, seed)
    component_lats = lat_grid[component]
    component_lons = lon_grid[component]
    if component_lats.size == 0 or component_lons.size == 0:
        return _region_from_risk_polygons(fallback_polygons, _forecast_category_from_grid(category_arr))

    center_lat = float(lat_grid[seed])
    center_lon = float(lon_grid[seed])
    focus_category = int(category_arr[seed]) if category_arr.size else 0
    bbox = _bbox_for_grid_component(component_lats, component_lons, lat_grid, lon_grid)
    return {
        "label": "Highlighted corridor",
        "centerLat": center_lat,
        "centerLon": center_lon,
        "bbox": bbox,
        "states": [],
        "focusCategory": SPC_RISK_LABELS[focus_category] if 0 <= focus_category < len(SPC_RISK_LABELS) else "NONE",
        "focusHazard": focus_hazard,
        "focusMethod": method,
        "focusProbability": float(probability_peak[seed]) if np.isfinite(probability_peak[seed]) else 0.0,
    }


def _prioritized_focus_probability_grid(
    probabilities: Mapping[str, np.ndarray],
    shape: tuple[int, int],
) -> tuple[str, np.ndarray] | None:
    tornado = _hazard_probability_grid(probabilities, "tornado", shape)
    tornado_max = _finite_grid_max(tornado)
    if tornado is not None and tornado_max > 0:
        return "tornado", tornado

    fallback: list[tuple[float, int, str, np.ndarray]] = []
    for priority, hazard in enumerate(("wind", "hail")):
        grid = _hazard_probability_grid(probabilities, hazard, shape)
        peak = _finite_grid_max(grid)
        if grid is not None and peak > 0:
            fallback.append((peak, -priority, hazard, grid))
    if not fallback:
        return None
    _, _, hazard, grid = max(fallback, key=lambda item: (item[0], item[1]))
    return hazard, grid


def _hazard_probability_grid(
    probabilities: Mapping[str, np.ndarray],
    hazard: str,
    shape: tuple[int, int],
) -> np.ndarray | None:
    value = probabilities.get(hazard)
    if value is None:
        return None
    arr = np.asarray(value, dtype=float)
    if arr.shape != shape:
        return None
    return np.where(np.isfinite(arr), arr, 0.0)


def _finite_grid_max(grid: np.ndarray | None) -> float:
    if grid is None:
        return 0.0
    finite = grid[np.isfinite(grid)]
    if finite.size == 0:
        return 0.0
    return float(np.nanmax(finite))


def _max_probability_index(probability_peak: np.ndarray, mask: np.ndarray) -> tuple[int, int] | None:
    valid = np.asarray(mask, dtype=bool) & np.isfinite(probability_peak)
    if not np.any(valid):
        return None
    scores = np.where(valid, probability_peak, -np.inf)
    max_score = float(np.nanmax(scores))
    candidates = np.argwhere(np.isclose(scores, max_score, rtol=1e-6, atol=1e-9))
    if candidates.size == 0:
        return tuple(int(x) for x in np.unravel_index(int(np.nanargmax(scores)), scores.shape))
    center = np.mean(candidates, axis=0)
    distances = np.sum((candidates - center) ** 2, axis=1)
    row, col = candidates[int(np.argmin(distances))]
    return int(row), int(col)


def _connected_component(mask: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    target = np.asarray(mask, dtype=bool)
    component = np.zeros(target.shape, dtype=bool)
    if not target[seed]:
        return component
    queue: deque[tuple[int, int]] = deque([seed])
    component[seed] = True
    rows, cols = target.shape
    while queue:
        row, col = queue.popleft()
        for d_row in (-1, 0, 1):
            for d_col in (-1, 0, 1):
                if d_row == 0 and d_col == 0:
                    continue
                next_row = row + d_row
                next_col = col + d_col
                if next_row < 0 or next_col < 0 or next_row >= rows or next_col >= cols:
                    continue
                if target[next_row, next_col] and not component[next_row, next_col]:
                    component[next_row, next_col] = True
                    queue.append((next_row, next_col))
    return component


def _bbox_for_grid_component(
    component_lats: np.ndarray,
    component_lons: np.ndarray,
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
) -> list[float]:
    lon_step = _median_grid_step(lon_grid, axis=1, default=0.5)
    lat_step = _median_grid_step(lat_grid, axis=0, default=0.35)
    min_lon, max_lon = max(-130.0, float(np.nanmin(component_lons))), min(-60.0, float(np.nanmax(component_lons)))
    min_lat, max_lat = max(20.0, float(np.nanmin(component_lats))), min(55.0, float(np.nanmax(component_lats)))
    pad_lon = max(0.6, lon_step * 2.0, (max_lon - min_lon) * 0.2)
    pad_lat = max(0.4, lat_step * 2.0, (max_lat - min_lat) * 0.2)
    return [
        max(-130.0, min_lon - pad_lon),
        max(20.0, min_lat - pad_lat),
        min(-60.0, max_lon + pad_lon),
        min(55.0, max_lat + pad_lat),
    ]


def _median_grid_step(grid: np.ndarray, axis: int, default: float) -> float:
    if grid.ndim != 2 or grid.shape[axis] < 2:
        return default
    diffs = np.diff(grid, axis=axis)
    finite = np.abs(diffs[np.isfinite(diffs)])
    if finite.size == 0:
        return default
    step = float(np.nanmedian(finite))
    return step if step > 0 else default


def _region_from_risk_polygons(payload: Mapping[str, Any], category: str) -> dict[str, Any]:
    points: list[tuple[float, float]] = []
    if isinstance(payload, Mapping):
        features = payload.get("features")
        if isinstance(features, list):
            target = _frontend_category(category)
            category_features = [
                feature for feature in features
                if isinstance(feature, Mapping)
                and _frontend_category(str((feature.get("properties") or {}).get("category", ""))) == target
            ]
            selected = category_features or [feature for feature in features if isinstance(feature, Mapping)]
            for feature in selected:
                geometry = feature.get("geometry") or {}
                if isinstance(geometry, Mapping):
                    points.extend(_geojson_positions(geometry.get("coordinates")))
    if not points:
        return {
            "label": "Highlighted corridor",
            "centerLat": 37.0,
            "centerLon": -97.0,
            "bbox": [-105.0, 30.0, -89.0, 43.0],
            "states": [],
        }

    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    min_lon, max_lon = max(-130.0, min(lons)), min(-60.0, max(lons))
    min_lat, max_lat = max(20.0, min(lats)), min(55.0, max(lats))
    pad_lon = max(1.5, (max_lon - min_lon) * 0.15)
    pad_lat = max(1.0, (max_lat - min_lat) * 0.15)
    bbox = [
        max(-130.0, min_lon - pad_lon),
        max(20.0, min_lat - pad_lat),
        min(-60.0, max_lon + pad_lon),
        min(55.0, max_lat + pad_lat),
    ]
    return {
        "label": "Highlighted corridor",
        "centerLat": (bbox[1] + bbox[3]) / 2,
        "centerLon": (bbox[0] + bbox[2]) / 2,
        "bbox": bbox,
        "states": [],
    }


def _geojson_positions(value: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if not isinstance(value, list):
        return points
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        lon, lat = float(value[0]), float(value[1])
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return [(lon, lat)]
        return points
    for item in value:
        points.extend(_geojson_positions(item))
    return points


def _point_sampled_ingredients(
    features: GriddedFeatures,
    lats: np.ndarray,
    lons: np.ndarray,
    region: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    center_lat = float(region.get("centerLat", 37.0) or 37.0)
    center_lon = float(region.get("centerLon", -97.0) or -97.0)
    i_lat, i_lon = _nearest_grid_index(lats, lons, center_lat, center_lon)
    raw = features.raw
    sbcape = _array_point(raw.get("sbcape"), i_lat, i_lon)
    mlcape = _array_point(raw.get("mlcape"), i_lat, i_lon, sbcape * 0.85)
    mucape = _array_point(raw.get("mucape"), i_lat, i_lon, max(sbcape, mlcape))
    cin = _array_point(raw.get("cin"), i_lat, i_lon)
    td_f = _array_point(raw.get("sfcDewpointF"), i_lat, i_lon, 50.0)
    td2m = (td_f - 32.0) * 5.0 / 9.0 + 273.15
    lcl_m = _array_point(raw.get("lclM"), i_lat, i_lon, 1500.0)
    t2m = td2m + max(lcl_m, 0.0) / 125.0
    pwat = _array_point(raw.get("pwatIn"), i_lat, i_lon, 0.8) * 25.4
    shear_kt = _array_point(raw.get("shear06Kt"), i_lat, i_lon)
    srh01 = _array_point(raw.get("srh01"), i_lat, i_lon)
    srh03 = _array_point(raw.get("srh03"), i_lat, i_lon, srh01 * 1.4)
    sr_wind = _array_point(raw.get("stormRelWindKt"), i_lat, i_lon, shear_kt * 0.5)
    composites = diag.composites(
        cape=np.array([sbcape]),
        mlcape=np.array([mlcape]),
        mucape=np.array([mucape]),
        shear_kt=np.array([shear_kt]),
        srh01=np.array([srh01]),
        srh03=np.array([srh03]),
        cin=np.array([cin]),
        td2m_K=np.array([td2m]),
    )
    ingredients = _ingredients_at_point(
        sbcape,
        mlcape,
        mucape,
        cin,
        td2m,
        t2m,
        pwat,
        shear_kt,
        srh01,
        srh03,
        sr_wind,
        {key: float(value[0]) for key, value in composites.items()},
    )
    lat_grid, lon_grid = _lat_lon_grids(lats, lons)
    sample = {
        "method": "nearest_grid_point",
        "requestedLat": center_lat,
        "requestedLon": center_lon,
        "gridLat": _array_point(lat_grid, i_lat, i_lon, center_lat),
        "gridLon": _array_point(lon_grid, i_lat, i_lon, center_lon),
        "gridRow": int(i_lat),
        "gridCol": int(i_lon),
    }
    return ingredients, sample


def _nearest_grid_index(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> tuple[int, int]:
    lat_arr, lon_arr = _lat_lon_grids(lats, lons)
    distance = (lat_arr - lat) ** 2 + ((lon_arr - lon) * np.cos(np.radians(lat))) ** 2
    return tuple(int(x) for x in np.unravel_index(int(np.nanargmin(distance)), distance.shape))


def _lat_lon_grids(lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    if lat_arr.ndim == 1 and lon_arr.ndim == 1:
        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)
        return lat_grid, lon_grid
    return lat_arr, lon_arr


def _array_point(arr: Any, i_lat: int, i_lon: int, default: float = 0.0) -> float:
    value_arr = np.asarray(arr, dtype=float)
    if value_arr.ndim != 2 or i_lat < 0 or i_lon < 0 or i_lat >= value_arr.shape[0] or i_lon >= value_arr.shape[1]:
        return default
    value = float(value_arr[i_lat, i_lon])
    return value if np.isfinite(value) else default


def _upper_air_overlay_from_fetched(
    cycle: HrrrCycle,
    forecast_hour: int,
    fetched: FetchedHour,
    valid_time_iso: str,
) -> dict[str, Any]:
    fields = fetched.fields
    lines = _hgt500_lines_from_field(fields.get("hgt500"), fetched.lats, fetched.lons)
    grid_stride = int(fetched.metadata.get("gridStride", 1) or 1)
    wind_stride = max(4, round(22 / max(1, grid_stride)))
    vectors = _wind500_vectors_from_fields(
        fields.get("u500"),
        fields.get("v500"),
        fetched.lats,
        fetched.lons,
        stride=wind_stride,
    )
    return {
        "upperAirLines": lines,
        "upperAirVectors": vectors,
        "metadata": {
            "domain": "CONUS",
            "level": "500mb",
            "fields": ["hgt500", "u500", "v500"],
            "gridStride": grid_stride,
            "windBarbStride": wind_stride,
            "source": "HRRR",
            "sourceMode": "incremental_artifact_pipeline",
            "hasHeightContours": len(lines) > 0,
            "hasWindVectors": len(vectors) > 0,
            "windVectorCount": len(vectors),
            "heightContourCount": len(lines),
            "sourceCycle": cycle.label,
            "forecastHour": int(forecast_hour),
            "runDate": cycle.run_date,
            "runCycle": cycle.run_cycle,
            "modelForecastHour": int(forecast_hour),
            "validTimeISO": valid_time_iso,
        },
    }


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


def _aggregate_postprocessing(hour_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = {
        "weakInstability": 0,
        "weakKinematics": 0,
        "strongCapOrDryAir": 0,
        "experimentalModel": 0,
        "isolatedComponent": 0,
        "coastalOffshore": 0,
        "missingCategoryBuffer": 0,
    }
    removed_components = 0
    raw_probability_max = {"tornado": 0.0, "hail": 0.0, "wind": 0.0}
    capped_probability_max = {"tornado": 0.0, "hail": 0.0, "wind": 0.0}
    for artifact in hour_artifacts:
        probability = artifact.get("probabilityStats", {})
        post = artifact.get("postProcessing", {})
        for key, value in probability.get("downgradedCells", {}).items():
            totals[key] = totals.get(key, 0) + int(value)
        for key, value in post.get("downgradedCells", {}).items():
            totals[key] = totals.get(key, 0) + int(value)
        removed_components += int(post.get("removedComponents", 0))
        for hazard, value in probability.get("rawProbabilityMax", {}).items():
            raw_probability_max[hazard] = max(raw_probability_max.get(hazard, 0.0), float(value))
        for hazard, value in probability.get("cappedProbabilityMax", {}).items():
            capped_probability_max[hazard] = max(capped_probability_max.get(hazard, 0.0), float(value))
    return {
        "environmentalCapsApplied": True,
        "morphologicalSmoothingApplied": any(
            artifact.get("postProcessing", {}).get("morphologicalSmoothingApplied")
            for artifact in hour_artifacts
        ),
        "exactBandsGenerated": True,
        "removedComponents": removed_components,
        "downgradedCells": totals,
        "rawProbabilityMax": raw_probability_max,
        "cappedProbabilityMax": capped_probability_max,
    }


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


def _read_json_payload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _tile_grid_payload(tile: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    category_grid = np.asarray(tile.get("categoryOrdinal"), dtype=np.int16)
    lats = np.asarray(tile.get("lats"), dtype=float)
    lons = np.asarray(tile.get("lons"), dtype=float)
    if category_grid.ndim != 2 or lats.shape != category_grid.shape or lons.shape != category_grid.shape:
        raise ValueError("incremental probability tile is missing matching category/lats/lons grids")
    return lats, lons, category_grid


def _write_incremental_spc_verification(
    output_dir: Path,
    index: Mapping[str, Any],
    cycle: HrrrCycle,
    requested_hours: Iterable[int],
    session: requests.Session,
    spc_fetch: Callable[[requests.Session, Path | None], dict[str, Any]],
) -> dict[str, Any]:
    try:
        spc = spc_fetch(session, output_dir)
        spc_geojson = spc.get("categoryGeojson")
        if not isinstance(spc_geojson, Mapping):
            raise ValueError("SPC Day 1 fetch did not return a category GeoJSON payload")

        ready = set(_int_list(index.get("readyForecastHours")))
        tile_lats: np.ndarray | None = None
        tile_lons: np.ndarray | None = None
        category_grids: list[np.ndarray] = []
        verification_hours: list[int] = []
        for forecast_hour in sorted({int(hour) for hour in requested_hours if int(hour) in ready}):
            tile_path = output_dir / "hours" / f"f{forecast_hour:02d}" / "probability_tile.json"
            tile = _read_json_payload(tile_path)
            if not isinstance(tile, Mapping):
                continue
            hour_lats, hour_lons, category_grid = _tile_grid_payload(tile)
            if tile_lats is None:
                tile_lats = hour_lats
                tile_lons = hour_lons
            elif hour_lats.shape != tile_lats.shape or hour_lons.shape != tile_lons.shape:
                raise ValueError(f"incremental probability tile shape mismatch for F{forecast_hour:02d}")
            category_grids.append(category_grid)
            verification_hours.append(forecast_hour)

        if tile_lats is None or tile_lons is None or not category_grids:
            raise ValueError("No ready incremental probability tiles were available for SPC verification")

        verification_grid = _aggregate_for_spc_window(cycle, verification_hours, category_grids, spc_geojson)
        summary = compare_prediction_to_spc(tile_lats, tile_lons, verification_grid, spc_geojson, None)
        summary["spcDay1Url"] = spc.get("day1Url")
        summary["spcGeojsonZipUrl"] = spc.get("geojsonZipUrl")
        summary["spcFetchedAtISO"] = spc.get("fetchedAtISO")
        summary["spcFetchedAfterPredictionArtifacts"] = True
        summary["verificationGridSource"] = "incremental_probability_tiles"
        summary["verificationForecastHours"] = verification_hours
        summary["cycle"] = index.get("cycle")
        summary["cycleTimeISO"] = index.get("cycleTimeISO")
        summary["generatedAtISO"] = _now_iso()
        _write_json(output_dir / "verification_summary.json", summary)
        _write_json(output_dir / "spc_day1_cat.geojson", spc_geojson)
        return summary
    except Exception as exc:  # noqa: BLE001
        summary = {
            "error": f"{type(exc).__name__}: {exc}",
            "spcFetchedAfterPredictionArtifacts": True,
            "leakageGuard": "Current official SPC outlook is fetched only after prediction artifacts are generated.",
            "verificationGridSource": "incremental_probability_tiles",
            "cycle": index.get("cycle"),
            "cycleTimeISO": index.get("cycleTimeISO"),
            "generatedAtISO": _now_iso(),
        }
        _write_json(output_dir / "verification_summary.json", summary)
        return summary


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
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = output_dir.with_name(f"{output_dir.name}.previous")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if output_dir.exists():
        shutil.move(str(output_dir), str(backup_dir))
    try:
        shutil.move(str(working_dir), str(output_dir))
    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        if backup_dir.exists():
            shutil.move(str(backup_dir), str(output_dir))
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def _publish_complete_incremental_snapshot(
    output_dir: Path,
    index: Mapping[str, Any],
    requested_hours: Iterable[int],
) -> None:
    if not _incremental_index_covers_requested_hours(index, requested_hours):
        return
    complete_dir = _incremental_complete_output_dir(output_dir)
    complete_dir.parent.mkdir(parents=True, exist_ok=True)
    complete_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    for source in files:
        relative = source.relative_to(output_dir)
        if relative == Path("index.json"):
            continue
        _copy_file_atomic(source, complete_dir / relative)

    # Publish the index last so readers only switch to the new complete snapshot
    # after all referenced per-hour artifacts are present.
    _write_json(complete_dir / "index.json", index)


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f"{destination.name}.tmp")
    if tmp.exists():
        tmp.unlink()
    shutil.copyfile(source, tmp)
    tmp.replace(destination)


def _get_gcs_storage_client():
    try:
        from google.cloud import storage  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("google-cloud-storage is required for GCS artifact publishing") from exc
    return storage.Client()


def _publish_incremental_artifacts_to_gcs(
    output_dir: Path,
    index: Mapping[str, Any],
    requested_hours: Iterable[int],
    bucket_name: str,
    prefix: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    bucket_name = bucket_name.strip()
    if not bucket_name:
        return {"enabled": False, "latencyMs": 0, "currentFiles": 0, "completeFiles": 0}

    client = _get_gcs_storage_client()
    bucket = client.bucket(bucket_name)
    output_dir = Path(output_dir)
    current_files = _upload_directory_to_gcs(bucket, output_dir, _gcs_join(prefix, output_dir.name))
    complete_files = 0
    complete_dir = _incremental_complete_output_dir(output_dir)
    if complete_dir.exists() and _incremental_index_covers_requested_hours(index, requested_hours):
        complete_files = _upload_directory_to_gcs(bucket, complete_dir, _gcs_join(prefix, complete_dir.name))
    previous_files = 0
    previous_dir = output_dir.with_name(f"{output_dir.name}.previous")
    if previous_dir.exists():
        previous_files = _upload_directory_to_gcs(bucket, previous_dir, _gcs_join(prefix, previous_dir.name))

    result = {
        "enabled": True,
        "bucket": bucket_name,
        "prefix": prefix.strip("/"),
        "currentFiles": current_files,
        "completeFiles": complete_files,
        "previousFiles": previous_files,
        "latencyMs": int((time.perf_counter() - started) * 1000),
    }
    print(
        f"[gcs publish] bucket={bucket_name} prefix={result['prefix']} "
        f"currentFiles={current_files} completeFiles={complete_files} previousFiles={previous_files} "
        f"latency={result['latencyMs']}ms",
        flush=True,
    )
    return result


def _publish_incremental_shard_artifacts_to_gcs(
    output_dir: Path,
    index: Mapping[str, Any],
    processed_hours: Iterable[int],
    bucket_name: str,
    prefix: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    bucket_name = bucket_name.strip()
    if not bucket_name:
        return {"enabled": False, "latencyMs": 0, "currentFiles": 0, "completeFiles": 0}

    client = _get_gcs_storage_client()
    bucket = client.bucket(bucket_name)
    output_dir = Path(output_dir)
    current_files = 0
    for forecast_hour in sorted({int(hour) for hour in processed_hours}):
        hour_dir = output_dir / "hours" / f"f{forecast_hour:02d}"
        current_files += _upload_directory_to_gcs(
            bucket,
            hour_dir,
            _gcs_join(prefix, output_dir.name, "hours", f"f{forecast_hour:02d}"),
        )

    result = {
        "enabled": True,
        "bucket": bucket_name,
        "prefix": prefix.strip("/"),
        "currentFiles": current_files,
        "completeFiles": 0,
        "latencyMs": int((time.perf_counter() - started) * 1000),
    }
    print(
        f"[gcs shard publish] bucket={bucket_name} prefix={result['prefix']} "
        f"hours={sorted({int(hour) for hour in processed_hours})} "
        f"currentFiles={current_files} latency={result['latencyMs']}ms",
        flush=True,
    )
    return result


def _finalize_sharded_incremental_snapshot(
    output_dir: Path,
    index: Mapping[str, Any],
    requested_hours: Iterable[int],
    bucket_name: str,
    prefix: str = "",
    timeout_seconds: int = 2700,
    poll_seconds: int = 20,
) -> dict[str, Any]:
    requested = resolve_forecast_hours(requested_hours)
    output_dir = Path(output_dir)
    started = time.perf_counter()
    deadline = started + max(1, int(timeout_seconds))
    poll = max(1, int(poll_seconds))
    cycle_time_iso = str(index.get("cycleTimeISO") or "")
    artifact_generation_id = str(index.get("artifactGenerationId") or "") if index.get("artifactChangesThisRun") else ""
    last_payload = dict(index)

    while True:
        _hydrate_incremental_artifacts_from_gcs(output_dir, bucket_name, prefix)
        ready = [
            hour
            for hour in requested
            if _incremental_hour_ready(
                output_dir,
                hour,
                cycle_time_iso=cycle_time_iso,
                artifact_generation_id=artifact_generation_id,
            )
        ]
        failed_hours = _failed_incremental_hours_for_cycle(output_dir, requested, cycle_time_iso)
        failed = sorted({int(item["forecastHour"]) for item in failed_hours})
        pending = [hour for hour in requested if hour not in ready and hour not in failed]
        status = "complete" if not pending and not failed else "partial"
        last_payload = {
            **dict(index),
            "generatedAtISO": _now_iso(),
            "requestedForecastHours": requested,
            "processForecastHours": requested,
            "readyForecastHours": ready,
            "failedForecastHours": failed,
            "failedHours": failed_hours,
            "pendingForecastHours": pending,
            "latestReadyForecastHour": ready[-1] if ready else None,
            "status": status,
            "taskShardFinalized": True,
            "taskShardFinalizeLatencyMs": int((time.perf_counter() - started) * 1000),
        }
        _write_json(output_dir / "index.json", last_payload)
        _write_json(output_dir / "metadata.json", last_payload)
        if status == "complete":
            _publish_complete_incremental_snapshot(output_dir, last_payload, requested)
            _publish_incremental_artifacts_to_gcs(output_dir, last_payload, requested, bucket_name, prefix)
            print(
                f"[task shard finalize] complete ready={len(ready)} requested={len(requested)}",
                flush=True,
            )
            return last_payload
        if time.perf_counter() >= deadline:
            _publish_incremental_artifacts_to_gcs(output_dir, last_payload, requested, bucket_name, prefix)
            print(
                f"[task shard finalize] timeout ready={len(ready)} pending={len(pending)} failed={len(failed)}",
                flush=True,
            )
            return last_payload
        print(
            f"[task shard finalize] waiting ready={len(ready)} pending={len(pending)} failed={len(failed)}",
            flush=True,
        )
        time.sleep(poll)


def _hydrate_incremental_artifacts_from_gcs(
    output_dir: Path,
    bucket_name: str,
    prefix: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    bucket_name = bucket_name.strip()
    if not bucket_name:
        return {"enabled": False, "latencyMs": 0, "currentFiles": 0, "completeFiles": 0}

    client = _get_gcs_storage_client()
    bucket = client.bucket(bucket_name)
    output_dir = Path(output_dir)
    current_files = _download_gcs_prefix_to_directory(bucket, _gcs_join(prefix, output_dir.name), output_dir)
    complete_dir = _incremental_complete_output_dir(output_dir)
    complete_files = _download_gcs_prefix_to_directory(bucket, _gcs_join(prefix, complete_dir.name), complete_dir)
    result = {
        "enabled": True,
        "bucket": bucket_name,
        "prefix": prefix.strip("/"),
        "currentFiles": current_files,
        "completeFiles": complete_files,
        "latencyMs": int((time.perf_counter() - started) * 1000),
    }
    print(
        f"[gcs hydrate] bucket={bucket_name} prefix={result['prefix']} "
        f"currentFiles={current_files} completeFiles={complete_files} "
        f"latency={result['latencyMs']}ms",
        flush=True,
    )
    return result


def _download_gcs_prefix_to_directory(bucket: Any, source_prefix: str, destination_dir: Path) -> int:
    source_prefix = source_prefix.strip("/")
    list_prefix = f"{source_prefix}/" if source_prefix else ""
    downloaded = 0
    try:
        from google.api_core import exceptions as gcs_exceptions  # type: ignore
    except Exception:  # noqa: BLE001
        gcs_exceptions = None
    for blob in bucket.list_blobs(prefix=list_prefix):
        blob_name = str(blob.name)
        if not blob_name or blob_name.endswith("/"):
            continue
        relative_name = blob_name[len(list_prefix):] if list_prefix and blob_name.startswith(list_prefix) else blob_name
        if not relative_name:
            continue
        destination = destination_dir / Path(relative_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            blob.download_to_filename(str(destination))
        except Exception as exc:  # noqa: BLE001
            not_found = (
                (gcs_exceptions is not None and isinstance(exc, gcs_exceptions.NotFound))
                or type(exc).__name__ == "NotFound"
            )
            if not_found:
                print(f"[gcs hydrate skip] missing object during concurrent publish {blob_name}", flush=True)
                continue
            raise
        downloaded += 1
    return downloaded


def _upload_directory_to_gcs(bucket: Any, source_dir: Path, destination_prefix: str) -> int:
    if not source_dir.exists():
        return 0
    source_dir = Path(source_dir)
    files = sorted(path for path in source_dir.rglob("*") if path.is_file())
    index_path = source_dir / "index.json"
    ordered_files = [path for path in files if path != index_path]
    if index_path in files:
        ordered_files.append(index_path)

    uploaded = 0
    for source in ordered_files:
        relative = source.relative_to(source_dir).as_posix()
        blob = bucket.blob(_gcs_join(destination_prefix, relative))
        blob.upload_from_filename(str(source))
        uploaded += 1
    return uploaded


def _gcs_join(*parts: str | Path | None) -> str:
    return "/".join(str(part).strip("/") for part in parts if part is not None and str(part).strip("/"))


def _try_acquire_gcs_run_lock(
    bucket_name: str,
    lock_name: str,
    ttl_seconds: int,
) -> GcsRunLock | None:
    bucket_name = bucket_name.strip()
    lock_name = lock_name.strip().strip("/")
    if not bucket_name or not lock_name:
        return None

    try:
        from google.api_core import exceptions as gcs_exceptions  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("google-api-core is required for GCS run locking") from exc

    client = _get_gcs_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(lock_name)
    payload = {
        "id": str(uuid.uuid4()),
        "createdAtISO": _now_iso(),
        "ttlSeconds": int(ttl_seconds),
    }
    for _ in range(2):
        try:
            blob.upload_from_string(
                json.dumps(payload, indent=2, default=_json_default),
                content_type="application/json",
                if_generation_match=0,
            )
            generation = int(blob.generation) if getattr(blob, "generation", None) is not None else None
            print(f"[run lock] acquired gs://{bucket_name}/{lock_name}", flush=True)
            return GcsRunLock(bucket_name=bucket_name, blob_name=lock_name, generation=generation)
        except gcs_exceptions.PreconditionFailed:
            if _delete_stale_gcs_run_lock(blob, int(ttl_seconds), gcs_exceptions):
                continue
            print(f"[run lock] held gs://{bucket_name}/{lock_name}; skipping overlapping execution", flush=True)
            return None
    return None


def _delete_stale_gcs_run_lock(blob: Any, ttl_seconds: int, gcs_exceptions: Any) -> bool:
    try:
        blob.reload()
    except gcs_exceptions.NotFound:
        return True
    updated = getattr(blob, "updated", None)
    generation = getattr(blob, "generation", None)
    if updated is None:
        return False
    age_seconds = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
    if age_seconds <= max(1, int(ttl_seconds)):
        return False
    try:
        blob.delete(if_generation_match=generation)
        print(f"[run lock] removed stale lock age={int(age_seconds)}s", flush=True)
        return True
    except (gcs_exceptions.NotFound, gcs_exceptions.PreconditionFailed):
        return False


def _release_gcs_run_lock(lock: GcsRunLock) -> None:
    try:
        from google.api_core import exceptions as gcs_exceptions  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[run lock] release unavailable {type(exc).__name__}: {exc}", flush=True)
        return
    try:
        bucket = _get_gcs_storage_client().bucket(lock.bucket_name)
        blob = bucket.blob(lock.blob_name)
        kwargs = {"if_generation_match": lock.generation} if lock.generation is not None else {}
        blob.delete(**kwargs)
        print(f"[run lock] released gs://{lock.bucket_name}/{lock.blob_name}", flush=True)
    except gcs_exceptions.NotFound:
        return
    except gcs_exceptions.PreconditionFailed:
        print(f"[run lock] not released because generation changed gs://{lock.bucket_name}/{lock.blob_name}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[run lock] release failed {type(exc).__name__}: {exc}", flush=True)


def _incremental_complete_output_dir(output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    configured = os.environ.get("AUTOOUTLOOK_INCREMENTAL_COMPLETE_ARTIFACT_DIR")
    if configured and output_dir == DEFAULT_INCREMENTAL_OUTPUT_DIR:
        return Path(configured)
    return output_dir.with_name(f"{output_dir.name}_complete")


def _incremental_index_covers_requested_hours(
    index: Mapping[str, Any],
    requested_hours: Iterable[int],
) -> bool:
    if index.get("status") != "complete":
        return False
    model = index.get("model")
    if isinstance(model, Mapping) and model.get("active") is False:
        return False
    requested = {int(hour) for hour in requested_hours}
    ready = set(_int_list(index.get("readyForecastHours")))
    return requested.issubset(ready)


def _write_failed_run_metadata(
    output_dir: Path,
    working_dir: Path,
    context: Mapping[str, Any],
    exc: Exception,
    started: float,
) -> None:
    payload = {
        "generatedAtISO": _now_iso(),
        "status": "failed",
        "error": f"{type(exc).__name__}: {exc}",
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "previousLatestPreserved": output_dir.exists(),
        "temporaryOutputDir": str(working_dir),
        **dict(context),
    }
    failure_path = output_dir.with_name(f"{output_dir.name}.failed.json")
    try:
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(failure_path, payload)
    except Exception:
        pass


def _read_incremental_index_payload(output_dir: Path) -> dict[str, Any]:
    index_path = output_dir / "index.json"
    if not index_path.exists():
        return {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_incremental_index(output_dir: Path, cycle: HrrrCycle) -> dict[str, Any] | None:
    payload = _read_incremental_index_payload(output_dir)
    if payload.get("cycle") != cycle.label:
        return None
    return payload


def _cache_previous_incremental_cycle(output_dir: Path) -> Path | None:
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return None
    previous_index = _read_incremental_index_payload(output_dir)
    if not previous_index.get("cycle"):
        return None
    previous_dir = output_dir.with_name(f"{output_dir.name}.previous")
    if previous_dir.exists():
        shutil.rmtree(previous_dir, ignore_errors=True)
    shutil.copytree(output_dir, previous_dir)
    return previous_dir


def _clear_incremental_hour_artifacts(output_dir: Path) -> None:
    hours_dir = output_dir / "hours"
    if hours_dir.exists():
        shutil.rmtree(hours_dir, ignore_errors=True)
    hours_dir.mkdir(parents=True, exist_ok=True)


def _clear_incremental_hour_artifact(output_dir: Path, forecast_hour: int) -> None:
    hour_dir = output_dir / "hours" / f"f{int(forecast_hour):02d}"
    if hour_dir.exists():
        shutil.rmtree(hour_dir, ignore_errors=True)


def _incremental_hour_ready(
    output_dir: Path,
    forecast_hour: int,
    cycle_time_iso: str | None = None,
    artifact_generation_id: str | None = None,
) -> bool:
    hour_dir = output_dir / "hours" / f"f{int(forecast_hour):02d}"
    if not all(
        (hour_dir / name).exists()
        for name in ("risk_polygons.geojson", "probability_tile.json", "upper_air_overlay.json", "metadata.json")
    ):
        return False
    metadata_path = hour_dir / "metadata.json"
    return (
        _incremental_metadata_has_focus_fields(metadata_path)
        and _incremental_metadata_matches_cycle(metadata_path, forecast_hour, cycle_time_iso)
        and _incremental_metadata_matches_generation(metadata_path, artifact_generation_id)
    )


def _incremental_metadata_matches_cycle(path: Path, forecast_hour: int, cycle_time_iso: str | None = None) -> bool:
    if not cycle_time_iso:
        return True
    cycle_time = _parse_iso(cycle_time_iso)
    if cycle_time is None:
        return True
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        valid_time = _parse_iso(metadata.get("validTimeISO"))
    except Exception:
        return False
    if valid_time is None:
        return False
    expected = cycle_time + timedelta(hours=int(forecast_hour))
    return abs((valid_time - expected).total_seconds()) <= 60


def _incremental_metadata_matches_generation(path: Path, artifact_generation_id: str | None = None) -> bool:
    if not artifact_generation_id:
        return True
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return metadata.get("artifactGenerationId") == artifact_generation_id


def _failed_incremental_hours_for_cycle(
    output_dir: Path,
    requested_hours: Iterable[int],
    cycle_time_iso: str | None,
) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for forecast_hour in requested_hours:
        metadata_path = output_dir / "hours" / f"f{int(forecast_hour):02d}" / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metadata.get("status") != "failed":
            continue
        if cycle_time_iso and not _incremental_metadata_matches_cycle(metadata_path, int(forecast_hour), cycle_time_iso):
            continue
        failed.append({
            "forecastHour": int(forecast_hour),
            "stage": metadata.get("stage", "incremental"),
            "error": metadata.get("error", "failed"),
            "generatedAtISO": metadata.get("generatedAtISO"),
        })
    return failed


def _incremental_metadata_has_focus_fields(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    region = payload.get("region")
    ingredients = payload.get("ingredients")
    sample = payload.get("ingredientSample")
    if not isinstance(region, dict) or not isinstance(ingredients, dict) or not isinstance(sample, dict):
        return False
    try:
        center_lat = float(region.get("centerLat"))
        center_lon = float(region.get("centerLon"))
        int(sample.get("gridRow"))
        int(sample.get("gridCol"))
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(center_lat) and np.isfinite(center_lon))


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
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


def resolve_forecast_hours(
    forecast_hours: Iterable[int] | None = None,
    all_hours: bool = False,
    model_name: str = "hrrr",
) -> list[int]:
    if all_hours and forecast_hours is not None:
        raise ValueError("--all-hours cannot be combined with --forecast-hours")
    if model_name == "ecmwf":
        default_hours = list(range(0, 91, 3))
        selected = default_hours if (forecast_hours is None) else forecast_hours
        hours = sorted({int(hour) for hour in selected})
        invalid = [hour for hour in hours if hour < 0 or hour > 90]
        if invalid:
            raise ValueError(f"Forecast hours must be in 0..90: {invalid}")
    else:
        selected = ALL_FORECAST_HOURS if all_hours else (FORECAST_HOURS if forecast_hours is None else forecast_hours)
        hours = sorted({int(hour) for hour in selected})
        invalid = [hour for hour in hours if hour < 0 or hour > 48]
        if invalid:
            raise ValueError(f"Forecast hours must be in 0..48: {invalid}")
    if not hours:
        raise ValueError("At least one forecast hour is required")
    return hours


def resolve_required_forecast_hour(
    forecast_hours: Iterable[int],
    require_complete_hour: int | None = None,
    cycle_policy: str = DEFAULT_CYCLE_POLICY,
    model_name: str = "hrrr",
) -> int:
    if require_complete_hour is not None:
        required = int(require_complete_hour)
    elif cycle_policy == "complete-48":
        required = 90 if model_name == "ecmwf" else 48
    elif cycle_policy == "latest-startable":
        required = 0
    else:
        hours = sorted({int(hour) for hour in forecast_hours})
        if not hours:
            raise ValueError("At least one forecast hour is required")
        required = max(hours)
    max_hour = 90 if model_name == "ecmwf" else 48
    if required < 0 or required > max_hour:
        model_upper = model_name.upper()
        raise ValueError(f"Required complete {model_upper} forecast hour must be in 0..{max_hour}: {required}")
    return required


def resolve_cycle_policy(cycle_policy: str | None = None, incremental: bool = False) -> str:
    policy = cycle_policy or (DEFAULT_INCREMENTAL_CYCLE_POLICY if incremental else DEFAULT_CYCLE_POLICY)
    if policy not in CYCLE_POLICIES:
        raise ValueError(f"Cycle policy must be one of {', '.join(CYCLE_POLICIES)}: {policy}")
    return policy


def _resolve_worker_count(value: int | None, default: int) -> int:
    if value is None:
        return max(1, int(default))
    return max(1, int(value))


def _resolve_grid_stride(value: int | None) -> int:
    return max(1, int(value if value is not None else DEFAULT_GRID_STRIDE))


def _resolve_tile_stride(value: int | None, grid_stride: int) -> int:
    if value is not None:
        return max(1, int(value))
    raw = os.environ.get("AUTOOUTLOOK_TILE_STRIDE")
    if raw is not None and raw.strip() != "":
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, int(grid_stride))


def _detect_hrrr_cycle(
    session: requests.Session,
    now: datetime | None,
    required_forecast_hour: int,
    detect_cycle_fn: CycleDetectFn | None,
    model_name: str = "hrrr",
) -> HrrrCycle | HrrrCycleDetection:
    if detect_cycle_fn is not None:
        return detect_cycle_fn(session, now)
    if model_name == "ecmwf":
        from backend.ecmwf_selected import latest_available_ecmwf_cycle
        cycle = latest_available_ecmwf_cycle()
        return HrrrCycle(cycle.run_date, cycle.run_cycle)
    return latest_available_hrrr_cycle_with_metadata(
        session=session,
        now=now,
        require_forecast_hour=required_forecast_hour,
    )


def _normalize_cycle_detection(
    detection: HrrrCycle | HrrrCycleDetection,
    requested_forecast_hours: Iterable[int],
    required_forecast_hour: int,
    cycle_policy: str,
    require_complete_hour: int | None = None,
) -> tuple[HrrrCycle, dict[str, Any]]:
    requested_hours = sorted({int(hour) for hour in requested_forecast_hours})
    required_hours = sorted({0, int(required_forecast_hour)})
    if isinstance(detection, HrrrCycleDetection):
        cycle = detection.selected
        metadata = dict(detection.metadata)
    else:
        cycle = detection
        metadata = {
            "selected": _cycle_metadata(detection),
            "checkedCycles": [],
            "preferredCyclesUTC": [0, 6, 12, 18],
        }
    checked_cycles = list(metadata.get("checkedCycles") or [])
    latest_extended_candidate = metadata.get("latestExtendedCandidate") or (checked_cycles[0] if checked_cycles else _cycle_metadata(cycle))
    metadata.update({
        "selected": metadata.get("selected") or _cycle_metadata(cycle),
        "latestExtendedCandidate": latest_extended_candidate,
        "checkedCycles": checked_cycles,
        "requestedForecastHours": requested_hours,
        "requiredForecastHourForCycle": int(required_forecast_hour),
        "requiredForecastHours": _int_list(metadata.get("requiredForecastHours")) or required_hours,
        "requiredForecastHoursChecked": _int_list(metadata.get("requiredForecastHoursChecked")) or required_hours,
        "cyclePolicy": _cycle_policy_metadata(
            cycle_policy,
            requested_hours,
            required_forecast_hour,
            require_complete_hour,
        ),
    })
    selected_was_fallback = bool(
        metadata.get("selectedCycleWasFallback", _selected_cycle_was_fallback(cycle, checked_cycles))
    )
    metadata["selectedCycleWasFallback"] = selected_was_fallback
    metadata["fallbackReason"] = metadata.get("fallbackReason") or (
        _cycle_fallback_reason(checked_cycles, required_forecast_hour) if selected_was_fallback else None
    )
    return cycle, metadata


def _cycle_detection_artifact_fields(cycle_detection_metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requiredForecastHourForCycle": cycle_detection_metadata.get("requiredForecastHourForCycle"),
        "requiredForecastHoursChecked": cycle_detection_metadata.get("requiredForecastHoursChecked", []),
        "latestExtendedCandidate": cycle_detection_metadata.get("latestExtendedCandidate"),
        "selectedCycleWasFallback": bool(cycle_detection_metadata.get("selectedCycleWasFallback")),
        "cyclePolicy": cycle_detection_metadata.get("cyclePolicy"),
        "checkedCycles": cycle_detection_metadata.get("checkedCycles", []),
        "fallbackReason": cycle_detection_metadata.get("fallbackReason"),
    }


def _cycle_policy_metadata(
    cycle_policy: str,
    requested_forecast_hours: Iterable[int],
    required_forecast_hour: int,
    require_complete_hour: int | None = None,
) -> dict[str, Any]:
    required_hours = sorted({0, int(required_forecast_hour)})
    requested_hours = sorted({int(hour) for hour in requested_forecast_hours})
    return {
        "name": cycle_policy,
        "model": "HRRR",
        "allowedRunCyclesUTC": [0, 6, 12, 18],
        "requestedForecastHours": requested_hours,
        "requiredForecastHourForCycle": int(required_forecast_hour),
        "requiredForecastHoursChecked": required_hours,
        "requireCompleteHourOverride": require_complete_hour,
        "description": (
            "Select the newest 00Z/06Z/12Z/18Z HRRR cycle with usable selected-field "
            f".idx files for {', '.join(f'f{hour:02d}' for hour in required_hours)}."
        ),
    }


def _selected_cycle_was_fallback(cycle: HrrrCycle, checked_cycles: list[dict[str, Any]]) -> bool:
    if not checked_cycles:
        return False
    first = checked_cycles[0]
    try:
        return str(first.get("runDate")) != cycle.run_date or int(first.get("runCycle")) != cycle.run_cycle
    except (TypeError, ValueError):
        return len(checked_cycles) > 1


def _cycle_fallback_reason(checked_cycles: list[dict[str, Any]], required_forecast_hour: int) -> str | None:
    if len(checked_cycles) <= 1:
        return None
    latest = checked_cycles[0]
    missing_hours = [
        int(report.get("forecastHour", required_forecast_hour))
        for report in latest.get("hours", [])
        if not (report.get("idxAvailable") and report.get("requiredFieldsPresent"))
    ]
    missing = ", ".join(f"f{hour:02d}" for hour in sorted(set(missing_hours)) if hour >= 0)
    if missing:
        return f"{latest.get('label', 'Latest extended HRRR cycle')} incomplete for {missing}"
    return f"{latest.get('label', 'Latest extended HRRR cycle')} failed completeness checks"


def _cycle_metadata(cycle: HrrrCycle) -> dict[str, Any]:
    return {
        "runDate": cycle.run_date,
        "runCycle": cycle.run_cycle,
        "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
        "label": cycle.label,
    }


def _cache_metadata(
    cache_dir: Path | str | None,
    cache_ttl_hours: float,
    no_cache: bool,
    hour_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fetches = [artifact.get("fetch", {}) for artifact in (hour_artifacts or [])]
    hits = sum(1 for item in fetches if item.get("cacheHit"))
    return {
        "enabled": not no_cache and cache_dir is not None,
        "dir": str(cache_dir) if cache_dir is not None else None,
        "ttlHours": cache_ttl_hours,
        "hits": hits,
        "misses": max(0, len(fetches) - hits),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Artifact output directory.")
    parser.add_argument("--model", default="hrrr", choices=["hrrr", "ecmwf"], help="Convective forecast model to ingest.")
    parser.add_argument("--forecast-hours", type=int, nargs="+")
    parser.add_argument("--all-hours", action="store_true", help="Process every forecast hour f00 through f48.")
    parser.add_argument(
        "--require-complete-hour",
        type=int,
        help="Override the HRRR forecast hour used for cycle-completeness checks.",
    )
    parser.add_argument(
        "--cycle-policy",
        choices=CYCLE_POLICIES,
        help=(
            "HRRR cycle selection policy. Normal mode defaults to complete-requested; "
            "incremental mode defaults to latest-startable."
        ),
    )
    parser.add_argument("--max-workers", type=int, default=None, help="Deprecated alias for --range-workers.")
    parser.add_argument("--hour-workers", type=int, default=DEFAULT_HOUR_WORKERS, help="Parallel forecast-hour workers for incremental mode.")
    parser.add_argument("--range-workers", type=int, default=DEFAULT_RANGE_WORKERS, help="Parallel HRRR byte-range downloads inside each hour.")
    parser.add_argument("--grid-stride", type=int, default=DEFAULT_GRID_STRIDE, help="Downsample decoded HRRR grids after decode.")
    parser.add_argument("--tile-stride", type=int, default=None, help="Optional probability-tile stride; defaults to AUTOOUTLOOK_TILE_STRIDE or grid stride.")
    parser.add_argument("--min-successful-hours", type=int, default=8)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_SELECTED_CACHE_DIR)
    parser.add_argument("--cache-ttl-hours", type=float, default=DEFAULT_CACHE_TTL_HOURS)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-spc-verify", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Run forever on a schedule.")
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    parser.add_argument("--incremental", action="store_true", help="Publish each forecast hour as soon as it is generated.")
    parser.add_argument("--publish-each-hour", action="store_true", help="Alias for --incremental.")
    parser.add_argument("--initial-hours", type=int, nargs="+", help="Optional first hours to process before the remaining requested hours.")
    parser.add_argument("--hour-delay-seconds", type=float, default=0.0)
    parser.add_argument("--stop-after-hour", type=int)
    parser.add_argument("--force", action="store_true", help="Regenerate incremental hours even when existing artifacts are marked ready.")
    parser.add_argument("--continue-on-hour-failure", action="store_true", default=True)
    parser.add_argument("--stop-on-hour-failure", dest="continue_on_hour_failure", action="store_false")
    parser.add_argument("--publish-gcs-bucket", default=os.environ.get("AUTOOUTLOOK_PUBLISH_GCS_BUCKET", ""), help="Upload finished incremental artifacts to this Cloud Storage bucket.")
    parser.add_argument("--publish-gcs-prefix", default=os.environ.get("AUTOOUTLOOK_PUBLISH_GCS_PREFIX", ""), help="Optional object prefix for GCS artifact publishing.")
    parser.add_argument("--gcs-lock-bucket", default=os.environ.get("AUTOOUTLOOK_RUN_LOCK_BUCKET", ""), help="Cloud Storage bucket used for best-effort overlap prevention.")
    parser.add_argument("--gcs-lock-name", default=os.environ.get("AUTOOUTLOOK_RUN_LOCK_NAME", "locks/autooutlook-artifact-refresh.lock"), help="Object name used for the overlap-prevention lock.")
    parser.add_argument("--gcs-lock-ttl-seconds", type=int, default=DEFAULT_GCS_LOCK_TTL_SECONDS, help="Seconds before an abandoned GCS run lock may be replaced.")
    parser.add_argument("--task-shard-finalize-timeout-seconds", type=int, default=_env_int("AUTOOUTLOOK_TASK_SHARD_FINALIZE_TIMEOUT_SECONDS", 2700), help="Seconds task 0 waits for all task-sharded incremental hours before publishing the complete snapshot.")
    parser.add_argument("--task-shard-finalize-poll-seconds", type=int, default=_env_int("AUTOOUTLOOK_TASK_SHARD_FINALIZE_POLL_SECONDS", 20), help="Seconds between task-shard finalization GCS hydrate checks.")
    args = parser.parse_args()
    if args.all_hours and args.forecast_hours:
        parser.error("--all-hours cannot be combined with --forecast-hours")
    return args


def resolve_cli_forecast_hours(args: argparse.Namespace) -> list[int]:
    incremental_mode = bool(args.incremental or args.publish_each_hour)
    incremental_all_hours = incremental_mode and args.forecast_hours is None
    forecast_hours = resolve_forecast_hours(
        args.forecast_hours,
        all_hours=bool(args.all_hours or incremental_all_hours),
    )
    if args.initial_hours:
        forecast_hours = sorted({*args.initial_hours, *forecast_hours})
    return forecast_hours


def cloud_run_task_shard_from_env(environ: Mapping[str, str] | None = None) -> CloudRunTaskShard | None:
    env = environ if environ is not None else os.environ
    raw_count = _optional_int(env.get("CLOUD_RUN_TASK_COUNT"))
    raw_index = _optional_int(env.get("CLOUD_RUN_TASK_INDEX"))
    if raw_count is None or raw_index is None or raw_count <= 1:
        return None
    if raw_index < 0 or raw_index >= raw_count:
        raise ValueError(f"CLOUD_RUN_TASK_INDEX must be in 0..{raw_count - 1}: {raw_index}")
    return CloudRunTaskShard(index=raw_index, count=raw_count)


def resolve_cloud_run_task_forecast_hours(
    forecast_hours: Iterable[int],
    task_shard: CloudRunTaskShard | None,
) -> list[int]:
    hours = resolve_forecast_hours(forecast_hours)
    if task_shard is None:
        return hours
    return [
        hour
        for ordinal, hour in enumerate(hours)
        if ordinal % task_shard.count == task_shard.index
    ]


def _task_sharded_lock_name(lock_name: str, task_shard: CloudRunTaskShard | None) -> str:
    clean = lock_name.strip().strip("/")
    if task_shard is None:
        return clean
    return f"{clean}.task-{task_shard.index:02d}"


def main() -> None:
    args = parse_args()
    incremental_mode = args.incremental or args.publish_each_hour
    forecast_hours = resolve_cli_forecast_hours(args)
    task_shard = cloud_run_task_shard_from_env() if incremental_mode else None
    process_forecast_hours = resolve_cloud_run_task_forecast_hours(forecast_hours, task_shard)
    cycle_policy = resolve_cycle_policy(args.cycle_policy, incremental=incremental_mode)
    output_dir = args.output_dir or (DEFAULT_INCREMENTAL_OUTPUT_DIR if incremental_mode else DEFAULT_OUTPUT_DIR)
    run_lock = None
    if args.gcs_lock_bucket:
        lock_name = _task_sharded_lock_name(args.gcs_lock_name, task_shard)
        run_lock = _try_acquire_gcs_run_lock(args.gcs_lock_bucket, lock_name, args.gcs_lock_ttl_seconds)
        if run_lock is None:
            print(json.dumps({
                "status": "skipped",
                "reason": "run_lock_held",
                "lockBucket": args.gcs_lock_bucket,
                "lockName": lock_name,
            }, indent=2))
            return
    try:
        while True:
            if incremental_mode:
                if args.publish_gcs_bucket:
                    _hydrate_incremental_artifacts_from_gcs(output_dir, args.publish_gcs_bucket, args.publish_gcs_prefix)
                metadata = run_incremental_pipeline(
                    output_dir=output_dir,
                    forecast_hours=forecast_hours,
                    process_forecast_hours=process_forecast_hours,
                    max_workers=args.max_workers,
                    hour_workers=args.hour_workers,
                    range_workers=args.range_workers,
                    tile_stride=args.tile_stride,
                    grid_stride=args.grid_stride,
                    cache_dir=args.cache_dir,
                    cache_ttl_hours=args.cache_ttl_hours,
                    no_cache=args.no_cache,
                    hour_delay_seconds=args.hour_delay_seconds,
                    stop_after_hour=args.stop_after_hour,
                    continue_on_hour_failure=args.continue_on_hour_failure,
                    force=args.force,
                    cycle_policy=cycle_policy,
                    require_complete_hour=args.require_complete_hour,
                    verify_spc=not args.no_spc_verify,
                    publish_gcs_bucket=args.publish_gcs_bucket,
                    publish_gcs_prefix=args.publish_gcs_prefix,
                    model_name=args.model,
                )
                needs_shard_finalizer = (
                    task_shard is not None
                    and task_shard.index == 0
                    and bool(args.publish_gcs_bucket)
                    and (metadata.get("status") != "complete" or bool(metadata.get("artifactChangesThisRun")))
                )
                if needs_shard_finalizer:
                    metadata = _finalize_sharded_incremental_snapshot(
                        output_dir,
                        metadata,
                        forecast_hours,
                        args.publish_gcs_bucket,
                        args.publish_gcs_prefix,
                        timeout_seconds=args.task_shard_finalize_timeout_seconds,
                        poll_seconds=args.task_shard_finalize_poll_seconds,
                    )
            else:
                metadata = run_pipeline(
                    output_dir=output_dir,
                    forecast_hours=forecast_hours,
                    max_workers=args.range_workers if args.max_workers is None else args.max_workers,
                    tile_stride=args.tile_stride,
                    grid_stride=args.grid_stride,
                    min_successful_hours=args.min_successful_hours,
                    cache_dir=args.cache_dir,
                    cache_ttl_hours=args.cache_ttl_hours,
                    no_cache=args.no_cache,
                    verify_spc=not args.no_spc_verify,
                    preview=not args.no_preview,
                    cycle_policy=cycle_policy,
                    require_complete_hour=args.require_complete_hour,
                    model_name=args.model,
                )
            print(json.dumps({
                "outputDir": str(output_dir),
                "cycle": metadata["cycle"],
                "generatedAtISO": metadata["generatedAtISO"],
                "latencyMs": metadata["latencyMs"],
                "hourWorkers": metadata.get("hourWorkers"),
                "rangeWorkers": metadata.get("rangeWorkers"),
                "taskShard": {"index": task_shard.index, "count": task_shard.count} if task_shard else None,
                "processForecastHours": metadata.get("processForecastHours"),
                "gridStride": metadata.get("gridStride"),
                "tileStride": metadata.get("tileStride"),
            }, indent=2))
            if not args.loop:
                return
            time.sleep(max(60.0, args.interval_minutes * 60.0))
    finally:
        if run_lock is not None:
            _release_gcs_run_lock(run_lock)


if __name__ == "__main__":
    main()
