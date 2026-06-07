"""Lazy XGBoost model loading and live hazard probability inference."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, HAZARD_KEYS, feature_schema_hash, feature_vector

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
METADATA_FILE = "metadata.json"
TERM_MODEL_TYPE = "calibrated_linear_terms_v1"
MIN_XGBOOST_TRAINING_ROWS = 5000

_cached_fingerprint: tuple[tuple[str, float], ...] | None = None
_cached_result: tuple[dict[str, Any], dict[str, Any] | None] | None = None


def _status_from_metadata(
    metadata: Mapping[str, Any],
    active: bool,
    reason: str | None = None,
    compatibility_mode: str | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "active": active,
        "version": metadata.get("version", "unknown"),
        "trainedAtISO": metadata.get("trainedAtISO"),
        "featureSchemaHash": metadata.get("featureSchemaHash"),
        "featureSchemaVersion": metadata.get("featureSchemaVersion"),
        "artifactType": metadata.get("artifactType", "xgboost_joblib"),
        "trainingRows": _training_rows(metadata),
        "allowBootstrapRuntime": bool(metadata.get("allowBootstrapRuntime", False)),
    }
    if isinstance(metadata.get("datasetQuality"), Mapping):
        status["datasetQuality"] = dict(metadata["datasetQuality"])
    if compatibility_mode:
        status["featureCompatibilityMode"] = compatibility_mode
        status["runtimeFeatureSchemaVersion"] = FEATURE_SCHEMA_VERSION
        status["runtimeFeatureSchemaHash"] = feature_schema_hash()
    if reason:
        status["reason"] = reason
    return status


def _inactive(
    reason: str,
    metadata: Mapping[str, Any] | None = None,
    compatibility_mode: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if metadata is None:
        return {"active": False, "reason": reason}, None
    return _status_from_metadata(metadata, active=False, reason=reason, compatibility_mode=compatibility_mode), None


class TermModel:
    """Small calibrated term model used for local bootstrap artifacts.

    This is not the final XGBoost model. It lets the runtime exercise the
    same mlHazards path until archive-trained joblib artifacts are available.
    """

    def __init__(self, artifact: Mapping[str, Any]) -> None:
        self.intercept = float(artifact.get("intercept", 0.0))
        self.terms = list(artifact.get("terms", []))

    def predict_probability(self, features: Mapping[str, float]) -> float:
        score = self.intercept
        for term in self.terms:
            score += float(term.get("weight", 0.0)) * _term_value(term, features)
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, score))))


def _term_value(term: Mapping[str, Any], features: Mapping[str, float]) -> float:
    raw = float(features.get(str(term.get("feature")), 0.0))
    transform = str(term.get("transform", "clip_scale"))
    if transform == "identity":
        value = raw
    elif transform == "inverse_range":
        lo = float(term.get("min", 0.0))
        hi = float(term.get("max", 1.0))
        value = 1.0 - ((raw - lo) / max(1e-6, hi - lo))
    else:
        scale = float(term.get("scale", 1.0))
        offset = float(term.get("offset", 0.0))
        value = (raw - offset) / max(1e-6, scale)
    lo_clip = float(term.get("clipMin", 0.0))
    hi_clip = float(term.get("clipMax", 1.0))
    return max(lo_clip, min(hi_clip, value))


def _artifact_fingerprint(metadata: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    paths = [MODEL_DIR / METADATA_FILE]
    artifact_type = metadata.get("artifactType", "xgboost_joblib")
    suffix = "_model.json" if artifact_type == TERM_MODEL_TYPE else "_xgb.joblib"
    paths.extend(MODEL_DIR / f"{hazard}{suffix}" for hazard in HAZARD_KEYS)
    return tuple((str(path), path.stat().st_mtime if path.exists() else -1.0) for path in paths)


def _training_rows(metadata: Mapping[str, Any]) -> int:
    try:
        return int(metadata.get("trainingRows", 0))
    except (TypeError, ValueError):
        return 0


def _resolve_model_feature_names(
    metadata: Mapping[str, Any],
    artifact_type: str,
) -> tuple[tuple[str, ...], str | None] | tuple[None, str]:
    expected_hash = feature_schema_hash()
    model_feature_names = tuple(str(name) for name in metadata.get("featureNames", ()))
    if metadata.get("featureSchemaHash") == expected_hash and model_feature_names == FEATURE_NAMES:
        return FEATURE_NAMES, None

    if artifact_type != "xgboost_joblib":
        if metadata.get("featureSchemaHash") != expected_hash:
            return None, f"feature schema mismatch: model={metadata.get('featureSchemaHash')} runtime={expected_hash}"
        return None, "feature name/order mismatch"

    if not model_feature_names:
        return None, "XGBoost metadata missing featureNames"

    missing_runtime_features = [name for name in model_feature_names if name not in FEATURE_NAMES]
    if missing_runtime_features:
        return None, f"XGBoost feature name mismatch: unavailable runtime features {missing_runtime_features}"

    return model_feature_names, "runtime_subset_features"


def _load_bundle() -> tuple[dict[str, Any], dict[str, Any] | None]:
    global _cached_fingerprint, _cached_result
    if not MODEL_DIR.exists():
        return _inactive(f"model directory missing: {MODEL_DIR}")

    metadata_path = MODEL_DIR / METADATA_FILE
    if not metadata_path.exists():
        return _inactive(f"model metadata missing: {metadata_path}")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _inactive(f"model metadata unreadable: {exc}")

    artifact_type = metadata.get("artifactType", "xgboost_joblib")
    model_feature_names, compatibility = _resolve_model_feature_names(metadata, str(artifact_type))
    if model_feature_names is None:
        return _inactive(str(compatibility), metadata)

    if artifact_type == TERM_MODEL_TYPE:
        if not metadata.get("allowBootstrapRuntime", False):
            return _inactive("bootstrap term model disabled until an archive-trained XGBoost artifact is available", metadata)
        fingerprint = _artifact_fingerprint(metadata)
        if _cached_fingerprint == fingerprint and _cached_result is not None:
            return _cached_result
        models: dict[str, Any] = {}
        for hazard in HAZARD_KEYS:
            path = MODEL_DIR / f"{hazard}_model.json"
            if not path.exists():
                return _inactive(f"model artifact missing: {path}", metadata)
            try:
                models[hazard] = TermModel(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                return _inactive(f"failed loading {path.name}: {exc}", metadata)
        status = _status_from_metadata(metadata, active=True, compatibility_mode=compatibility)
        _cached_fingerprint = fingerprint
        _cached_result = status, {"metadata": metadata, "models": models, "featureNames": model_feature_names}
        return _cached_result

    training_rows = _training_rows(metadata)
    if training_rows < MIN_XGBOOST_TRAINING_ROWS and not metadata.get("allowSmallTrainingSet", False):
        return _inactive(
            f"XGBoost trainingRows {training_rows} below required {MIN_XGBOOST_TRAINING_ROWS}; train a larger archive first",
            metadata,
        )
    quality = metadata.get("datasetQuality")
    if (
        isinstance(quality, Mapping)
        and bool(quality.get("experimentalOnly"))
        and not metadata.get("allowExperimentalRuntime", False)
    ):
        return _inactive(
            "XGBoost dataset marked experimental/demo-only; gather larger archive data before live activation",
            metadata,
        )

    fingerprint = _artifact_fingerprint(metadata)
    if _cached_fingerprint == fingerprint and _cached_result is not None:
        return _cached_result

    try:
        import joblib
    except Exception as exc:  # noqa: BLE001
        return _inactive(f"joblib unavailable for model loading: {exc}")

    models: dict[str, Any] = {}
    for hazard in HAZARD_KEYS:
        path = MODEL_DIR / f"{hazard}_xgb.joblib"
        if not path.exists():
            return _inactive(f"model artifact missing: {path}", metadata)
        try:
            models[hazard] = joblib.load(path)
        except Exception as exc:  # noqa: BLE001
            return _inactive(f"failed loading {path.name}: {exc}", metadata)

    status = _status_from_metadata(metadata, active=True, compatibility_mode=compatibility)
    _cached_fingerprint = fingerprint
    feature_indexes = tuple(FEATURE_NAMES.index(name) for name in model_feature_names)
    _cached_result = status, {
        "metadata": metadata,
        "models": models,
        "featureNames": model_feature_names,
        "featureIndexes": feature_indexes,
    }
    return _cached_result


def model_status() -> dict[str, Any]:
    status, _ = _load_bundle()
    return dict(status)


def reset_model_cache() -> None:
    global _cached_fingerprint, _cached_result
    _cached_fingerprint = None
    _cached_result = None


def predict_ml_hazards(
    ingredients: Mapping[str, Any],
    forecast_hour: int | float,
) -> dict[str, float] | None:
    """Return calibrated tornado/hail/wind probabilities, or None if inactive."""
    status, bundle = _load_bundle()
    if not status.get("active") or bundle is None:
        return None

    vector = feature_vector(ingredients, forecast_hour)
    features = dict(zip(FEATURE_NAMES, vector, strict=True))
    model_feature_names = tuple(bundle.get("featureNames", FEATURE_NAMES))
    model_vector = [features[name] for name in model_feature_names]
    x = np.asarray([model_vector], dtype=float)
    probabilities: dict[str, float] = {}
    for hazard in HAZARD_KEYS:
        model = bundle["models"][hazard]
        try:
            if hasattr(model, "predict_probability"):
                value = float(model.predict_probability(features))
            elif hasattr(model, "predict_proba"):
                proba = model.predict_proba(x)
                value = float(proba[0][1] if np.asarray(proba).shape[1] > 1 else proba[0][0])
            else:
                value = float(model.predict(x)[0])
        except Exception as exc:  # noqa: BLE001
            log.warning("ML hazard inference failed for %s: %s", hazard, exc)
            return None
        probabilities[hazard] = max(0.0, min(1.0, value))
    return probabilities


def predict_ml_hazard_matrix(feature_matrix: np.ndarray) -> dict[str, np.ndarray] | None:
    """Return vectorized tornado/hail/wind probabilities for gridded feature rows."""
    status, bundle = _load_bundle()
    if not status.get("active") or bundle is None:
        return None

    x = np.asarray(feature_matrix, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(FEATURE_NAMES):
        raise ValueError(f"feature_matrix must be shaped (n, {len(FEATURE_NAMES)})")
    feature_indexes = tuple(bundle.get("featureIndexes", range(len(FEATURE_NAMES))))
    model_x = x[:, feature_indexes]

    probabilities: dict[str, np.ndarray] = {}
    for hazard in HAZARD_KEYS:
        model = bundle["models"][hazard]
        try:
            if hasattr(model, "predict_probability"):
                values = np.asarray([
                    float(model.predict_probability(dict(zip(FEATURE_NAMES, row, strict=True))))
                    for row in x
                ], dtype=float)
            elif hasattr(model, "predict_proba"):
                proba = np.asarray(model.predict_proba(model_x), dtype=float)
                values = proba[:, 1] if proba.ndim == 2 and proba.shape[1] > 1 else proba.reshape(-1)
            else:
                values = np.asarray(model.predict(model_x), dtype=float).reshape(-1)
        except Exception as exc:  # noqa: BLE001
            log.warning("ML gridded hazard inference failed for %s: %s", hazard, exc)
            return None
        probabilities[hazard] = np.clip(values, 0.0, 1.0)
    return probabilities
