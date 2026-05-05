"""Flask entrypoint for AutoOutlook's local data service.

GET /api/forecast  -> returns a backend-bundle JSON for the latest HRRR cycle.
GET /api/health    -> tiny liveness probe.

Run with:  python -m backend.server   (from the project root)
       or: python backend/server.py   (also works; sys.path is patched)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import json
from datetime import datetime, timezone
from pathlib import Path

# Allow `python backend/server.py` to import sibling modules when invoked
# as a script rather than `python -m backend.server`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import abort, jsonify, send_file, send_from_directory, Flask  # noqa: E402
from flask_cors import CORS  # noqa: E402

from backend.cache import TTLCache  # noqa: E402
from backend.bundle_builder import build_bundle  # noqa: E402
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
ARTIFACT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_ARTIFACT_DIR",
    Path(__file__).resolve().parent / "artifacts" / "latest",
))
INCREMENTAL_ARTIFACT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_INCREMENTAL_ARTIFACT_DIR",
    Path(__file__).resolve().parent / "artifacts" / "latest_incremental",
))


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
    index_path = INCREMENTAL_ARTIFACT_DIR / "index.json"
    if not index_path.exists():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.info("Could not read incremental artifact index for forecast cycle sync: %s", exc)
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


@app.get("/")
def frontend_index():
    return _static_file("index.html")


@app.get("/api/forecast")
def forecast():
    now = datetime.now(timezone.utc)
    base_time = _forecast_base_time(now)
    key = _cycle_key(base_time)
    cached = cache.get(key)
    if cached is not None:
        log.info("cache HIT  %s", key)
        return jsonify(cached)

    lock = _singleflight_lock(key)
    with lock:
        cached = cache.get(key)
        if cached is not None:
            log.info("cache HIT after wait %s", key)
            return jsonify(cached)

        log.info("cache MISS %s -> fetching NOMADS HRRR GRIB", key)
        try:
            bundle = build_bundle(now=base_time)
        except NomadsFetchError as exc:
            log.warning("NOMADS fetch failed: %s", exc)
            return jsonify({"error": str(exc), "code": "nomads_fetch_failed"}), 503
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected backend failure")
            return jsonify({"error": str(exc), "code": "backend_error"}), 500

        cache.set(key, bundle)
        log.info("cache SET  %s (%d ms)", key, bundle.get("latencyMs", -1))
        return jsonify(bundle)


@app.get("/api/outlook/latest")
def latest_outlook_metadata():
    return _json_artifact("metadata.json")


@app.get("/api/outlook/risk-polygons")
def latest_outlook_risk_polygons():
    return _json_artifact("risk_polygons.geojson")


@app.get("/api/outlook/aggregate-risk-polygons")
def latest_outlook_aggregate_risk_polygons():
    return _json_artifact("aggregate_risk_polygons.geojson")


@app.get("/api/outlook/probability-tiles")
def latest_outlook_probability_tiles():
    return _json_artifact("probability_tiles.json")


@app.get("/api/outlook/verification")
def latest_outlook_verification():
    return _json_artifact("verification_summary.json")


@app.get("/api/outlook/incremental")
def incremental_outlook_index():
    return _json_path(INCREMENTAL_ARTIFACT_DIR / "index.json")


@app.get("/api/outlook/incremental/available-hours")
def incremental_outlook_available_hours():
    return _json_path(INCREMENTAL_ARTIFACT_DIR / "index.json")


@app.get("/api/outlook/incremental/summary")
def incremental_outlook_summary():
    index_path = INCREMENTAL_ARTIFACT_DIR / "index.json"
    if not index_path.exists():
        return _json_path(index_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    hours = []
    for forecast_hour in index.get("readyForecastHours", []):
        metadata_path = _incremental_hour_path(int(forecast_hour)) / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
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
    return jsonify({
        "cycle": index.get("cycle"),
        "cycleTimeISO": index.get("cycleTimeISO"),
        "generatedAtISO": index.get("generatedAtISO"),
        "hours": sorted(hours, key=lambda item: item["forecastHour"]),
    })


@app.get("/api/outlook/incremental/hour/<int:forecast_hour>/risk-polygons")
def incremental_outlook_hour_risk_polygons(forecast_hour: int):
    return _json_path(_incremental_hour_path(forecast_hour) / "risk_polygons.geojson")


@app.get("/api/outlook/incremental/hour/<int:forecast_hour>/probability-tile")
def incremental_outlook_hour_probability_tile(forecast_hour: int):
    return _json_path(_incremental_hour_path(forecast_hour) / "probability_tile.json")


@app.get("/api/outlook/incremental/hour/<int:forecast_hour>/metadata")
def incremental_outlook_hour_metadata(forecast_hour: int):
    return _json_path(_incremental_hour_path(forecast_hour) / "metadata.json")


@app.get("/api/outlook/preview.png")
def latest_outlook_preview():
    path = _artifact_path("preview.png")
    if not path.exists():
        return jsonify({"error": "outlook preview artifact missing", "code": "artifact_missing"}), 404
    return send_file(path, mimetype="image/png", conditional=True)


def _artifact_path(name: str) -> Path:
    return ARTIFACT_DIR / name


def _incremental_hour_path(forecast_hour: int) -> Path:
    return INCREMENTAL_ARTIFACT_DIR / "hours" / f"f{int(forecast_hour):02d}"


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


def _json_path(path: Path):
    if not path.exists():
        return jsonify({
            "error": f"outlook artifact missing: {path.name}",
            "code": "artifact_missing",
            "artifactDir": str(path.parent),
        }), 404
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


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
        return jsonify({
            "error": "frontend build missing",
            "code": "frontend_build_missing",
            "distDir": str(STATIC_DIR),
        }), 404
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    host = os.environ.get("AUTOOUTLOOK_HOST", "127.0.0.1")
    port = int(os.environ.get("AUTOOUTLOOK_PORT", "8765"))
    dev = os.environ.get("FLASK_DEBUG", "0") == "1"
    log.info("AutoOutlook backend listening on http://%s:%d (reload=%s)", host, port, dev)
    app.run(host=host, port=port, debug=dev, use_reloader=dev)
