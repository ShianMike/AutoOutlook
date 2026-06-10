"""Sample historical verification for old probability tables vs SPC CIG tables."""
from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from backend.ml.add_intensity_labels import DEFAULT_OUTPUT as DEFAULT_INPUT
from backend.ml.features import FEATURE_NAMES
from backend.ml.gridded_outlook import (
    GriddedFeatures,
    SPC_CIG_CATEGORY_FEATURE_FLAG,
    SPC_RISK_LABELS,
    category_counts,
    category_grid_from_probabilities,
)
from backend.ml.inference import predict_ml_hazard_matrix
from backend.ml.spc_verification import official_category_grid

ARCHIVE_DOWNLOAD_DIR = Path(__file__).resolve().parents[1] / "ml_data" / "archive_2020_2024_downloaded"
DEFAULT_SPC_DIR = ARCHIVE_DOWNLOAD_DIR / "spc_outlooks_all_days"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "ml_data" / "archive_training" / "cig_category_verification_sample.json"


def _require_deps() -> Any:
    try:
        import pandas as pd
        import shapefile
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Historical CIG verification requires pandas, pyarrow, and pyshp. "
            "Run `python -m pip install -r backend/requirements.txt` first. "
            f"Original error: {exc}"
        ) from exc
    return pd


@contextmanager
def _feature_flag(value: str) -> Iterator[None]:
    old = os.environ.get(SPC_CIG_CATEGORY_FEATURE_FLAG)
    os.environ[SPC_CIG_CATEGORY_FEATURE_FLAG] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(SPC_CIG_CATEGORY_FEATURE_FLAG, None)
        else:
            os.environ[SPC_CIG_CATEGORY_FEATURE_FLAG] = old


def _category_label_grid(grid: np.ndarray) -> list[str]:
    return [SPC_RISK_LABELS[int(value)] for value in np.asarray(grid, dtype=int).reshape(-1)]


def _label_counts(labels: list[str]) -> dict[str, int]:
    return {label: labels.count(label) for label in SPC_RISK_LABELS if labels.count(label)}


def _agreement(pred: np.ndarray, official: np.ndarray) -> dict[str, Any]:
    pred_arr = np.asarray(pred, dtype=int).reshape(-1)
    official_arr = np.asarray(official, dtype=int).reshape(-1)
    valid = (pred_arr > 0) | (official_arr > 0)
    total = int(np.sum(valid))
    same = int(np.sum((pred_arr == official_arr) & valid))
    return {
        "comparisonCells": total,
        "agreementCells": same,
        "agreementFraction": float(same / total) if total else None,
        "underforecastCells": int(np.sum(official_arr > pred_arr)),
        "overforecastCells": int(np.sum(pred_arr > official_arr)),
    }


def _frame_to_features(frame: Any) -> GriddedFeatures:
    raw = {
        name: frame[name].astype(float).to_numpy().reshape(-1, 1)
        for name in FEATURE_NAMES
    }
    matrix = np.column_stack([raw[name].reshape(-1) for name in FEATURE_NAMES]).astype(float)
    return GriddedFeatures(raw=raw, normalized={}, matrix=matrix, shape=(len(frame), 1))


def _predict_categories(frame: Any) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    features = _frame_to_features(frame)
    probabilities = predict_ml_hazard_matrix(features.matrix)
    if probabilities is None:
        raise SystemExit("Active hazard model unavailable; cannot run historical category verification")
    shaped_probabilities = {
        hazard: np.asarray(values, dtype=float).reshape(features.shape)
        for hazard, values in probabilities.items()
    }
    with _feature_flag(""):
        old_grid = category_grid_from_probabilities(shaped_probabilities, features)
    with _feature_flag("1"):
        new_grid = category_grid_from_probabilities(shaped_probabilities, features)
    return old_grid, new_grid, shaped_probabilities


def _spc_geojson_for_date(spc_dir: Path, run_date: str) -> Mapping[str, Any] | None:
    day_dir = spc_dir / str(run_date)
    if not day_dir.exists():
        return None
    zips = sorted(day_dir.glob("*-shp.zip"))
    if not zips:
        return None
    return _cat_geojson_from_shapefile_zip(zips[-1])


def _cat_geojson_from_shapefile_zip(path: Path) -> Mapping[str, Any]:
    import shapefile

    with zipfile.ZipFile(path) as zf:
        shp_name = next(name for name in zf.namelist() if name.endswith("_cat.shp"))
        stem = shp_name[:-4]
        reader = shapefile.Reader(
            shp=io.BytesIO(zf.read(f"{stem}.shp")),
            shx=io.BytesIO(zf.read(f"{stem}.shx")),
            dbf=io.BytesIO(zf.read(f"{stem}.dbf")),
        )
        features = []
        for shape_record in reader.iterShapeRecords():
            features.append({
                "type": "Feature",
                "geometry": shape_record.shape.__geo_interface__,
                "properties": dict(shape_record.record.as_dict()),
            })
    return {
        "type": "FeatureCollection",
        "features": features,
        "sourceZip": str(path),
    }


