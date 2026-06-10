"""Merge downloaded HRRR archive parts into one audited training dataset."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES, HAZARD_KEYS

ARCHIVE_DOWNLOAD_DIR = Path(__file__).resolve().parents[1] / "ml_data" / "archive_2020_2024_downloaded"
ARCHIVE_TRAINING_DIR = Path(__file__).resolve().parents[1] / "ml_data" / "archive_training"
DEFAULT_OUTPUT = ARCHIVE_TRAINING_DIR / "autooutlook_hrrr_2020_202602_00z_f00_f48.parquet"
DEFAULT_SUMMARY = ARCHIVE_TRAINING_DIR / "autooutlook_hrrr_2020_202602_00z_f00_f48_summary.json"

DEFAULT_INPUT_NAMES = (
    "hrrr_features_part_2020_2022_preserved_until_20210614_z00_+06h.with_lightning.parquet",
    "hrrr_features_part_2020_2022_remaining_00z_f00_f48.with_lightning.parquet",
    "hrrr_features_part_2023_2024_preserved_until_20230128_z00_+45h.parquet",
    "hrrr_features_part_2023_2024_remaining_00z_f00_f48.with_lightning.parquet",
    "hrrr_features_part_2025_2026_through_feb_00z_f00_f48.with_lightning.parquet",
)

KEY_COLUMNS = ("runDate", "runCycle", "forecastHour", "sampleLat", "sampleLon")
LIGHTNING_COLUMNS = (
    "hrrrLtng",
    "hrrrLtngSd1m",
    "hrrrLtngSd2m",
    "hrrrLightningAvailable",
    "hrrrLightningSource",
    "hrrrLightningFieldCount",
    "hrrrLightningError",
)


def _require_deps() -> tuple[Any, Any]:
    try:
        import numpy as np
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Archive merge requires pandas, numpy, and pyarrow. "
            "Run `pip install -r backend/requirements.txt` first. "
            f"Original error: {exc}"
        ) from exc
    return pd, np


def _default_inputs() -> list[Path]:
    return [ARCHIVE_DOWNLOAD_DIR / name for name in DEFAULT_INPUT_NAMES]


def _input_summary(frame: Any, path: Path) -> dict[str, Any]:
    label_counts = {
        hazard: int(frame.get(f"label_{hazard}", 0).astype(int).sum())
        for hazard in HAZARD_KEYS
        if f"label_{hazard}" in frame.columns
    }
    lightning_available = (
        int(frame["hrrrLightningAvailable"].fillna(False).astype(bool).sum())
        if "hrrrLightningAvailable" in frame.columns
        else None
    )
    return {
        "path": str(path),
        "rows": int(len(frame)),
        "runDateMin": str(frame["runDate"].min()) if "runDate" in frame.columns and len(frame) else None,
        "runDateMax": str(frame["runDate"].max()) if "runDate" in frame.columns and len(frame) else None,
        "uniqueRunDates": int(frame["runDate"].nunique()) if "runDate" in frame.columns else None,
        "forecastHourMin": float(frame["forecastHour"].min()) if "forecastHour" in frame.columns and len(frame) else None,
        "forecastHourMax": float(frame["forecastHour"].max()) if "forecastHour" in frame.columns and len(frame) else None,
        "labels": label_counts,
        "lightningAvailableRows": lightning_available,
    }


def _ensure_columns(frame: Any, pd: Any) -> Any:
    for column in LIGHTNING_COLUMNS:
        if column not in frame.columns:
            if column == "hrrrLightningAvailable":
                frame[column] = False
            elif column == "hrrrLightningFieldCount":
                frame[column] = 0
            else:
                frame[column] = pd.NA
    return frame


def _missing_feature_counts(frame: Any) -> dict[str, int]:
    return {
        name: int(frame[name].isna().sum())
        for name in FEATURE_NAMES
        if name in frame.columns and int(frame[name].isna().sum()) > 0
    }


def _lightning_coverage_by_year(frame: Any) -> dict[str, dict[str, int]]:
    if "runDate" not in frame.columns or "hrrrLightningAvailable" not in frame.columns:
        return {}
    work = frame.loc[:, ["runDate", "hrrrLightningAvailable"]].copy()
    work["year"] = work["runDate"].astype(str).str.slice(0, 4)
    available = work["hrrrLightningAvailable"].fillna(False).astype(bool)
    grouped = work.assign(_available=available).groupby("year", dropna=False)
    return {
        str(year): {
            "rows": int(group["_available"].count()),
            "lightningAvailableRows": int(group["_available"].sum()),
        }
        for year, group in grouped
    }


def _split_coverage(frame: Any) -> dict[str, int]:
    if "runDate" not in frame.columns:
        return {}
    dates = frame["runDate"].astype(str)
    return {
        "2020_2022": int(((dates >= "20200101") & (dates <= "20221231")).sum()),
        "2023_2024": int(((dates >= "20230101") & (dates <= "20241231")).sum()),
        "2025_202602": int(((dates >= "20250101") & (dates <= "20260228")).sum()),
    }


def _dedupe_training_rows(frame: Any) -> tuple[Any, int]:
    missing_keys = [column for column in KEY_COLUMNS if column not in frame.columns]
    if missing_keys:
        raise SystemExit(f"Training archive missing key columns: {missing_keys}")

    before = int(len(frame))
    work = frame.copy()
    available = (
        work["hrrrLightningAvailable"].fillna(False).astype(bool)
        if "hrrrLightningAvailable" in work.columns
        else False
    )
    field_count = (
        work["hrrrLightningFieldCount"].fillna(0).astype(int)
        if "hrrrLightningFieldCount" in work.columns
        else 0
    )
    work["_mergeLightningPreference"] = available.astype(int) * 10 + field_count
    work = work.sort_values([*KEY_COLUMNS, "_mergeLightningPreference"], kind="mergesort")
    work = work.drop_duplicates(subset=list(KEY_COLUMNS), keep="last")
    work = work.drop(columns=["_mergeLightningPreference"])
    work = work.sort_values(list(KEY_COLUMNS), kind="mergesort").reset_index(drop=True)
    return work, before - int(len(work))


def merge_archive(inputs: list[Path], output: Path, summary_path: Path) -> dict[str, Any]:
    pd, np = _require_deps()

    frames = []
    input_summaries = []
    for input_path in inputs:
        if not input_path.exists():
            raise SystemExit(f"Archive input does not exist: {input_path}")
        frame = pd.read_parquet(input_path)
        frame = _ensure_columns(frame, pd)
        frame["sourceFile"] = input_path.name
        input_summaries.append(_input_summary(frame, input_path))
        frames.append(frame)

    merged = pd.concat(frames, ignore_index=True, sort=False)
    duplicate_keys_before = int(merged.duplicated(subset=list(KEY_COLUMNS), keep=False).sum())
    merged, duplicates_dropped = _dedupe_training_rows(merged)

    for name in FEATURE_NAMES:
        if name not in merged.columns:
            raise SystemExit(f"Merged training archive missing feature column: {name}")
        merged[name] = pd.to_numeric(merged[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    for hazard in HAZARD_KEYS:
        label = f"label_{hazard}"
        if label not in merged.columns:
            raise SystemExit(f"Merged training archive missing label column: {label}")
        merged[label] = merged[label].fillna(0).astype(int)

    output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(output, index=False)

    summary = {
        "createdAtISO": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "inputs": input_summaries,
        "output": str(output),
        "summary": str(summary_path),
        "rowsBeforeDedupe": int(sum(item["rows"] for item in input_summaries)),
        "rows": int(len(merged)),
        "duplicateKeyRowsBeforeDedupe": duplicate_keys_before,
        "duplicatesDropped": int(duplicates_dropped),
        "uniqueKeys": int(merged[list(KEY_COLUMNS)].drop_duplicates().shape[0]),
        "runDateMin": str(merged["runDate"].min()) if len(merged) else None,
        "runDateMax": str(merged["runDate"].max()) if len(merged) else None,
        "uniqueRunDates": int(merged["runDate"].nunique()) if "runDate" in merged.columns else None,
        "forecastHourMin": float(merged["forecastHour"].min()) if len(merged) else None,
        "forecastHourMax": float(merged["forecastHour"].max()) if len(merged) else None,
        "labels": {
            hazard: int(merged[f"label_{hazard}"].sum())
            for hazard in HAZARD_KEYS
        },
        "lightningAvailableRows": int(merged["hrrrLightningAvailable"].fillna(False).astype(bool).sum()),
        "lightningCoverageByYear": _lightning_coverage_by_year(merged),
        "missingFeatureCounts": _missing_feature_counts(merged),
        "splitCoverage": _split_coverage(merged),
        "keyColumns": list(KEY_COLUMNS),
        "featureNames": list(FEATURE_NAMES),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", dest="inputs", help="Input parquet. Repeatable.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = merge_archive(args.inputs or _default_inputs(), args.output, args.summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
