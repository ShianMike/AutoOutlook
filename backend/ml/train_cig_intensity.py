"""Train XGBoost classifiers for SPC CIG/intensity target labels."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.ml.add_intensity_labels import DEFAULT_OUTPUT as DEFAULT_INPUT
from backend.ml.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, feature_schema_hash
from backend.ml.reports import INTENSITY_LABEL_KEYS
from backend.ml.train_xgboost import _group_shuffle_split, _metric_block, _require_training_deps, _time_based_split

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[1] / "models" / "cig_intensity"

CIG_TARGETS = {
    "tornado_ef2_plus": {"hazard": "tornado", "cig": 1, "threshold": "EF2+"},
    "tornado_ef3_plus": {"hazard": "tornado", "cig": 2, "threshold": "EF3+"},
    "hail_2in_plus": {"hazard": "hail", "cig": 1, "threshold": "2.00in+"},
    "hail_3_5in_plus": {"hazard": "hail", "cig": 2, "threshold": "3.50in+"},
    "wind_56kt_plus": {"hazard": "wind", "cig": 1, "threshold": "56kt+"},
    "wind_65kt_plus": {"hazard": "wind", "cig": 1, "threshold": "65kt+"},
    "wind_74kt_plus": {"hazard": "wind", "cig": 2, "threshold": "74kt+"},
    "wind_83kt_plus": {"hazard": "wind", "cig": 3, "threshold": "83kt+"},
}


def _dataset_quality(frame: Any) -> dict[str, Any]:
    return {
        "trainingRows": int(len(frame)),
        "uniqueRunDates": int(frame["runDate"].nunique()) if "runDate" in frame.columns else None,
        "positiveCounts": {
            key: int(frame[f"label_{key}"].astype(int).sum())
            for key in INTENSITY_LABEL_KEYS
            if f"label_{key}" in frame.columns
        },
    }


def train(
    input_path: Path,
    models_dir: Path,
    split_strategy: str,
    test_size: float,
    test_start_date: str,
    random_state: int,
    n_estimators: int,
) -> dict[str, Any]:
    joblib, pd, CalibratedClassifierCV, train_test_split, _, StratifiedKFold, XGBClassifier, metric_fns = _require_training_deps()
    frame = pd.read_parquet(input_path)

    missing_features = [name for name in FEATURE_NAMES if name not in frame.columns]
    if missing_features:
        raise SystemExit(f"CIG training dataset missing feature columns: {missing_features}")
    missing_labels = [f"label_{key}" for key in INTENSITY_LABEL_KEYS if f"label_{key}" not in frame.columns]
    if missing_labels:
        raise SystemExit(f"CIG training dataset missing intensity label columns: {missing_labels}")

    models_dir.mkdir(parents=True, exist_ok=True)
    x = frame.loc[:, FEATURE_NAMES].astype(float)

    metrics: dict[str, Any] = {}
    for target in INTENSITY_LABEL_KEYS:
        label_col = f"label_{target}"
        y = frame[label_col].astype(int)
        if split_strategy == "time":
            train_idx, test_idx, split = _time_based_split(frame, test_start_date)
        elif split_strategy == "group-shuffle":
            train_idx, test_idx, split = _group_shuffle_split(frame, x, y, frame["runDate"], test_size, random_state)
        else:
            train_idx, test_idx = train_test_split(
                frame.index,
                test_size=test_size,
                random_state=random_state,
                stratify=y,
            )
            split = {
                "strategy": "stratified",
                "testSize": test_size,
                "trainRows": int(len(train_idx)),
                "testRows": int(len(test_idx)),
            }
        x_train, x_test = x.loc[train_idx], x.loc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
        positives = int(y.sum())
        negatives = int(len(y) - positives)
        train_positives = int(y_train.sum())
        train_negatives = int(len(y_train) - train_positives)
        if train_positives < 2 or train_negatives < 2 or int(y_test.sum()) < 1:
            metrics[target] = {
                "skipped": True,
                "reason": "not enough train/test positives for a held-out binary classifier",
                "samples": int(len(y)),
                "positives": positives,
                "negatives": negatives,
                "trainPositives": train_positives,
                "testPositives": int(y_test.sum()),
            }
            continue

        scale_pos_weight = max(1.0, float(train_negatives / max(1, train_positives)))
        base = XGBClassifier(
            n_estimators=n_estimators,
            max_depth=3,
            learning_rate=0.04,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            random_state=random_state,
            scale_pos_weight=scale_pos_weight,
        )
        cv_folds = max(2, min(3, train_positives, train_negatives))
        model = CalibratedClassifierCV(
            estimator=base,
            method="isotonic",
            cv=StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=random_state),
        )
        model.fit(x_train, y_train)
        y_prob = model.predict_proba(x_test)[:, 1]
        metrics[target] = {
            "skipped": False,
            "samples": int(len(y)),
            "positives": positives,
            "negatives": negatives,
            "trainSamples": int(len(y_train)),
            "testSamples": int(len(y_test)),
            "trainPositives": train_positives,
            "testPositives": int(y_test.sum()),
            "calibrationCv": cv_folds,
            "target": CIG_TARGETS[target],
            "split": split,
            **_metric_block(y_test, y_prob, metric_fns),
        }
        joblib.dump(model, models_dir / f"{target}_xgb.joblib")

    trained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata = {
        "version": f"xgb-cig-intensity-{trained_at.replace(':', '').replace('-', '')}",
        "artifactType": "xgboost_joblib",
        "trainedAtISO": trained_at,
        "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
        "featureSchemaHash": feature_schema_hash(),
        "featureNames": list(FEATURE_NAMES),
        "targets": CIG_TARGETS,
        "trainingRows": int(len(frame)),
        "trainingInput": str(input_path),
        "splitStrategy": split_strategy,
        "datasetQuality": _dataset_quality(frame),
        "metrics": metrics,
    }
    (models_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--split-strategy", choices=("time", "group-shuffle", "stratified"), default="time")
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--test-start-date", default="20250101")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--n-estimators", type=int, default=180)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = train(
        args.input,
        args.models_dir,
        args.split_strategy,
        args.test_size,
        args.test_start_date,
        args.random_state,
        args.n_estimators,
    )
    print(json.dumps({
        "modelsDir": str(args.models_dir),
        "version": metadata["version"],
        "featureSchemaHash": metadata["featureSchemaHash"],
        "metrics": metadata["metrics"],
    }, indent=2))


if __name__ == "__main__":
    main()
