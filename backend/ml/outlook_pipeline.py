"""Scheduled HRRR-to-SPC-style gridded outlook artifact pipeline."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import requests

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
from backend.bundle_builder import _hgt500_lines_from_field, _wind500_vectors_from_fields
from backend.ml.features import FEATURE_NAMES, feature_schema_hash
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    GriddedFeatures,
    apply_category_probability_ceiling,
    apply_environmental_probability_caps,
    apply_offshore_probability_suppression,
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
) -> dict[str, Any]:
    """Generate deployable prediction artifacts, then optionally verify against SPC."""
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    output_dir = Path(output_dir)
    working_dir = output_dir.with_name(f"{output_dir.name}.tmp")
    if working_dir.exists():
        shutil.rmtree(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)
    hours = resolve_forecast_hours(forecast_hours)
    resolved_cycle_policy = resolve_cycle_policy(cycle_policy, incremental=False)
    required_forecast_hour = resolve_required_forecast_hour(hours, require_complete_hour, resolved_cycle_policy)
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
        detection = _detect_hrrr_cycle(session, now, required_forecast_hour, detect_cycle_fn)
        cycle, cycle_detection_metadata = _normalize_cycle_detection(
            detection,
            hours,
            required_forecast_hour,
            resolved_cycle_policy,
            require_complete_hour,
        )
        print(f"[cycle selected] HRRR {cycle.run_cycle:02d}Z {cycle.run_date}", flush=True)
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
        aggregate_probabilities = _aggregate_probabilities(probability_grids)
        aggregate_features = _aggregate_feature_grids(feature_grids)
        aggregate_polygons = risk_polygons_from_grid(
            base_lats,
            base_lons,
            aggregate_grid,
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
            preview_file = _render_preview(working_dir / "preview.png", base_lats, base_lons, aggregate_grid)

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
) -> dict[str, Any]:
    """Publish per-hour artifacts as soon as each HRRR hour is processed."""
    started = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "hours").mkdir(parents=True, exist_ok=True)
    requested_force_hours = set(resolve_forecast_hours(forecast_hours))
    hours = sorted(requested_force_hours)
    resolved_cycle_policy = resolve_cycle_policy(cycle_policy, incremental=True)
    required_forecast_hour = resolve_required_forecast_hour(hours, require_complete_hour, resolved_cycle_policy)
    hour_workers = _resolve_worker_count(hour_workers, DEFAULT_HOUR_WORKERS)
    range_workers = _resolve_worker_count(range_workers if range_workers is not None else max_workers, DEFAULT_RANGE_WORKERS)
    grid_stride = _resolve_grid_stride(grid_stride)
    tile_stride = _resolve_tile_stride(tile_stride, grid_stride)

    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0 incremental"
    ready_hours: list[int] = []
    failed_hours: list[dict[str, Any]] = []
    try:
        predictor = predictor_fn or predict_hazard_grids
        print(f"[cycle check] requested hours require f{required_forecast_hour:02d}", flush=True)
        detection = _detect_hrrr_cycle(session, now, required_forecast_hour, detect_cycle_fn)
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
            _clear_incremental_hour_artifacts(output_dir)
        if existing_index is not None:
            existing_ready = [
                hour for hour in _int_list(existing_index.get("readyForecastHours"))
                if _incremental_hour_ready(output_dir, hour)
            ]
            existing_ready = sorted({
                *existing_ready,
                *(hour for hour in hours if _incremental_hour_ready(output_dir, hour)),
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
            _write_json(output_dir / "index.json", payload)
            _write_json(output_dir / "metadata.json", payload)
            return payload

        index = write_index("running")
        process_hours: list[int] = []
        for forecast_hour in hours:
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
            status = "partial" if stop_after_hour is not None or failed_hours else "complete"
            index = write_index(status)
        _publish_complete_incremental_snapshot(output_dir, index, hours)
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
) -> FetchedHour:
    if fetch_hour is None:
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
            "latencyMs": 0,
            "timing": timing,
            "categoryCounts": counts,
            "categoryCountsBeforeCaps": category_counts(built["categoryGridBeforeCaps"]),
            "categoryCountsAfterCaps": category_counts(built["categoryGridAfterCaps"]),
            "categoryCountsAfterSmoothing": counts,
            "probabilityStats": built["probabilityReport"],
            "postProcessing": built["postProcessingReport"],
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


def _normalize_fetched_hour(
    ref: HrrrHourRef,
    result: tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]] | SelectedHrrrHour,
) -> FetchedHour:
    if isinstance(result, SelectedHrrrHour):
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
    features = gridded_features_from_fields(fetched.fields, forecast_hour)
    raw_probabilities = predictor(features)
    if raw_probabilities is None:
        raise RuntimeError("ML hazard model returned no gridded probabilities")
    category_before_caps = category_grid_from_probabilities(raw_probabilities, features, model)
    cap_result = apply_environmental_probability_caps(raw_probabilities, features, model)
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
    probability_report = {
        **cap_result.report,
        "environmentalCappedProbabilityMax": cap_result.report.get("cappedProbabilityMax"),
        "cappedProbabilityMax": final_probability_result.report.get("offshoreSuppressedProbabilityMax"),
        **category_probability_result.report,
        **final_probability_result.report,
    }
    valid_time_iso = _valid_iso(cycle, forecast_hour)
    polygons = risk_polygons_from_grid(fetched.lats, fetched.lons, post_result.category_grid, forecast_hour, valid_time_iso)
    hazard_shapes = hazard_probability_shapes_from_grids(
        fetched.lats,
        fetched.lons,
        final_probability_result.probabilities,
        post_result.category_grid,
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
    return {
        "features": features,
        "rawProbabilities": raw_probabilities,
        "probabilities": final_probability_result.probabilities,
        "probabilityReport": probability_report,
        "categoryGridBeforeCaps": category_before_caps,
        "categoryGridAfterCaps": category_after_caps,
        "categoryGrid": post_result.category_grid,
        "postProcessingReport": post_result.report,
        "validTimeISO": valid_time_iso,
        "polygons": polygons,
        "hazardProbabilityShapes": hazard_shapes,
        "tile": tile,
        "upperAirOverlay": upper_air_overlay,
    }


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
    tmp_dir = complete_dir.with_name(f"{complete_dir.name}.tmp")
    backup_dir = complete_dir.with_name(f"{complete_dir.name}.previous")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    shutil.copytree(output_dir, tmp_dir)
    try:
        if complete_dir.exists():
            shutil.move(str(complete_dir), str(backup_dir))
        shutil.move(str(tmp_dir), str(complete_dir))
    except Exception:
        shutil.rmtree(complete_dir, ignore_errors=True)
        if backup_dir.exists():
            shutil.move(str(backup_dir), str(complete_dir))
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)


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


def _clear_incremental_hour_artifacts(output_dir: Path) -> None:
    hours_dir = output_dir / "hours"
    if hours_dir.exists():
        shutil.rmtree(hours_dir, ignore_errors=True)
    hours_dir.mkdir(parents=True, exist_ok=True)


def _clear_incremental_hour_artifact(output_dir: Path, forecast_hour: int) -> None:
    hour_dir = output_dir / "hours" / f"f{int(forecast_hour):02d}"
    if hour_dir.exists():
        shutil.rmtree(hour_dir, ignore_errors=True)


def _incremental_hour_ready(output_dir: Path, forecast_hour: int) -> bool:
    hour_dir = output_dir / "hours" / f"f{int(forecast_hour):02d}"
    return all(
        (hour_dir / name).exists()
        for name in ("risk_polygons.geojson", "probability_tile.json", "upper_air_overlay.json", "metadata.json")
    )


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


def resolve_forecast_hours(
    forecast_hours: Iterable[int] | None = None,
    all_hours: bool = False,
) -> list[int]:
    if all_hours and forecast_hours is not None:
        raise ValueError("--all-hours cannot be combined with --forecast-hours")
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
) -> int:
    if require_complete_hour is not None:
        required = int(require_complete_hour)
    elif cycle_policy == "complete-48":
        required = 48
    elif cycle_policy == "latest-startable":
        required = 0
    else:
        hours = sorted({int(hour) for hour in forecast_hours})
        if not hours:
            raise ValueError("At least one forecast hour is required")
        required = max(hours)
    if required < 0 or required > 48:
        raise ValueError(f"Required complete HRRR forecast hour must be in 0..48: {required}")
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
) -> HrrrCycle | HrrrCycleDetection:
    if detect_cycle_fn is not None:
        return detect_cycle_fn(session, now)
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


def main() -> None:
    args = parse_args()
    incremental_mode = args.incremental or args.publish_each_hour
    forecast_hours = resolve_cli_forecast_hours(args)
    cycle_policy = resolve_cycle_policy(args.cycle_policy, incremental=incremental_mode)
    output_dir = args.output_dir or (DEFAULT_INCREMENTAL_OUTPUT_DIR if incremental_mode else DEFAULT_OUTPUT_DIR)
    while True:
        if incremental_mode:
            metadata = run_incremental_pipeline(
                output_dir=output_dir,
                forecast_hours=forecast_hours,
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
            )
        print(json.dumps({
            "outputDir": str(output_dir),
            "cycle": metadata["cycle"],
            "generatedAtISO": metadata["generatedAtISO"],
            "latencyMs": metadata["latencyMs"],
            "hourWorkers": metadata.get("hourWorkers"),
            "rangeWorkers": metadata.get("rangeWorkers"),
            "gridStride": metadata.get("gridStride"),
            "tileStride": metadata.get("tileStride"),
        }, indent=2))
        if not args.loop:
            return
        time.sleep(max(60.0, args.interval_minutes * 60.0))


if __name__ == "__main__":
    main()
