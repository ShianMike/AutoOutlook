"""Ensure a usable runtime hazard model bundle is present for generation jobs."""
from __future__ import annotations

import subprocess
import sys

from .inference import model_status


def main() -> None:
    status = model_status()
    if status.get("active") and status.get("artifactType") == "xgboost_joblib":
        print(
            "Using XGBoost hazard model "
            f"{status.get('version')} rows={status.get('trainingRows')}"
        )
        return

    print(f"Bootstrapping fallback hazard models: {status}")
    subprocess.check_call([sys.executable, "-m", "backend.ml.bootstrap_models"])


if __name__ == "__main__":
    main()