def verify_sample(
    input_path: Path,
    spc_dir: Path,
    output_path: Path,
    max_rows: int,
    max_dates: int,
    random_state: int,
    run_dates: list[str] | None = None,
) -> dict[str, Any]:
    pd = _require_deps()
    columns = [
        "runDate",
        "sampleLat",
        "sampleLon",
        *FEATURE_NAMES,
    ]
    frame = pd.read_parquet(input_path, columns=columns)
    selected_run_dates = sorted(str(value) for value in frame["runDate"].astype(str).unique())
    if run_dates:
        wanted = {str(value).replace("-", "") for value in run_dates}
        selected_run_dates = [value for value in selected_run_dates if value in wanted]
        missing_training_dates = sorted(wanted - set(selected_run_dates))
    else:
        missing_training_dates = []
        if max_dates > 0:
            selected_run_dates = selected_run_dates[-max_dates:]
    frame = frame[frame["runDate"].astype(str).isin(selected_run_dates)].copy()
    if max_rows > 0 and len(frame) > max_rows:
        frame = frame.sample(n=max_rows, random_state=random_state).sort_values(["runDate", "sampleLat", "sampleLon"])
    frame = frame.reset_index(drop=True)

    old_grid, new_grid, probabilities = _predict_categories(frame)
    lat_grid = frame["sampleLat"].astype(float).to_numpy().reshape(-1, 1)
    lon_grid = frame["sampleLon"].astype(float).to_numpy().reshape(-1, 1)
    official = np.zeros(old_grid.shape, dtype=np.int16)
    missing_spc_dates: list[str] = []
    used_spc_dates: list[str] = []
    spc_max_by_date: dict[str, str] = {}
    for run_date, group in frame.groupby(frame["runDate"].astype(str), sort=False):
        geojson = _spc_geojson_for_date(spc_dir, str(run_date))
        if geojson is None:
            missing_spc_dates.append(str(run_date))
            continue
        indices = group.index.to_numpy()
        official[indices, :] = official_category_grid(lat_grid[indices, :], lon_grid[indices, :], geojson)
        used_spc_dates.append(str(run_date))
        spc_max_by_date[str(run_date)] = _max_spc_category_label(geojson)

    old_labels = _category_label_grid(old_grid)
    new_labels = _category_label_grid(new_grid)
    official_labels = _category_label_grid(official)
    changed = np.asarray(old_grid, dtype=int).reshape(-1) != np.asarray(new_grid, dtype=int).reshape(-1)
    summary = {
        "createdAtISO": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "input": str(input_path),
        "spcDir": str(spc_dir),
        "rows": int(len(frame)),
        "runDateMin": str(frame["runDate"].min()) if len(frame) else None,
        "runDateMax": str(frame["runDate"].max()) if len(frame) else None,
        "usedSpcDates": len(set(used_spc_dates)),
        "missingSpcDates": sorted(set(missing_spc_dates)),
        "missingTrainingDates": missing_training_dates,
        "spcMaxCategoryByDate": spc_max_by_date,
        "oldCategoryCounts": _label_counts(old_labels),
        "newCigCategoryCounts": _label_counts(new_labels),
        "officialCategoryCounts": _label_counts(official_labels),
        "oldVsOfficial": _agreement(old_grid, official),
        "newCigVsOfficial": _agreement(new_grid, official),
        "changedByCigTableCells": int(np.sum(changed)),
        "changedByCigTableFraction": float(np.mean(changed)) if len(frame) else None,
        "probabilityMax": {
            hazard: float(np.nanmax(values)) if np.size(values) else 0.0
            for hazard, values in probabilities.items()
        },
        "notes": [
            "Sample audit uses active hazard models plus trained CIG intensity models when AUTOOUTLOOK_SPC_CIG_CATEGORIES is enabled.",
            "Archived SPC verification uses the latest categorical shapefile ZIP available for each sampled runDate.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _max_spc_category_label(spc_geojson: Mapping[str, Any]) -> str:
    best = 0
    for feature in spc_geojson.get("features", []):
        label = str(feature.get("properties", {}).get("LABEL") or "").upper().strip()
        if label == "MOD":
            label = "MDT"
        if label in SPC_RISK_LABELS:
            best = max(best, SPC_RISK_LABELS.index(label))
    return SPC_RISK_LABELS[best]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--spc-dir", type=Path, default=DEFAULT_SPC_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--max-dates", type=int, default=180)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--run-date", action="append", dest="run_dates", help="Specific YYYYMMDD or YYYY-MM-DD date. Repeatable.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = verify_sample(
        args.input,
        args.spc_dir,
        args.output,
        args.max_rows,
        args.max_dates,
        args.random_state,
        run_dates=args.run_dates,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
