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

from flask import Flask, jsonify, send_file  # noqa: E402
from flask_cors import CORS  # noqa: E402

from backend.cache import TTLCache  # noqa: E402
from backend.bundle_builder import build_bundle  # noqa: E402
from backend.nomads_pipeline import NomadsFetchError  # noqa: E402

LOG_FMT = "[%(asctime)s] %(levelname)s %(name)s :: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("autooutlook")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

cache = TTLCache(ttl_seconds=600)  # 10 min
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()
ARTIFACT_DIR = Path(os.environ.get(
    "AUTOOUTLOOK_ARTIFACT_DIR",
    Path(__file__).resolve().parent / "artifacts" / "latest",
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


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "autooutlook-backend"})


@app.get("/api/forecast")
def forecast():
    now = datetime.now(timezone.utc)
    key = _cycle_key(now)
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
            bundle = build_bundle(now=now)
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


@app.get("/api/outlook/preview.png")
def latest_outlook_preview():
    path = _artifact_path("preview.png")
    if not path.exists():
        return jsonify({"error": "outlook preview artifact missing", "code": "artifact_missing"}), 404
    return send_file(path, mimetype="image/png", conditional=True)


def _artifact_path(name: str) -> Path:
    return ARTIFACT_DIR / name


def _json_artifact(name: str):
    path = _artifact_path(name)
    if not path.exists():
        return jsonify({
            "error": f"outlook artifact missing: {name}",
            "code": "artifact_missing",
            "artifactDir": str(ARTIFACT_DIR),
        }), 404
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    host = os.environ.get("AUTOOUTLOOK_HOST", "127.0.0.1")
    port = int(os.environ.get("AUTOOUTLOOK_PORT", "8765"))
    dev = os.environ.get("FLASK_DEBUG", "0") == "1"
    log.info("AutoOutlook backend listening on http://%s:%d (reload=%s)", host, port, dev)
    app.run(host=host, port=port, debug=dev, use_reloader=dev)
