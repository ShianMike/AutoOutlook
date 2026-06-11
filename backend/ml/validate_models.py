"""Validate trained XGBoost hazard models on held-out archive rows."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from .features import FEATURE_NAMES, HAZARD_KEYS, ensure_feature_frame_columns, feature_schema_hash
from .inference import METADATA_FILE, MODEL_DIR

CATEGORY_THRESHOLDS = {
    "tornado": (
        (0.45, "HIGH"),
        (0.30, "MDT"),
        (0.10, "ENH"),
        (0.05, "SLGT"),
        (0.02, "MRGL"),
    ),
    "hail": (
        (0.60, "MDT"),
        (0.30, "ENH"),
        (0.15, "SLGT"),
        (0.05, "MRGL"),
    ),
    "wind": (
        (0.60, "MDT"),
        (0.30, "ENH"),
        (0.15, "SLGT"),
        (0.05, "MRGL"),
    ),
    "thunder": (
        (0.10, "TSTM"),
    ),
}
CATEGORY_ORDER = ("NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH")
RELIABILITY_BINS = (0.0, 0.02, 0.05, 0.10, 0.15, 0.30, 0.45, 0.60, 1.0)


def category_for_probability(hazard: str, probability: float) -> str:
    for threshold, category in CATEGORY_THRESHOLDS[hazard]:
        if probability >= threshold:
            return category
    return "NONE" if hazard == "thunder" else "TSTM"


def _clean_pairs(y_true: Sequence[Any], y_prob: Sequence[Any]) -> tuple[list[int], list[float]]:
    labels: list[int] = []
    probabilities: list[float] = []
    for label, probability in zip(y_true, y_prob, strict=True):
        try:
            p = float(probability)
            y = int(label)
        except (TypeError, ValueError):
            continue
        if y not in (0, 1) or not math.isfinite(p):
            continue
        labels.append(y)
        probabilities.append(max(0.0, min(1.0, p)))
    return labels, probabilities


def brier_score(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    if not labels:
        return None
    return sum((p - y) ** 2 for y, p in zip(labels, probabilities, strict=True)) / len(labels)


def log_loss_score(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    if not labels:
        return None
    eps = 1e-12
    total = 0.0
    for y, p in zip(labels, probabilities, strict=True):
        p = min(1.0 - eps, max(eps, p))
        total += y * math.log(p) + (1 - y) * math.log(1 - p)
    return -total / len(labels)


def roc_auc_score(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    ranked = sorted(zip(probabilities, labels, strict=True), key=lambda item: item[0])
    rank_sum = 0.0
    i = 0
    while i < len(ranked):
        j = i + 1
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        rank_sum += avg_rank * sum(label for _, label in ranked[i:j])
        i = j
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def average_precision_score(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    positives = sum(labels)
    if positives == 0:
        return None
    ordered = sorted(zip(probabilities, labels, strict=True), key=lambda item: item[0], reverse=True)
    hits = 0
    precision_sum = 0.0
    for idx, (_, label) in enumerate(ordered, start=1):
        if label:
            hits += 1
            precision_sum += hits / idx
    return precision_sum / positives


def reliability_bins(
    labels: Sequence[int],
    probabilities: Sequence[float],
    bins: Sequence[float] = RELIABILITY_BINS,
) -> list[dict[str, float | int]]:
    out: list[dict[str, float | int]] = []
    for lo, hi in zip(bins[:-1], bins[1:], strict=True):
        indexes = [
            idx for idx, probability in enumerate(probabilities)
            if probability >= lo and (probability < hi or hi == 1.0 and probability <= hi)
        ]
        if not indexes:
            continue
        predicted = sum(probabilities[idx] for idx in indexes) / len(indexes)
        observed = sum(labels[idx] for idx in indexes) / len(indexes)
        out.append({
            "minProbability": lo,
            "maxProbability": hi,
            "count": len(indexes),
            "meanPredicted": predicted,
            "observedFrequency": observed,
            "absoluteError": abs(predicted - observed),
        })
    return out


def categorical_counts(hazard: str, labels: Sequence[int], probabilities: Sequence[float]) -> dict[str, dict[str, int]]:
    counts = {
        category: {"samples": 0, "events": 0}
        for category in CATEGORY_ORDER
    }
    for label, probability in zip(labels, probabilities, strict=True):
        category = category_for_probability(hazard, probability)
        counts[category]["samples"] += 1
        counts[category]["events"] += int(label)
    return counts


def threshold_contingency(hazard: str, labels: Sequence[int], probabilities: Sequence[float]) -> dict[str, int | float]:
    mrgl_threshold = CATEGORY_THRESHOLDS[hazard][-1][0]
    hits = misses = false_alarms = correct_negatives = 0
    for label, probability in zip(labels, probabilities, strict=True):
        predicted = probability >= mrgl_threshold
        if label and predicted:
            hits += 1
        elif label:
            misses += 1
        elif predicted:
            false_alarms += 1
        else:
            correct_negatives += 1
    pod = hits / (hits + misses) if hits + misses else None
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else None
    return {
        "threshold": mrgl_threshold,
        "hits": hits,
        "misses": misses,
        "falseAlarms": false_alarms,
        "correctNegatives": correct_negatives,
        "probabilityOfDetection": pod,
        "falseAlarmRatio": far,
    }


def metrics_for_hazard(hazard: str, y_true: Sequence[Any], y_prob: Sequence[Any]) -> dict[str, Any]:
    labels, probabilities = _clean_pairs(y_true, y_prob)
    positives = sum(labels)
    samples = len(labels)
    prevalence = positives / samples if samples else 0.0
    brier = brier_score(labels, probabilities)
    climatology_brier = brier_score(labels, [prevalence] * samples) if samples else None
    average_precision = average_precision_score(labels, probabilities)
    ap_lift = average_precision / prevalence if average_precision is not None and prevalence > 0 else None
    brier_skill = (
        1.0 - (brier / climatology_brier)
        if brier is not None and climatology_brier not in (None, 0.0)
        else None
    )
    return {
        "samples": samples,
        "positives": positives,
        "prevalence": prevalence,
        "brier": brier,
        "climatologyBrier": climatology_brier,
        "brierSkillScore": brier_skill,
        "logLoss": log_loss_score(labels, probabilities),
        "rocAuc": roc_auc_score(labels, probabilities),
        "averagePrecision": average_precision,
        "averagePrecisionLift": ap_lift,
        "reliabilityBins": reliability_bins(labels, probabilities),
        "mrglPlusContingency": threshold_contingency(hazard, labels, probabilities),
        "categoricalCounts": categorical_counts(hazard, labels, probabilities),
    }


def _require_runtime_deps() -> tuple[Any, Any]:
    try:
        import joblib
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Validation requires pandas, pyarrow, and joblib. Run `pip install -r backend/requirements.txt`. "
            f"Original error: {exc}"
        ) from exc
    return joblib, pd


def _read_frame(path: Path) -> Any:
    _, pd = _require_runtime_deps()
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _load_models(models_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    joblib, _ = _require_runtime_deps()
    metadata_path = models_dir / METADATA_FILE
    if not metadata_path.exists():
        raise SystemExit(f"Model metadata missing: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("featureSchemaHash") != feature_schema_hash():
        raise SystemExit(
            f"Feature schema mismatch: model={metadata.get('featureSchemaHash')} runtime={feature_schema_hash()}"
        )
    if tuple(metadata.get("featureNames", ())) != FEATURE_NAMES:
        raise SystemExit("Feature names/order in metadata do not match runtime schema")
    models = {
        hazard: joblib.load(models_dir / f"{hazard}_xgb.joblib")
        for hazard in HAZARD_KEYS
    }
    return metadata, models


def _predict_probabilities(model: Any, x: Any) -> list[float]:
    proba = model.predict_proba(x)
    return [max(0.0, min(1.0, float(row[1] if len(row) > 1 else row[0]))) for row in proba]


def validate(input_path: Path, models_dir: Path) -> dict[str, Any]:
    frame = ensure_feature_frame_columns(_read_frame(input_path))
    missing_features = [name for name in FEATURE_NAMES if name not in frame.columns]
    missing_labels = [f"label_{hazard}" for hazard in HAZARD_KEYS if f"label_{hazard}" not in frame.columns]
    if missing_features or missing_labels:
        raise SystemExit(json.dumps({
            "missingFeatures": missing_features,
            "missingLabels": missing_labels,
        }, indent=2))

    metadata, models = _load_models(models_dir)
    x = frame.loc[:, FEATURE_NAMES].astype(float)
    hazards: dict[str, Any] = {}
    for hazard in HAZARD_KEYS:
        probabilities = _predict_probabilities(models[hazard], x)
        hazards[hazard] = metrics_for_hazard(
            hazard,
            frame[f"label_{hazard}"].astype(int).tolist(),
            probabilities,
        )
    return {
        "input": str(input_path),
        "modelsDir": str(models_dir),
        "modelVersion": metadata.get("version"),
        "featureSchemaHash": metadata.get("featureSchemaHash"),
        "hazards": hazards,
    }


def gate_failures(
    report: dict[str, Any],
    min_auc: float,
    min_ap_lift: float,
    min_brier_skill: float,
    min_samples: int,
) -> list[str]:
    failures: list[str] = []
    for hazard, metrics in report["hazards"].items():
        if metrics["samples"] < min_samples:
            failures.append(f"{hazard}: samples {metrics['samples']} < {min_samples}")
        if metrics["positives"] == 0:
            failures.append(f"{hazard}: no positive labels in validation slice")
        if metrics["rocAuc"] is None or metrics["rocAuc"] < min_auc:
            failures.append(f"{hazard}: rocAuc {metrics['rocAuc']} < {min_auc}")
        if metrics["averagePrecisionLift"] is None or metrics["averagePrecisionLift"] < min_ap_lift:
            failures.append(f"{hazard}: averagePrecisionLift {metrics['averagePrecisionLift']} < {min_ap_lift}")
        if metrics["brierSkillScore"] is None or metrics["brierSkillScore"] < min_brier_skill:
            failures.append(f"{hazard}: brierSkillScore {metrics['brierSkillScore']} < {min_brier_skill}")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Held-out Parquet or CSV rows from gather_archive.py")
    parser.add_argument("--models-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero when validation gates fail")
    parser.add_argument("--min-samples", type=int, default=1000)
    parser.add_argument("--min-auc", type=float, default=0.60)
    parser.add_argument("--min-ap-lift", type=float, default=1.25)
    parser.add_argument("--min-brier-skill", type=float, default=0.02)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate(args.input, args.models_dir)
    failures = gate_failures(
        report,
        min_auc=args.min_auc,
        min_ap_lift=args.min_ap_lift,
        min_brier_skill=args.min_brier_skill,
        min_samples=args.min_samples,
    )
    report["gateFailures"] = failures
    print(json.dumps(report, indent=2))
    if args.strict and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
