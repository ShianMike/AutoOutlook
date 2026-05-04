"""Train calibrated XGBoost hazard classifiers from an archive Parquet dataset."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, HAZARD_KEYS, feature_schema_hash

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
MIN_RECOMMENDED_TRAINING_ROWS = 5000


def _dataset_quality(frame: Any) -> dict[str, Any]:
    label_columns = [f"label_{hazard}" for hazard in HAZARD_KEYS]
    duplicate_rows = int(frame.duplicated(subset=[*FEATURE_NAMES, *label_columns], keep="first").sum())
    positive_counts = {
        hazard: int(frame[f"label_{hazard}"].astype(int).sum())
        for hazard in HAZARD_KEYS
    }
    unique_run_dates = int(frame["runDate"].nunique()) if "runDate" in frame.columns else None
    training_rows = int(len(frame))
    experimental_only = training_rows < MIN_RECOMMENDED_TRAINING_ROWS
    return {
        "trainingRows": training_rows,
        "minimumRecommendedRows": MIN_RECOMMENDED_TRAINING_ROWS,
        "uniqueRunDates": unique_run_dates,
        "duplicateFeatureLabelRows": duplicate_rows,
        "positiveCounts": positive_counts,
        "experimentalOnly": experimental_only,
        "status": "experimental" if experimental_only else "candidate",
    }


def _require_training_deps() -> tuple[Any, Any, Any, Any, Any, Any, Any, Any]:
    try:
        import joblib
        import pandas as pd
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
        from sklearn.model_selection import GroupShuffleSplit, StratifiedKFold, train_test_split
        from xgboost import XGBClassifier
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Training dependencies are missing. Run `pip install -r backend/requirements.txt` "
            f"before training. Original error: {exc}"
        ) from exc
    metrics = {
        "average_precision_score": average_precision_score,
        "brier_score_loss": brier_score_loss,
        "log_loss": log_loss,
        "roc_auc_score": roc_auc_score,
    }
    return joblib, pd, CalibratedClassifierCV, train_test_split, GroupShuffleSplit, StratifiedKFold, XGBClassifier, metrics


def _metric_block(y_true: Any, y_prob: Any, metric_fns: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name, fn in metric_fns.items():
        try:
            out[name] = float(fn(y_true, y_prob))
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


def train(input_path: Path, models_dir: Path, test_size: float, random_state: int) -> dict[str, Any]:
    joblib, pd, CalibratedClassifierCV, train_test_split, GroupShuffleSplit, StratifiedKFold, XGBClassifier, metric_fns = _require_training_deps()
    frame = pd.read_parquet(input_path)

    missing_features = [name for name in FEATURE_NAMES if name not in frame.columns]
    if missing_features:
        raise SystemExit(f"Training dataset missing feature columns: {missing_features}")

    missing_labels = [f"label_{hazard}" for hazard in HAZARD_KEYS if f"label_{hazard}" not in frame.columns]
    if missing_labels:
        raise SystemExit(f"Training dataset missing label columns: {missing_labels}")

    models_dir.mkdir(parents=True, exist_ok=True)
    x = frame.loc[:, FEATURE_NAMES].astype(float)
    groups = frame["runDate"]
    metrics: dict[str, Any] = {}

    for hazard in HAZARD_KEYS:
        label_col = f"label_{hazard}"
        y = frame[label_col].astype(int)
        positives = int(y.sum())
        negatives = int(len(y) - positives)
        if positives < 2 or negatives < 2:
            raise SystemExit(
                f"Not enough positive/negative samples for {hazard}: positives={positives}, negatives={negatives}"
            )

        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)
        train_idx, test_idx = next(gss.split(x, y, groups=groups))

        x_train, x_test = x.iloc[train_idx], x.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        scale_pos_weight = max(1.0, float((len(y_train) - int(y_train.sum())) / max(1, int(y_train.sum()))))
        base = XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.04,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            random_state=random_state,
            scale_pos_weight=scale_pos_weight,
        )
        cv_folds = max(2, min(3, int(y_train.sum()), int(len(y_train) - y_train.sum())))
        stratified_cv = StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=random_state)
        model = CalibratedClassifierCV(estimator=base, method="isotonic", cv=stratified_cv)
        model.fit(x_train, y_train)
        y_prob = model.predict_proba(x_test)[:, 1]
        metrics[hazard] = {
            "samples": int(len(y)),
            "positives": positives,
            "negatives": negatives,
            "testSamples": int(len(y_test)),
            "calibrationCv": cv_folds,
            **_metric_block(y_test, y_prob, metric_fns),
        }
        joblib.dump(model, models_dir / f"{hazard}_xgb.joblib")

    trained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    quality = _dataset_quality(frame)
    metadata = {
        "version": f"xgb-hazards-{trained_at.replace(':', '').replace('-', '')}",
        "artifactType": "xgboost_joblib",
        "trainedAtISO": trained_at,
        "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
        "featureSchemaHash": feature_schema_hash(),
        "featureNames": list(FEATURE_NAMES),
        "hazards": list(HAZARD_KEYS),
        "trainingRows": int(len(frame)),
        "datasetQuality": quality,
        "metrics": metrics,
    }
    (models_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Parquet dataset from gather_archive.py")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = train(args.input, args.models_dir, args.test_size, args.random_state)
    print(json.dumps({
        "modelsDir": str(args.models_dir),
        "version": metadata["version"],
        "featureSchemaHash": metadata["featureSchemaHash"],
        "metrics": metadata["metrics"],
    }, indent=2))


if __name__ == "__main__":
    main()
