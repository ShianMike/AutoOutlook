"""Scheduled HRRR-to-SPC-style gridded outlook artifact pipeline."""
from __future__ import annotations

import argparse
import json
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
from backend.ml.features import FEATURE_NAMES, feature_schema_hash
from backend.ml.gridded_outlook import (
    SPC_RISK_LABELS,
    GriddedFeatures,
    apply_category_probability_ceiling,
    apply_environmental_probability_caps,
    category_counts,
    category_grid_from_probabilities,
    feature_stats,
    gridded_features_from_fields,
    merge_feature_collections,
    postprocess_category_grid,
    predict_hazard_grids,
    probability_tile,
    risk_polygons_from_grid,
)
from backend.ml.inference import model_status
from backend.ml.spc_verification import compare_prediction_to_spc, fetch_current_spc_day1_category

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "latest"
DEFAULT_INCREMENTAL_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "latest_incremental"
ALL_FORECAST_HOURS = tuple(range(49))
PRODUCTION_FORECAST_HOURS = tuple(list(range(19)) + list(range(21, 49, 3)))
FORECAST_HOURS = PRODUCTION_FORECAST_HOURS

FetchHourFn = Callable[[HrrrHourRef, requests.Session], tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]] | SelectedHrrrHour]
PredictorFn = Callable[[GriddedFeatures], dict[str, np.ndarray] | None]
SpcFetchFn = Callable[[requests.Session, Path | None], dict[str, Any]]


@dataclass(frozen=True)
class FetchedHour:
    lats: np.ndarray
    lons: np.ndarray
    fields: dict[str, np.ndarray]
    metadata: dict[str, Any]


