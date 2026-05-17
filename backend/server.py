"""Flask entrypoint for AutoOutlook's local data service.

GET /api/forecast  -> returns a backend-bundle JSON for the latest HRRR cycle.
GET /api/health    -> tiny liveness probe.

Run with:  python -m backend.server   (from the project root)
       or: python backend/server.py   (also works; sys.path is patched)
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import json
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `python backend/server.py` to import sibling modules when invoked
# as a script rather than `python -m backend.server`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import abort, jsonify, send_file, send_from_directory, Flask  # noqa: E402
from flask_cors import CORS  # noqa: E402

from backend.cache import TTLCache  # noqa: E402
from backend.bundle_builder import (  # noqa: E402
    CONUS_CITIES,
    _classify_cap,
    _classify_storm_mode,
    _initiation_confidence,
    build_bundle,
)
from backend.nomads_pipeline import NomadsFetchError  # noqa: E402

LOG_FMT = "[%(asctime)s] %(levelname)s %(name)s :: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("autooutlook")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "dist"

app = Flask(__name__)


def _cors_origins() -> str | list[str]:
    raw = os.environ.get("AUTOOUTLOOK_CORS_ORIGINS", "").strip()
    if not raw:
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    if raw == "*":
        return "*"
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


CORS(app, resources={r"/api/*": {"origins": _cors_origins()}})

cache = TTLCache(ttl_seconds=600)  # 10 min
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()
_gcs_client = None
_gcs_client_guard = threading.Lock()
ARTIFACT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_ARTIFACT_DIR",
    Path(__file__).resolve().parent / "artifacts" / "latest",
))
INCREMENTAL_ARTIFACT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_INCREMENTAL_ARTIFACT_DIR",
    Path(__file__).resolve().parent / "artifacts" / "latest_incremental",
))
_INCREMENTAL_COMPLETE_ARTIFACT_DIR_ENV = os.environ.get("AUTOOUTLOOK_INCREMENTAL_COMPLETE_ARTIFACT_DIR")
INCREMENTAL_COMPLETE_ARTIFACT_DIR = (
    Path(_INCREMENTAL_COMPLETE_ARTIFACT_DIR_ENV)
    if _INCREMENTAL_COMPLETE_ARTIFACT_DIR_ENV
    else None
)
FULL_INCREMENTAL_FORECAST_HOURS = set(range(49))
JSON_ARTIFACT_CACHE_SECONDS = int(os.environ.get("AUTOOUTLOOK_JSON_CACHE_SECONDS", "300"))
PNG_ARTIFACT_CACHE_SECONDS = int(os.environ.get("AUTOOUTLOOK_PNG_CACHE_SECONDS", "900"))
STATIC_ASSET_CACHE_SECONDS = 31536000


def _singleflight_lock(key: str) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[key] = lock
        return lock


def _cycle_key(now: datetime) -> str:
    cycle_hour = (now.hour // 6) * 6
    return f"HRRR-{now.date().isoformat()}-{cycle_hour:02d}Z"


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _artifact_cycle_time() -> datetime | None:
    index_path = _selected_incremental_artifact_dir() / "index.json"
    index = _read_json_path(index_path)
    if not isinstance(index, dict):
        return None
    cycle_time = _parse_iso_utc(index.get("cycleTimeISO"))
    ready = index.get("readyForecastHours")
    if cycle_time is None or not isinstance(ready, list) or 0 not in ready:
        return None
    return cycle_time


def _forecast_base_time(now: datetime) -> datetime:
    return _artifact_cycle_time() or now


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "autooutlook-backend"})


def _json_response(payload, cache_seconds: int | None = JSON_ARTIFACT_CACHE_SECONDS):
    response = jsonify(payload)
    if cache_seconds is not None and cache_seconds > 0:
        response.headers["Cache-Control"] = f"public, max-age={int(cache_seconds)}"
    return response


def _json_error(payload: dict, status_code: int):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _live_build_enabled() -> bool:
    return _bool_env("AUTOOUTLOOK_ENABLE_LIVE_BUILD", True)


@app.get("/")
def frontend_index():
    return _static_file("index.html")


@app.get("/api/forecast")
def forecast():
    now = datetime.now(timezone.utc)
    if _prefer_artifact_forecast():
        artifact_bundle = _artifact_forecast_bundle()
        if artifact_bundle is not None:
            return _json_response(artifact_bundle)
        if not _live_build_enabled():
            return _artifact_not_ready_response(INCREMENTAL_ARTIFACT_DIR / "index.json", status_code=503)

    if not _live_build_enabled():
        return _artifact_not_ready_response(INCREMENTAL_ARTIFACT_DIR / "index.json", status_code=503)

    base_time = _forecast_base_time(now)
    key = _cycle_key(base_time)
    cached = cache.get(key)
    if cached is not None:
        log.info("cache HIT  %s", key)
        return _json_response(cached, cache_seconds=120)

    lock = _singleflight_lock(key)
    with lock:
        cached = cache.get(key)
        if cached is not None:
            log.info("cache HIT after wait %s", key)
            return _json_response(cached, cache_seconds=120)

        log.info("cache MISS %s -> fetching NOMADS HRRR GRIB", key)
        try:
            bundle = build_bundle(now=base_time)
        except NomadsFetchError as exc:
            log.warning("NOMADS fetch failed: %s", exc)
            artifact_bundle = _artifact_forecast_bundle()
            if artifact_bundle is not None:
                return _json_response(artifact_bundle)
            return _json_error({"error": str(exc), "code": "nomads_fetch_failed"}, 503)
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected backend failure")
            return _json_error({"error": str(exc), "code": "backend_error"}, 500)

        cache.set(key, bundle)
        log.info("cache SET  %s (%d ms)", key, bundle.get("latencyMs", -1))
        return _json_response(bundle, cache_seconds=120)


@app.get("/api/outlook/latest")
def latest_outlook_metadata():
    return _json_artifact_or_incremental("metadata.json")


@app.get("/api/outlook/risk-polygons")
def latest_outlook_risk_polygons():
    return _json_artifact_or_incremental("risk_polygons.geojson")


@app.get("/api/outlook/aggregate-risk-polygons")
def latest_outlook_aggregate_risk_polygons():
    return _json_artifact_or_incremental("aggregate_risk_polygons.geojson")


@app.get("/api/outlook/probability-tiles")
def latest_outlook_probability_tiles():
    return _json_artifact_or_incremental("probability_tiles.json")


@app.get("/api/outlook/verification")
def latest_outlook_verification():
    return _json_artifact("verification_summary.json")


@app.get("/api/outlook/incremental")
def incremental_outlook_index():
    return _json_path(_selected_incremental_artifact_dir() / "index.json")


@app.get("/api/outlook/incremental/available-hours")
def incremental_outlook_available_hours():
    return _json_path(_selected_incremental_artifact_dir() / "index.json")


@app.get("/api/outlook/incremental/summary")
def incremental_outlook_summary():
    artifact_dir = _selected_incremental_artifact_dir()
    index_path = artifact_dir / "index.json"
    if not _artifact_exists(index_path):
        return _json_path(index_path)
    index = _read_json_path(index_path)
    if not isinstance(index, dict):
        return _json_path(index_path)
    hours = []
    for forecast_hour in index.get("readyForecastHours", []):
        metadata_path = _incremental_hour_path(int(forecast_hour), artifact_dir) / "metadata.json"
        if not _artifact_exists(metadata_path):
            continue
        metadata = _read_json_path(metadata_path)
        if not isinstance(metadata, dict):
            continue
        category_counts = metadata.get("categoryCounts") or {}
        probability_stats = metadata.get("probabilityStats") or {}
        probability_max = probability_stats.get("categoryConsistencyProbabilityMax") or probability_stats.get("cappedProbabilityMax") or {}
        category = _max_category_from_counts(category_counts)
        display_probability_max = _cap_probabilities_for_category(probability_max, category)
        main_hazard = _main_hazard_from_probabilities(probability_max)
        total_cells = sum(int(value) for value in category_counts.values() if isinstance(value, (int, float)))
        active_cells = total_cells - int(category_counts.get("NONE", 0) or 0)
        hours.append({
            "forecastHour": int(metadata.get("forecastHour", forecast_hour)),
            "validTimeISO": metadata.get("validTimeISO"),
            "category": category,
            "mainHazard": main_hazard,
            "peakHazardProbability": _max_hazard_probability(display_probability_max),
            "significantSevere": _has_significant_probability(display_probability_max, category, main_hazard),
            "coverage": active_cells / total_cells if total_cells > 0 else 0,
            "categoryCounts": category_counts,
            "probabilityMax": display_probability_max,
        })
    return _json_response({
        "cycle": index.get("cycle"),
        "cycleTimeISO": index.get("cycleTimeISO"),
        "generatedAtISO": index.get("generatedAtISO"),
        "hours": sorted(hours, key=lambda item: item["forecastHour"]),
    })


@app.get("/api/outlook/incremental/hour/<int:forecast_hour>/risk-polygons")
def incremental_outlook_hour_risk_polygons(forecast_hour: int):
    return _incremental_hour_json(forecast_hour, "risk_polygons.geojson")


@app.get("/api/outlook/incremental/hour/<int:forecast_hour>/probability-tile")
def incremental_outlook_hour_probability_tile(forecast_hour: int):
    return _incremental_hour_json(forecast_hour, "probability_tile.json")


@app.get("/api/outlook/incremental/hour/<int:forecast_hour>/metadata")
def incremental_outlook_hour_metadata(forecast_hour: int):
    return _incremental_hour_json(forecast_hour, "metadata.json")


@app.get("/api/outlook/preview.png")
def latest_outlook_preview():
    path = _artifact_path("preview.png")
    if not _artifact_exists(path):
        return _artifact_not_ready_response(path)
    return _png_artifact_response(path)


def _artifact_path(name: str) -> Path:
    return ARTIFACT_DIR / name


def _incremental_hour_path(forecast_hour: int, artifact_dir: Path | None = None) -> Path:
    base_dir = artifact_dir or _selected_incremental_artifact_dir()
    return base_dir / "hours" / f"f{int(forecast_hour):02d}"


def _artifact_bucket_name() -> str:
    return os.environ.get("AUTOOUTLOOK_ARTIFACT_BUCKET", "").strip()


def _artifact_prefix() -> str:
    return os.environ.get("AUTOOUTLOOK_ARTIFACT_PREFIX", "").strip().strip("/")


def _using_gcs_artifacts() -> bool:
    return bool(_artifact_bucket_name())


def _get_gcs_client():
    global _gcs_client
    with _gcs_client_guard:
        if _gcs_client is None:
            try:
                from google.cloud import storage  # type: ignore
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("google-cloud-storage is required when AUTOOUTLOOK_ARTIFACT_BUCKET is set") from exc
            _gcs_client = storage.Client()
        return _gcs_client


def _artifact_storage_key(path: Path) -> str:
    roots = [
        ARTIFACT_DIR.parent,
        INCREMENTAL_ARTIFACT_DIR.parent,
        _incremental_complete_artifact_dir().parent,
    ]
    for root in roots:
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        key = relative.as_posix()
        prefix = _artifact_prefix()
        return f"{prefix}/{key}" if prefix else key
    key = path.name
    prefix = _artifact_prefix()
    return f"{prefix}/{key}" if prefix else key


def _artifact_blob(path: Path):
    bucket = _get_gcs_client().bucket(_artifact_bucket_name())
    return bucket.blob(_artifact_storage_key(path))


def _artifact_exists(path: Path) -> bool:
    if not _using_gcs_artifacts():
        return path.exists()
    try:
        return bool(_artifact_blob(path).exists())
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not check artifact object %s: %s", _artifact_storage_key(path), exc)
        return False


def _download_artifact_bytes(path: Path) -> bytes | None:
    if not _using_gcs_artifacts():
        if not path.exists():
            return None
        return path.read_bytes()
    try:
        return _artifact_blob(path).download_as_bytes()
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ not in {"NotFound", "Forbidden"}:
            log.warning("Could not read artifact object %s: %s", _artifact_storage_key(path), exc)
        return None


def _png_artifact_response(path: Path):
    if _using_gcs_artifacts():
        data = _download_artifact_bytes(path)
        if data is None:
            return _artifact_not_ready_response(path)
        response = send_file(
            BytesIO(data),
            mimetype="image/png",
            download_name=path.name,
            conditional=True,
        )
    else:
        response = send_file(path, mimetype="image/png", conditional=True)
    response.headers["Cache-Control"] = f"public, max-age={PNG_ARTIFACT_CACHE_SECONDS}"
    return response


def _prefer_artifact_forecast() -> bool:
    return os.environ.get("AUTOOUTLOOK_FORECAST_SOURCE", "").strip().lower() in {"artifact", "artifacts"}


def _artifact_forecast_bundle():
    index = _incremental_index()
    if not index:
        return None
    ready_hours = _ready_forecast_hours(index)
    if not ready_hours:
        return None
    started = datetime.now(timezone.utc)
    hours = []
    for forecast_hour in ready_hours:
        metadata = _read_json_path(_incremental_hour_path(forecast_hour) / "metadata.json")
        if not isinstance(metadata, dict):
            continue
        valid_time_iso = metadata.get("validTimeISO") or _valid_iso_from_cycle(index, forecast_hour)
        if not isinstance(valid_time_iso, str):
            continue
        category = _forecast_category_from_counts(metadata.get("categoryCounts") or {})
        probability_max = _forecast_probability_max(metadata)
        main_hazard = _main_hazard_from_probabilities(probability_max) or "wind"
        hour_dir = _incremental_hour_path(forecast_hour)
        polygons = _read_json_path(hour_dir / "risk_polygons.geojson")
        upper_air = _artifact_upper_air_overlay(hour_dir, forecast_hour, valid_time_iso, index)
        region = _artifact_region(metadata, polygons, category)
        ingredients = _artifact_metadata_ingredients(metadata, category, main_hazard, probability_max, forecast_hour)
        hours.append({
            "forecastHour": forecast_hour,
            "validTimeISO": valid_time_iso,
            "region": region,
            "ingredients": ingredients,
            "mlHazards": {
                "tornado": float(probability_max.get("tornado", 0) or 0),
                "hail": float(probability_max.get("hail", 0) or 0),
                "wind": float(probability_max.get("wind", 0) or 0),
            },
            "hazards": {},
            "outlook": {
                "category": _frontend_category(category),
                "mainHazard": main_hazard,
                "confidence": _artifact_confidence(category, probability_max),
                "significantSevere": _has_significant_probability(probability_max, category, main_hazard),
                "headline": _artifact_headline(category, main_hazard, valid_time_iso),
            },
            "riskPolygons": [],
            "cities": _cities_for_region(region, _frontend_category(category)),
            "upperAirLines": upper_air.get("upperAirLines", []),
            "upperAirVectors": upper_air.get("upperAirVectors", []),
            "upperAirOverlay": upper_air.get("metadata"),
        })
    if not hours:
        return None
    hours.sort(key=lambda item: int(item["forecastHour"]))
    anchor_hour = min(hours, key=lambda item: abs(int(item["forecastHour"]) - 12))
    cycle_time_iso = index.get("cycleTimeISO") or hours[0]["validTimeISO"]
    fetched_at_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "cycle": index.get("cycle") or "Generated HRRR",
        "issuedAtISO": cycle_time_iso,
        "providerNotes": (
            "Generated HRRR/XGBoost artifacts served from the published artifact bucket; "
            "on-demand HRRR bundle generation is disabled for web stability."
        ),
        "latencyMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        "region": anchor_hour["region"],
        "cities": CONUS_CITIES,
        "hours": hours,
        "source": "live",
        "providerId": "backend",
        "fetchedAtISO": fetched_at_iso,
        "mlHazardHours": len(hours),
        "mlModel": index.get("model"),
    }


def _ready_forecast_hours(index: dict) -> list[int]:
    hours = []
    for value in index.get("readyForecastHours", []):
        try:
            hours.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted({hour for hour in hours if 0 <= hour <= 48})


def _valid_iso_from_cycle(index: dict, forecast_hour: int) -> str | None:
    cycle_time = _parse_iso_utc(index.get("cycleTimeISO"))
    if cycle_time is None:
        return None
    return (cycle_time + timedelta(hours=int(forecast_hour))).isoformat().replace("+00:00", "Z")


def _forecast_probability_max(metadata: dict) -> dict[str, float]:
    stats = metadata.get("probabilityStats") if isinstance(metadata.get("probabilityStats"), dict) else {}
    values = (
        stats.get("categoryConsistencyProbabilityMax")
        or stats.get("cappedProbabilityMax")
        or stats.get("environmentalCappedProbabilityMax")
        or stats.get("rawProbabilityMax")
        or {}
    )
    if not isinstance(values, dict):
        values = {}
    return {
        hazard: float(values.get(hazard, 0) or 0)
        for hazard in ("tornado", "hail", "wind")
    }


def _forecast_category_from_counts(category_counts: dict) -> str:
    category = _max_category_from_counts(category_counts)
    return "TSTM" if category == "NONE" else category


def _frontend_category(category: str) -> str:
    return "MOD" if category == "MDT" else "TSTM" if category == "NONE" else category


def _artifact_headline(category: str, main_hazard: str, valid_time_iso: str) -> str:
    valid = _parse_iso_utc(valid_time_iso)
    time_label = f"{valid.hour:02d}Z" if valid is not None else "the selected hour"
    hazard_label = {
        "tornado": "tornado potential",
        "hail": "hail potential",
        "wind": "damaging wind potential",
    }.get(main_hazard, "severe potential")
    if _frontend_category(category) == "TSTM":
        return f"General thunder around {time_label}, with organized severe weather limited."
    return f"Severe storms possible around {time_label}, mainly {hazard_label}."


def _artifact_confidence(category: str, probabilities: dict[str, float]) -> float:
    category_floor = {
        "NONE": 0.45,
        "TSTM": 0.50,
        "MRGL": 0.58,
        "SLGT": 0.66,
        "ENH": 0.73,
        "MDT": 0.80,
        "MOD": 0.80,
        "HIGH": 0.88,
    }.get(category, 0.55)
    return min(0.95, category_floor + max(probabilities.values(), default=0.0) * 0.2)


def _artifact_ingredients(category: str, main_hazard: str, probabilities: dict[str, float], forecast_hour: int) -> dict:
    ord_by_category = {"NONE": 0, "TSTM": 1, "MRGL": 2, "SLGT": 3, "ENH": 4, "MDT": 5, "MOD": 5, "HIGH": 6}
    severity = ord_by_category.get(category, 1)
    peak_probability = max(probabilities.values(), default=0.0)
    srh_boost = 80 if main_hazard == "tornado" else 25
    shear_boost = 8 if main_hazard in {"hail", "wind"} else 0
    return {
        "mlcape": 450 + severity * 420,
        "mucape": 650 + severity * 470,
        "sbcape": 350 + severity * 390,
        "cin": -160 + min(severity, 5) * 25,
        "sfcDewpointF": 58 + severity * 2.5,
        "pwatIn": 0.9 + severity * 0.12,
        "lclM": max(700, 1500 - severity * 120),
        "moistureDepthM": 1700 + severity * 360,
        "srh01": 50 + severity * 24 + srh_boost,
        "srh03": 100 + severity * 42 + srh_boost,
        "shear06Kt": 24 + severity * 4 + shear_boost,
        "stormRelWindKt": 18 + severity * 3 + shear_boost,
        "frontSignal": "strong" if severity >= 4 else "moderate" if severity >= 2 else "weak",
        "initiationConf": min(0.92, 0.28 + severity * 0.08 + peak_probability),
        "stormMode": "discrete" if main_hazard in {"tornado", "hail"} and severity >= 3 else "linear" if main_hazard == "wind" else "mixed",
        "capStrength": "weak" if severity >= 3 else "moderate",
        "stp": max(0.0, probabilities.get("tornado", 0.0) * 12 + severity * 0.15),
        "scp": max(0.0, peak_probability * 14 + severity * 0.3),
        "ehi": max(0.0, probabilities.get("tornado", 0.0) * 6 + severity * 0.1),
        "ship": max(0.0, probabilities.get("hail", 0.0) * 8 + severity * 0.12),
        "tornadoComposite": max(0.0, probabilities.get("tornado", 0.0) * 10 + forecast_hour * 0.002),
    }


def _artifact_region(metadata: dict, polygons, category: str) -> dict:
    region = metadata.get("region") if isinstance(metadata, dict) else None
    if isinstance(region, dict) and _valid_region(region):
        return {
            "label": str(region.get("label") or "Highlighted corridor"),
            "centerLat": float(region.get("centerLat")),
            "centerLon": float(region.get("centerLon")),
            "bbox": region.get("bbox") if isinstance(region.get("bbox"), list) and len(region.get("bbox")) == 4 else [
                float(region.get("centerLon")) - 5,
                float(region.get("centerLat")) - 3,
                float(region.get("centerLon")) + 5,
                float(region.get("centerLat")) + 3,
            ],
            "states": region.get("states") if isinstance(region.get("states"), list) else [],
        }
    return _region_from_feature_collection(polygons, category)


def _artifact_metadata_ingredients(
    metadata: dict,
    category: str,
    main_hazard: str,
    probabilities: dict[str, float],
    forecast_hour: int,
) -> dict:
    ingredients = metadata.get("ingredients") if isinstance(metadata, dict) else None
    if isinstance(ingredients, dict) and _valid_artifact_ingredients(ingredients):
        return _normalize_artifact_ingredients(ingredients)
    return _artifact_ingredients(category, main_hazard, probabilities, forecast_hour)


def _normalize_artifact_ingredients(ingredients: dict) -> dict:
    normalized = dict(ingredients)
    cin = _ingredient_float(normalized, "cin", 0.0)
    mlcape = _ingredient_float(normalized, "mlcape", 0.0)
    td_f = _ingredient_float(normalized, "sfcDewpointF", 50.0)
    shear_kt = _ingredient_float(normalized, "shear06Kt", 0.0)
    srh03 = _ingredient_float(normalized, "srh03", 0.0)
    front = str(normalized.get("frontSignal") or "none")
    if front not in {"none", "weak", "moderate", "strong"}:
        front = "none"
    normalized["frontSignal"] = front
    normalized["capStrength"] = _classify_cap(cin)
    normalized["initiationConf"] = _initiation_confidence(front, cin, mlcape, td_f)
    normalized["stormMode"] = _classify_storm_mode(shear_kt, srh03, front)
    return normalized


def _ingredient_float(ingredients: dict, key: str, default: float) -> float:
    try:
        value = float(ingredients.get(key))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _valid_region(region: dict) -> bool:
    try:
        center_lat = float(region.get("centerLat"))
        center_lon = float(region.get("centerLon"))
    except (TypeError, ValueError):
        return False
    return -90.0 <= center_lat <= 90.0 and -180.0 <= center_lon <= 180.0


def _valid_artifact_ingredients(ingredients: dict) -> bool:
    required = ("mlcape", "mucape", "sbcape", "cin", "sfcDewpointF", "pwatIn", "srh01", "srh03", "shear06Kt")
    for key in required:
        try:
            value = float(ingredients.get(key))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
    return True


def _region_from_feature_collection(payload, category: str) -> dict:
    points = []
    if isinstance(payload, dict):
        features = payload.get("features")
        if isinstance(features, list):
            target = _frontend_category(category)
            category_features = [
                feature for feature in features
                if isinstance(feature, dict)
                and _frontend_category(str((feature.get("properties") or {}).get("category", ""))) == target
            ]
            selected = category_features or [feature for feature in features if isinstance(feature, dict)]
            for feature in selected:
                points.extend(_geojson_positions((feature.get("geometry") or {}).get("coordinates")))
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


def _geojson_positions(value) -> list[tuple[float, float]]:
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


def _cities_for_region(region: dict, category: str) -> list[dict]:
    center_lat = float(region.get("centerLat", 37.0) or 37.0)
    center_lon = float(region.get("centerLon", -97.0) or -97.0)
    ramp = ["TSTM", "MRGL", "SLGT", "ENH", "MOD", "HIGH"]
    peak = max(0, ramp.index(category) if category in ramp else 0)
    cities = []
    for city in CONUS_CITIES:
        dist = ((float(city["lat"]) - center_lat) ** 2 + ((float(city["lon"]) - center_lon) * 0.8) ** 2) ** 0.5
        ord_value = peak
        if dist > 3.0:
            ord_value = max(0, ord_value - 1)
        if dist > 6.0:
            ord_value = max(0, ord_value - 1)
        cities.append({**city, "risk": ramp[ord_value]})
    return cities


def _empty_upper_air_overlay(forecast_hour: int, valid_time_iso: str, index: dict) -> dict:
    return {
        "domain": "CONUS",
        "level": "500mb",
        "fields": [],
        "gridStride": 0,
        "windBarbStride": 0,
        "source": "generated_artifact_forecast_bundle",
        "hasHeightContours": False,
        "hasWindVectors": False,
        "windVectorCount": 0,
        "heightContourCount": 0,
        "sourceCycle": index.get("cycle"),
        "forecastHour": int(forecast_hour),
        "validTimeISO": valid_time_iso,
        "error": "500 mb overlay is not generated by the artifact forecast bundle.",
    }


def _artifact_upper_air_overlay(hour_dir: Path, forecast_hour: int, valid_time_iso: str, index: dict) -> dict:
    overlay = _read_json_path(hour_dir / "upper_air_overlay.json")
    if not isinstance(overlay, dict):
        return {
            "upperAirLines": [],
            "upperAirVectors": [],
            "metadata": _empty_upper_air_overlay(forecast_hour, valid_time_iso, index),
        }
    metadata = overlay.get("metadata")
    if not isinstance(metadata, dict):
        metadata = _empty_upper_air_overlay(forecast_hour, valid_time_iso, index)
    return {
        "upperAirLines": overlay.get("upperAirLines", []) if isinstance(overlay.get("upperAirLines"), list) else [],
        "upperAirVectors": overlay.get("upperAirVectors", []) if isinstance(overlay.get("upperAirVectors"), list) else [],
        "metadata": metadata,
    }


def _incremental_hour_json(forecast_hour: int, artifact_name: str):
    hour = int(forecast_hour)
    index = _incremental_index()
    if index is not None:
        ready_hours = {
            int(item)
            for item in index.get("readyForecastHours", [])
            if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
        }
        if hour not in ready_hours:
            return _json_error({
                "error": f"incremental outlook hour F{hour:02d} is not ready for the selected HRRR cycle",
                "code": "incremental_hour_pending",
                "cycle": index.get("cycle"),
                "cycleTimeISO": index.get("cycleTimeISO"),
                "readyForecastHours": sorted(ready_hours),
                "pendingForecastHours": index.get("pendingForecastHours", []),
            }, 404)
    return _json_path(_incremental_hour_path(hour) / artifact_name)


def _max_category_from_counts(category_counts: dict) -> str:
    labels = ["NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
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
    for label in labels:
        count = int(category_counts.get(label, 0) or 0)
        if count >= minimum_cells[label]:
            best = label
    return best


def _main_hazard_from_probabilities(probabilities: dict) -> str | None:
    hazards = ["tornado", "hail", "wind"]
    values = [(hazard, float(probabilities.get(hazard, 0) or 0)) for hazard in hazards]
    hazard, probability = max(values, key=lambda item: item[1])
    return hazard if probability > 0 else None


def _cap_probabilities_for_category(probabilities: dict, category: str) -> dict[str, float]:
    ceilings = {
        "NONE": 0.0,
        "TSTM": 0.09,
        "MRGL": 0.14,
        "SLGT": 0.29,
        "ENH": 0.44,
        "MDT": 0.59,
        "HIGH": 1.0,
    }
    ceiling = ceilings.get(category, 0.0)
    return {
        hazard: min(float(probabilities.get(hazard, 0) or 0), ceiling)
        for hazard in ["tornado", "hail", "wind"]
    }


def _max_hazard_probability(probabilities: dict) -> float:
    return max((float(probabilities.get(hazard, 0) or 0) for hazard in ["tornado", "hail", "wind"]), default=0.0)


def _has_significant_probability(probabilities: dict, category: str, main_hazard: str | None) -> bool:
    if category not in {"SLGT", "ENH", "MDT", "HIGH"} or main_hazard not in {"tornado", "hail", "wind"}:
        return False
    thresholds = {
        "tornado": 0.10,
        "hail": 0.30,
        "wind": 0.30,
    }
    return float(probabilities.get(main_hazard, 0) or 0) >= thresholds[main_hazard]


def _json_artifact(name: str):
    return _json_path(_artifact_path(name))


def _json_artifact_or_incremental(name: str):
    if name == "metadata.json":
        incremental_path = _selected_incremental_artifact_dir() / "index.json"
        if _artifact_exists(incremental_path):
            return _json_path(incremental_path)
    if name in {"risk_polygons.geojson", "aggregate_risk_polygons.geojson"}:
        payload = _merged_incremental_risk_polygons()
        if payload is not None:
            return _json_response(payload)
    path = _artifact_path(name)
    if _artifact_exists(path):
        return _json_path(path)
    if name == "probability_tiles.json":
        payload = _incremental_probability_tiles()
        if payload is not None:
            return _json_response(payload)
    return _json_path(path)


def _read_json_path(path: Path) -> dict | list | None:
    try:
        data = _download_artifact_bytes(path)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.info("Could not read JSON artifact %s: %s", path, exc)
        return None


def _incremental_index() -> dict | None:
    index = _read_json_path(_selected_incremental_artifact_dir() / "index.json")
    return index if isinstance(index, dict) else None


def _selected_incremental_artifact_dir() -> Path:
    current = _read_incremental_index_from_dir(INCREMENTAL_ARTIFACT_DIR)
    if _incremental_index_has_full_coverage(current):
        return INCREMENTAL_ARTIFACT_DIR
    complete_dir = _incremental_complete_artifact_dir()
    fallback = _read_incremental_index_from_dir(complete_dir)
    if _incremental_index_has_full_coverage(fallback):
        return complete_dir
    return INCREMENTAL_ARTIFACT_DIR


def _incremental_complete_artifact_dir() -> Path:
    return INCREMENTAL_COMPLETE_ARTIFACT_DIR or INCREMENTAL_ARTIFACT_DIR.with_name(f"{INCREMENTAL_ARTIFACT_DIR.name}_complete")


def _read_incremental_index_from_dir(artifact_dir: Path) -> dict | None:
    index_path = artifact_dir / "index.json"
    if not _artifact_exists(index_path):
        return None
    index = _read_json_path(index_path)
    return index if isinstance(index, dict) else None


def _incremental_index_has_full_coverage(index: dict | None) -> bool:
    if not index or index.get("status") != "complete":
        return False
    model = index.get("model")
    if isinstance(model, dict) and model.get("active") is False:
        return False
    ready = set(_coerce_forecast_hours(index.get("readyForecastHours")))
    return FULL_INCREMENTAL_FORECAST_HOURS.issubset(ready)


def _coerce_forecast_hours(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    hours: list[int] = []
    for item in value:
        try:
            hours.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted({hour for hour in hours if 0 <= hour <= 48})


def _merged_incremental_risk_polygons() -> dict | None:
    index = _incremental_index()
    if not index:
        return None
    artifact_dir = _selected_incremental_artifact_dir()
    features: list[dict] = []
    for forecast_hour in index.get("readyForecastHours", []):
        payload = _read_json_path(_incremental_hour_path(int(forecast_hour), artifact_dir) / "risk_polygons.geojson")
        if isinstance(payload, dict) and isinstance(payload.get("features"), list):
            features.extend(payload["features"])
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "incremental_artifacts",
            "cycle": index.get("cycle"),
            "cycleTimeISO": index.get("cycleTimeISO"),
            "generatedAtISO": index.get("generatedAtISO"),
        },
    }


def _incremental_probability_tiles() -> dict | None:
    index = _incremental_index()
    if not index:
        return None
    artifact_dir = _selected_incremental_artifact_dir()
    hours = []
    for forecast_hour in index.get("readyForecastHours", []):
        hour_dir = _incremental_hour_path(int(forecast_hour), artifact_dir)
        tile = _read_json_path(hour_dir / "probability_tile.json")
        if not isinstance(tile, dict):
            continue
        metadata = _read_json_path(hour_dir / "metadata.json")
        if not isinstance(metadata, dict):
            metadata = {}
        hours.append({
            "forecastHour": int(tile.get("forecastHour", forecast_hour)),
            "validTimeISO": tile.get("validTimeISO") or metadata.get("validTimeISO"),
            "categoryCounts": metadata.get("categoryCounts"),
            "tile": tile,
        })
    return {
        "cycle": index.get("cycle"),
        "featureSchemaHash": index.get("featureSchemaHash"),
        "riskLabels": index.get("riskLabels"),
        "gridStride": index.get("gridStride"),
        "tileStride": index.get("tileStride"),
        "environmentalCapsApplied": True,
        "categoryConsistencyCapsApplied": True,
        "hours": sorted(hours, key=lambda item: item["forecastHour"]),
    }


def _artifact_status_context() -> dict:
    context: dict[str, object] = {}
    for artifact_dir in [INCREMENTAL_ARTIFACT_DIR, _incremental_complete_artifact_dir()]:
        index = _read_incremental_index_from_dir(artifact_dir)
        if not isinstance(index, dict):
            continue
        for key in (
            "cycle",
            "cycleTimeISO",
            "generatedAtISO",
            "status",
            "readyForecastHours",
            "pendingForecastHours",
            "failedForecastHours",
        ):
            if key in index:
                context[key] = index.get(key)
        break
    return context


def _artifact_not_ready_response(path: Path, status_code: int = 404):
    payload = {
        "error": "outlook not ready",
        "code": "outlook_not_ready",
        "artifact": path.name,
    }
    payload.update(_artifact_status_context())
    return _json_error(payload, status_code)


def _json_path(path: Path):
    if not _artifact_exists(path):
        return _artifact_not_ready_response(path)
    payload = _read_json_path(path)
    if payload is None:
        return _json_error({
            "error": f"outlook artifact unreadable: {path.name}",
            "code": "artifact_unreadable",
            "artifact": path.name,
        }, 500)
    return _json_response(payload)


@app.get("/<path:path>")
def frontend_static_or_spa(path: str):
    if path.startswith("api/") or any(part.startswith(".") for part in Path(path).parts):
        abort(404)
    static_path = STATIC_DIR / path
    if static_path.is_file():
        return _static_file(path)
    return _static_file("index.html")


def _static_file(path: str):
    if not STATIC_DIR.exists():
        return _json_error({
            "error": "frontend build missing",
            "code": "frontend_build_missing",
        }, 404)
    response = send_from_directory(STATIC_DIR, path)
    if path.startswith("assets/"):
        response.headers["Cache-Control"] = f"public, max-age={STATIC_ASSET_CACHE_SECONDS}, immutable"
    elif path == "index.html":
        response.headers["Cache-Control"] = "no-cache"
    return response


if __name__ == "__main__":
    host = os.environ.get("AUTOOUTLOOK_HOST", "127.0.0.1")
    port = int(os.environ.get("AUTOOUTLOOK_PORT", "8765"))
    dev = os.environ.get("FLASK_DEBUG", "0") == "1"
    log.info("AutoOutlook backend listening on http://%s:%d (reload=%s)", host, port, dev)
    app.run(host=host, port=port, debug=dev, use_reloader=dev)
