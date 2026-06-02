"""Shared feature schema for training and live ML inference."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

FEATURE_SCHEMA_VERSION = "ml-features-v4-parcel-cape-cin"
HAZARD_KEYS = ("tornado", "hail", "wind")

FEATURE_NAMES = (
    "forecastHour",
    "mlcape",
    "mucape",
    "sbcape",
    "cape3km",
    "cape180",
    "cin",
    "cinSb",
    "cinMl",
    "cinMu",
    "cin180",
    "sfcDewpointF",
    "pwatIn",
    "lclM",
    "moistureDepthM",
    "srh01",
    "srh03",
    "shear06Kt",
    "stormRelWindKt",
    "stp",
    "scp",
    "ehi",
    "ship",
    "lapseRate700500CPerKm",
    "freezingLevelM",
    "surfacePressurePa",
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
        _num(ingredients.get("cape3km")),
        _num(ingredients.get("cape180")),
        _num(ingredients.get("cin")),
        _num(ingredients.get("cinSb")),
        _num(ingredients.get("cinMl")),
        _num(ingredients.get("cinMu")),
        _num(ingredients.get("cin180")),
        _num(ingredients.get("sfcDewpointF"), 50.0),
        _num(ingredients.get("pwatIn"), 0.8),
        _num(ingredients.get("lclM"), 1500.0),
        _num(ingredients.get("moistureDepthM"), 1500.0),
        _num(ingredients.get("srh01")),
        _num(ingredients.get("srh03")),
        _num(ingredients.get("shear06Kt")),
        _num(ingredients.get("stormRelWindKt")),
        _num(ingredients.get("stp")),
        _num(ingredients.get("scp")),
        _num(ingredients.get("ehi")),
        _num(ingredients.get("ship")),
        _num(ingredients.get("lapseRate700500CPerKm")),
        _num(ingredients.get("freezingLevelM")),
        _num(ingredients.get("surfacePressurePa")),
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