def run_pipeline(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    forecast_hours: Iterable[int] | None = None,
    now: datetime | None = None,
    max_workers: int = 3,
    tile_stride: int | None = None,
    grid_stride: int = 3,
    min_successful_hours: int = 8,
    cache_dir: Path | str | None = DEFAULT_SELECTED_CACHE_DIR,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    no_cache: bool = False,
    verify_spc: bool = True,
    preview: bool = True,
    detect_cycle_fn: Callable[[requests.Session, datetime | None], HrrrCycle | HrrrCycleDetection] | None = None,
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
    effective_min_successful = min(max(1, int(min_successful_hours)), len(hours))
    tile_stride = max(1, int(tile_stride if tile_stride is not None else grid_stride))
    grid_stride = max(1, int(grid_stride))
    failure_context: dict[str, Any] = {
        "requestedForecastHours": hours,
        "minSuccessfulHours": int(min_successful_hours),
        "effectiveMinSuccessfulHours": effective_min_successful,
        "gridStride": grid_stride,
        "cache": _cache_metadata(cache_dir, cache_ttl_hours, no_cache),
    }

    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0"
    try:
        detect = detect_cycle_fn or (lambda sess, dt: latest_available_hrrr_cycle_with_metadata(sess, dt))
        predictor = predictor_fn or predict_hazard_grids
        spc_fetch = spc_fetch_fn or fetch_current_spc_day1_category

        detection = detect(session, now)
        cycle, cycle_detection_metadata = _normalize_cycle_detection(detection)
        failure_context["cycle"] = cycle.label
        failure_context["cycleDetection"] = cycle_detection_metadata
        model = model_status()
        failure_context["model"] = model
        if not model.get("active"):
            raise RuntimeError(f"ML model inactive; refusing deployable outlook generation: {model.get('reason', 'unknown')}")

        raw_hours, failed_hours = _fetch_hours(
            cycle,
            hours,
            session,
            fetch_hour_fn,
            max_workers=max_workers,
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
    max_workers: int = 1,
    tile_stride: int | None = None,
    grid_stride: int = 3,
    cache_dir: Path | str | None = DEFAULT_SELECTED_CACHE_DIR,
    cache_ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
    no_cache: bool = False,
    hour_delay_seconds: float = 0.0,
    stop_after_hour: int | None = None,
    continue_on_hour_failure: bool = True,
    force: bool = False,
    detect_cycle_fn: Callable[[requests.Session, datetime | None], HrrrCycle | HrrrCycleDetection] | None = None,
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
    tile_stride = max(1, int(tile_stride if tile_stride is not None else grid_stride))
    grid_stride = max(1, int(grid_stride))

    session = requests.Session()
    session.headers["User-Agent"] = "AutoOutlook-outlook-pipeline/1.0 incremental"
    ready_hours: list[int] = []
    failed_hours: list[dict[str, Any]] = []
    try:
        detect = detect_cycle_fn or (lambda sess, dt: latest_available_hrrr_cycle_with_metadata(sess, dt))
        predictor = predictor_fn or predict_hazard_grids
        detection = detect(session, now)
        cycle, cycle_detection_metadata = _normalize_cycle_detection(detection)
        model = model_status()
        if not model.get("active"):
            raise RuntimeError(f"ML model inactive; refusing incremental outlook generation: {model.get('reason', 'unknown')}")
        existing_index = _read_incremental_index(output_dir, cycle)
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
            ready = sorted(ready_hours)
            failed = sorted({int(item["forecastHour"]) for item in failed_hours})
            pending = [hour for hour in hours if hour not in ready and hour not in failed]
            payload = {
                "cycle": cycle.label,
                "cycleTimeISO": cycle.cycle_time.isoformat().replace("+00:00", "Z"),
                "cycleMetadata": _cycle_metadata(cycle),
                "cycleDetection": cycle_detection_metadata,
                "generatedAtISO": _now_iso(),
                "mode": "incremental",
                "requestedForecastHours": hours,
                "readyForecastHours": ready,
                "failedForecastHours": failed,
                "failedHours": failed_hours,
                "pendingForecastHours": pending,
                "latestReadyForecastHour": ready[-1] if ready else None,
                "status": status,
                "gridStride": grid_stride,
                "tileStride": tile_stride,
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
                    "hours": "hours/fXX/{risk_polygons.geojson,probability_tile.json,metadata.json}",
                },
                "latencyMs": int((time.perf_counter() - started) * 1000),
            }
            _write_json(output_dir / "index.json", payload)
            _write_json(output_dir / "metadata.json", payload)
            return payload

        index = write_index("running")
        for forecast_hour in hours:
            hour_started = time.perf_counter()
            try:
                should_force_hour = force and forecast_hour in requested_force_hours
                if not should_force_hour and forecast_hour in ready_hours and _incremental_hour_ready(output_dir, forecast_hour):
                    print(f"[incremental skip] F{forecast_hour:02d} already ready", flush=True)
                    status = "running"
                    if stop_after_hour is not None and forecast_hour >= stop_after_hour:
                        status = "partial"
                        index = write_index(status)
                        break
                    index = write_index(status)
                    continue
                failed_hours = [
                    item for item in failed_hours
                    if int(item.get("forecastHour", -999)) != int(forecast_hour)
                ]
                ref = hour_ref(cycle, forecast_hour)
                fetched = _fetch_one_hour(
                    ref,
                    session,
                    fetch_hour_fn,
                    max_workers=max_workers,
                    cache_dir=cache_dir,
                    cache_ttl_hours=cache_ttl_hours,
                    no_cache=no_cache,
                    grid_stride=grid_stride,
                )
                built = _build_hour_artifact(cycle, forecast_hour, fetched, predictor, model, tile_stride)
                hour_dir = output_dir / "hours" / f"f{forecast_hour:02d}"
                hour_dir.mkdir(parents=True, exist_ok=True)
                hour_metadata = {
                    "forecastHour": forecast_hour,
                    "validTimeISO": built["validTimeISO"],
                    "status": "ready",
                    "generatedAtISO": _now_iso(),
                    "latencyMs": int((time.perf_counter() - hour_started) * 1000),
                    "categoryCounts": category_counts(built["categoryGrid"]),
                    "categoryCountsBeforeCaps": category_counts(built["categoryGridBeforeCaps"]),
                    "categoryCountsAfterCaps": category_counts(built["categoryGridAfterCaps"]),
                    "categoryCountsAfterSmoothing": category_counts(built["categoryGrid"]),
                    "probabilityStats": built["probabilityReport"],
                    "postProcessing": built["postProcessingReport"],
                    "fetch": fetched.metadata,
                    "artifacts": {
                        "riskPolygons": "risk_polygons.geojson",
                        "probabilityTile": "probability_tile.json",
                        "metadata": "metadata.json",
                    },
                }
                _write_json(hour_dir / "risk_polygons.geojson", built["polygons"])
                _write_json(hour_dir / "probability_tile.json", built["tile"])
                _write_json(hour_dir / "metadata.json", hour_metadata)
                if forecast_hour not in ready_hours:
                    ready_hours.append(forecast_hour)
                print(
                    f"[incremental ok] F{forecast_hour:02d} "
                    f"cat={category_counts(built['categoryGrid'])} "
                    f"latency={hour_metadata['latencyMs']}ms",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                failure = {
                    "forecastHour": forecast_hour,
                    "stage": "incremental",
                    "error": f"{type(exc).__name__}: {exc}",
                    "generatedAtISO": _now_iso(),
                }
                failed_hours.append(failure)
                hour_dir = output_dir / "hours" / f"f{forecast_hour:02d}"
                hour_dir.mkdir(parents=True, exist_ok=True)
                _write_json(hour_dir / "metadata.json", {**failure, "status": "failed"})
                print(f"[incremental fail] F{forecast_hour:02d} {failure['error']}", flush=True)
                if not continue_on_hour_failure:
                    write_index("failed")
                    raise
            status = "running"
            if stop_after_hour is not None and forecast_hour >= stop_after_hour:
                status = "partial"
                index = write_index(status)
                break
            index = write_index(status)
            if hour_delay_seconds > 0:
                time.sleep(hour_delay_seconds)
        else:
            index = write_index("complete" if not failed_hours else "partial")
        return index
    except Exception:
        if output_dir.exists():
            failed = sorted({int(item["forecastHour"]) for item in failed_hours})
            _write_json(output_dir / "index.json", {
                "generatedAtISO": _now_iso(),
                "mode": "incremental",
                "requestedForecastHours": hours,
                "readyForecastHours": sorted(ready_hours),
                "failedForecastHours": failed,
                "failedHours": failed_hours,
                "pendingForecastHours": [hour for hour in hours if hour not in ready_hours and hour not in failed],
                "status": "failed",
                "previousLatestPreserved": DEFAULT_OUTPUT_DIR.exists(),
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
    final_probability_result = apply_category_probability_ceiling(cap_result.probabilities, post_result.category_grid)
    probability_report = {
        **cap_result.report,
        "environmentalCappedProbabilityMax": cap_result.report.get("cappedProbabilityMax"),
        "cappedProbabilityMax": final_probability_result.report.get("categoryConsistencyProbabilityMax"),
        **final_probability_result.report,
    }
    valid_time_iso = _valid_iso(cycle, forecast_hour)
    polygons = risk_polygons_from_grid(fetched.lats, fetched.lons, post_result.category_grid, forecast_hour, valid_time_iso)
    tile = probability_tile(
        fetched.lats,
        fetched.lons,
        final_probability_result.probabilities,
        post_result.category_grid,
        forecast_hour,
        valid_time_iso,
        stride=tile_stride,
    )
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
        "tile": tile,
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


def _read_incremental_index(output_dir: Path, cycle: HrrrCycle) -> dict[str, Any] | None:
    index_path = output_dir / "index.json"
    if not index_path.exists():
        return None
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("cycle") != cycle.label:
        return None
    return payload if isinstance(payload, dict) else None


def _incremental_hour_ready(output_dir: Path, forecast_hour: int) -> bool:
    hour_dir = output_dir / "hours" / f"f{int(forecast_hour):02d}"
    return all(
        (hour_dir / name).exists()
        for name in ("risk_polygons.geojson", "probability_tile.json", "metadata.json")
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


def _normalize_cycle_detection(detection: HrrrCycle | HrrrCycleDetection) -> tuple[HrrrCycle, dict[str, Any]]:
    if isinstance(detection, HrrrCycleDetection):
        return detection.selected, detection.metadata
    return detection, {
        "selected": _cycle_metadata(detection),
        "checkedCycles": [],
        "preferredCyclesUTC": [0, 6, 12, 18],
        "requiredForecastHours": [0, 48],
    }


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
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--forecast-hours", type=int, nargs="+")
    parser.add_argument("--all-hours", action="store_true", help="Process every forecast hour f00 through f48.")
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--grid-stride", type=int, default=3, help="Downsample decoded HRRR grids after decode.")
    parser.add_argument("--tile-stride", type=int, default=None, help="Optional probability-tile stride; defaults to grid stride.")
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
    while True:
        if incremental_mode:
            metadata = run_incremental_pipeline(
                output_dir=DEFAULT_INCREMENTAL_OUTPUT_DIR,
                forecast_hours=forecast_hours,
                max_workers=max(1, args.max_workers),
                tile_stride=args.tile_stride,
                grid_stride=args.grid_stride,
                cache_dir=args.cache_dir,
                cache_ttl_hours=args.cache_ttl_hours,
                no_cache=args.no_cache,
                hour_delay_seconds=args.hour_delay_seconds,
                stop_after_hour=args.stop_after_hour,
                continue_on_hour_failure=args.continue_on_hour_failure,
                force=args.force,
            )
            output_dir = DEFAULT_INCREMENTAL_OUTPUT_DIR
        else:
            metadata = run_pipeline(
                output_dir=args.output_dir,
                forecast_hours=forecast_hours,
                max_workers=args.max_workers,
                tile_stride=args.tile_stride,
                grid_stride=args.grid_stride,
                min_successful_hours=args.min_successful_hours,
                cache_dir=args.cache_dir,
                cache_ttl_hours=args.cache_ttl_hours,
                no_cache=args.no_cache,
                verify_spc=not args.no_spc_verify,
                preview=not args.no_preview,
            )
            output_dir = args.output_dir
        print(json.dumps({
            "outputDir": str(output_dir),
            "cycle": metadata["cycle"],
            "generatedAtISO": metadata["generatedAtISO"],
            "latencyMs": metadata["latencyMs"],
        }, indent=2))
        if not args.loop:
            return
        time.sleep(max(60.0, args.interval_minutes * 60.0))


if __name__ == "__main__":
    main()
