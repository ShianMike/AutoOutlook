"""Create local bootstrap ML hazard artifacts.

These artifacts intentionally use the same backend/models runtime path as the
archive-trained XGBoost models, but they are lightweight calibrated term models
that require no external ML dependencies. Use them to make AutoOutlook run in
ML mode immediately; replace them with train_xgboost.py artifacts after archive
training.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, HAZARD_KEYS, feature_schema_hash
from .inference import MODEL_DIR, TERM_MODEL_TYPE


def term(feature: str, weight: float, scale: float = 1.0, transform: str = "clip_scale", **extra: Any) -> dict[str, Any]:
    return {"feature": feature, "weight": weight, "scale": scale, "transform": transform, **extra}


BOOTSTRAP_MODELS: dict[str, dict[str, Any]] = {
    "tornado": {
        "type": TERM_MODEL_TYPE,
        "intercept": -5.15,
        "terms": [
            term("stp", 2.25, 4.0),
            term("srh01", 0.58, 250.0),
            term("srh03", 0.20, 400.0),
            term("lclM", 0.48, transform="inverse_range", min=650.0, max=1800.0),
            term("mlcape", 0.35, 2500.0),
            term("initiationConf", 0.46),
            term("frontSignalOrdinal", 0.16, 3.0),
            term("capStrengthOrdinal", -0.36, 3.0),
            term("stormModeDiscrete", 0.62, transform="identity"),
            term("stormModeMixed", -0.40, transform="identity"),
            term("stormModeLinear", -0.95, transform="identity"),
            term("forecastHour", -0.15, 48.0),
        ],
    },
    "hail": {
        "type": TERM_MODEL_TYPE,
        "intercept": -3.45,
        "terms": [
            term("mucape", 0.95, 3500.0),
            term("ship", 1.45, 2.5),
            term("shear06Kt", 0.88, 55.0),
            term("stormRelWindKt", 0.32, 45.0),
            term("initiationConf", 0.58),
            term("scp", 0.18, 6.0),
            term("capStrengthOrdinal", -0.28, 3.0),
            term("stormModeDiscrete", 0.22, transform="identity"),
            term("stormModeMixed", 0.08, transform="identity"),
            term("stormModeLinear", -0.18, transform="identity"),
            term("forecastHour", -0.08, 48.0),
        ],
    },
    "wind": {
        "type": TERM_MODEL_TYPE,
        "intercept": -3.35,
        "terms": [
            term("mlcape", 0.72, 3000.0),
            term("shear06Kt", 0.98, 55.0),
            term("stormRelWindKt", 0.46, 45.0),
            term("initiationConf", 0.70),
            term("frontSignalOrdinal", 0.40, 3.0),
            term("capStrengthOrdinal", -0.20, 3.0),
            term("stormModeLinear", 0.65, transform="identity"),
            term("stormModeMixed", 0.32, transform="identity"),
            term("stormModeDiscrete", -0.08, transform="identity"),
            term("forecastHour", -0.04, 48.0),
        ],
    },
}


def write_bootstrap_models(models_dir: Path = MODEL_DIR) -> dict[str, Any]:
    models_dir.mkdir(parents=True, exist_ok=True)
    for hazard in HAZARD_KEYS:
        (models_dir / f"{hazard}_model.json").write_text(
            json.dumps(BOOTSTRAP_MODELS[hazard], indent=2),
            encoding="utf-8",
        )

    trained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata = {
        "version": f"bootstrap-terms-{trained_at.replace(':', '').replace('-', '')}",
        "artifactType": TERM_MODEL_TYPE,
        "trainedAtISO": trained_at,
        "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
        "featureSchemaHash": feature_schema_hash(),
        "featureNames": list(FEATURE_NAMES),
        "hazards": list(HAZARD_KEYS),
        "trainingRows": 0,
        "note": "Local bootstrap term model. Replace with archive-trained XGBoost artifacts for production accuracy.",
    }
    (models_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=MODEL_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metadata = write_bootstrap_models(args.models_dir)
    print(json.dumps({
        "modelsDir": str(args.models_dir),
        "version": metadata["version"],
        "artifactType": metadata["artifactType"],
        "featureSchemaHash": metadata["featureSchemaHash"],
    }, indent=2))


if __name__ == "__main__":
    main()
