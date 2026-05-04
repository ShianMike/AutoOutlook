"""Shared feature schema for training and live ML inference."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

FEATURE_SCHEMA_VERSION = "ml-features-v2-pure-ai"
HAZARD_KEYS = ("tornado", "hail", "wind")

FEATURE_NAMES = (
    "forecastHour",
    "mlcape",
    "mucape",
    "sbcape",
    "cin",
    "sfcDewpointF",
    "pwatIn",
    "lclM",
    "moistureDepthM",
    "srh01",
    "srh03",
    "shear06Kt",
    "stormRelWindKt",
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def feature_vector(ingredients: Mapping[str, Any], forecast_hour: int | float) -> list[float]:
    """Return the exact ordered feature vector used by every model artifact."""
    return [
        _num(forecast_hour),
        _num(ingredients.get("mlcape")),
        _num(ingredients.get("mucape")),
        _num(ingredients.get("sbcape")),
        _num(ingredients.get("cin")),
        _num(ingredients.get("sfcDewpointF"), 50.0),
        _num(ingredients.get("pwatIn"), 0.8),
        _num(ingredients.get("lclM"), 1500.0),
        _num(ingredients.get("moistureDepthM"), 1500.0),
        _num(ingredients.get("srh01")),
        _num(ingredients.get("srh03")),
        _num(ingredients.get("shear06Kt")),
        _num(ingredients.get("stormRelWindKt")),
    ]


def feature_row(ingredients: Mapping[str, Any], forecast_hour: int | float) -> dict[str, float]:
    return dict(zip(FEATURE_NAMES, feature_vector(ingredients, forecast_hour), strict=True))


def feature_schema_hash() -> str:
    payload = {
        "version": FEATURE_SCHEMA_VERSION,
        "featureNames": FEATURE_NAMES,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
