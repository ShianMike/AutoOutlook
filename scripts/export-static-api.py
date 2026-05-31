"""Export generated AutoOutlook artifacts as a static API tree.

This is used by the no-Google-Cloud hosting path:

1. Generate artifacts with ``backend.ml.outlook_pipeline``.
2. Build the Vite frontend into ``dist``.
3. Export JSON/GeoJSON files into ``dist/_api``.
4. Let Cloudflare Pages Functions map ``/api/*`` routes to those static files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "backend" / "artifacts" / "latest_incremental_complete"
DEFAULT_LEGACY_ARTIFACT_DIR = PROJECT_ROOT / "backend" / "artifacts" / "latest"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist" / "_api"
FULL_INCREMENTAL_FORECAST_HOURS = set(range(49))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--legacy-artifact-dir", type=Path, default=DEFAULT_LEGACY_ARTIFACT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def copy_if_exists(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def coerce_hours(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    hours: list[int] = []
    for item in value:
        try:
            hour = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 90:
            hours.append(hour)
    return sorted(set(hours))


def probability_max(metadata: dict[str, Any]) -> dict[str, float]:
    stats = metadata.get("probabilityStats") if isinstance(metadata.get("probabilityStats"), dict) else {}
    values = (
        stats.get("categoryConsistencyProbabilityMax")
        or stats.get("cappedProbabilityMax")
        or stats.get("environmentalCappedProbabilityMax")
        or stats.get("rawProbabilityMax")
        or {}
    )
    if not isinstance(values, dict):
        values = {}
    return {hazard: float(values.get(hazard, 0) or 0) for hazard in ("tornado", "hail", "wind")}


def import_artifact_server_helpers(artifact_dir: Path):
    os.environ["AUTOOUTLOOK_ARTIFACT_DIR"] = str(artifact_dir)
    os.environ["AUTOOUTLOOK_INCREMENTAL_ARTIFACT_DIR"] = str(artifact_dir)
    os.environ["AUTOOUTLOOK_INCREMENTAL_COMPLETE_ARTIFACT_DIR"] = str(artifact_dir)
    os.environ["AUTOOUTLOOK_FORECAST_SOURCE"] = "artifact"
    os.environ["AUTOOUTLOOK_ENABLE_LIVE_BUILD"] = "false"
    os.environ.pop("AUTOOUTLOOK_ARTIFACT_BUCKET", None)
    os.environ.pop("AUTOOUTLOOK_ARTIFACT_PREFIX", None)

    import importlib
    if "backend.server" in sys.modules:
        server = importlib.reload(sys.modules["backend.server"])
    else:
        from backend import server  # noqa: PLC0415

    return server


def build_incremental_summary(index: dict[str, Any], artifact_dir: Path, helpers) -> dict[str, Any]:
    hours: list[dict[str, Any]] = []
    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        metadata = read_json(artifact_dir / "hours" / f"f{forecast_hour:02d}" / "metadata.json")
        if not isinstance(metadata, dict):
            continue
        category_counts = metadata.get("categoryCounts") or {}
        if not isinstance(category_counts, dict):
            category_counts = {}
        raw_probability_max = probability_max(metadata)
        category = helpers._max_category_from_counts(category_counts)
        display_probability_max = helpers._cap_probabilities_for_category(raw_probability_max, category)
        main_hazard = helpers._main_hazard_from_probabilities(raw_probability_max)
        total_cells = sum(int(value) for value in category_counts.values() if isinstance(value, (int, float)))
        active_cells = total_cells - int(category_counts.get("NONE", 0) or 0)
        hours.append({
            "forecastHour": int(metadata.get("forecastHour", forecast_hour)),
            "validTimeISO": metadata.get("validTimeISO"),
            "category": category,
            "mainHazard": main_hazard,
            "peakHazardProbability": helpers._max_hazard_probability(display_probability_max),
            "significantSevere": helpers._has_significant_probability(display_probability_max, category, main_hazard),
            "coverage": active_cells / total_cells if total_cells > 0 else 0,
            "categoryCounts": category_counts,
            "probabilityMax": display_probability_max,
        })
    return {
        "cycle": index.get("cycle"),
        "cycleTimeISO": index.get("cycleTimeISO"),
        "generatedAtISO": index.get("generatedAtISO"),
        "hours": sorted(hours, key=lambda item: item["forecastHour"]),
    }


def merged_risk_polygons(index: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        payload = read_json(artifact_dir / "hours" / f"f{forecast_hour:02d}" / "risk_polygons.geojson")
        if isinstance(payload, dict) and isinstance(payload.get("features"), list):
            features.extend(payload["features"])
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "incremental_artifacts",
            "cycle": index.get("cycle"),
            "cycleTimeISO": index.get("cycleTimeISO"),
            "generatedAtISO": index.get("generatedAtISO"),
        },
    }


def lightweight_probability_tiles(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle": index.get("cycle"),
        "featureSchemaHash": index.get("featureSchemaHash"),
        "riskLabels": index.get("riskLabels"),
        "gridStride": index.get("gridStride"),
        "tileStride": index.get("tileStride"),
        "environmentalCapsApplied": True,
        "categoryConsistencyCapsApplied": True,
        "hours": [],
        "staticExportNote": "Per-hour probability tiles are served from /api/outlook/incremental/hour/:hour/probability-tile.",
    }


def validate_full_index(index: dict[str, Any]) -> None:
    ready = set(coerce_hours(index.get("readyForecastHours")))
    model = index.get("cycleDetection", {}).get("cyclePolicy", {}).get("model", "HRRR").upper()
    if model == "ECMWF":
        expected = set(range(0, 91, 3))
    else:
        expected = set(range(49))

    if index.get("status") != "complete" or not expected.issubset(ready):
        missing = sorted(expected - ready)
        raise ValueError(
            f"Refusing static export because artifacts are not a complete {model} set. "
            f"status={index.get('status')!r} missing={missing[:10]}"
        )


def export_static_api(artifact_dir: Path, legacy_artifact_dir: Path, output_dir: Path) -> None:
    artifact_dir = artifact_dir.resolve()
    legacy_artifact_dir = legacy_artifact_dir.resolve()
    output_dir = output_dir.resolve()
    index = read_json(artifact_dir / "index.json")
    if not isinstance(index, dict):
        raise SystemExit(f"Missing incremental index: {artifact_dir / 'index.json'}")
    validate_full_index(index)

    helpers = import_artifact_server_helpers(artifact_dir)
    forecast = helpers._artifact_forecast_bundle()
    if not isinstance(forecast, dict):
        raise SystemExit("Could not build /api/forecast from generated artifacts.")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "health.json", {
        "status": "ok",
        "service": "autooutlook-static-api",
        "generatedAtISO": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    })
    write_json(output_dir / "forecast.json", forecast)
    write_json(output_dir / "outlook" / "latest.json", index)
    write_json(output_dir / "outlook" / "incremental" / "index.json", index)
    write_json(output_dir / "outlook" / "incremental" / "summary.json", build_incremental_summary(index, artifact_dir, helpers))
    write_json(output_dir / "outlook" / "risk-polygons.geojson", merged_risk_polygons(index, artifact_dir))
    write_json(output_dir / "outlook" / "aggregate-risk-polygons.geojson", merged_risk_polygons(index, artifact_dir))
    write_json(output_dir / "outlook" / "probability-tiles.json", lightweight_probability_tiles(index))

    copy_if_exists(legacy_artifact_dir / "verification_summary.json", output_dir / "outlook" / "verification.json")
    copy_if_exists(legacy_artifact_dir / "preview.png", output_dir / "outlook" / "preview.png")

    for forecast_hour in coerce_hours(index.get("readyForecastHours")):
        source_hour_dir = artifact_dir / "hours" / f"f{forecast_hour:02d}"
        target_hour_dir = output_dir / "outlook" / "incremental" / "hour" / f"f{forecast_hour:02d}"
        copy_if_exists(source_hour_dir / "risk_polygons.geojson", target_hour_dir / "risk-polygons.geojson")
        copy_if_exists(source_hour_dir / "probability_tile.json", target_hour_dir / "probability-tile.json")
        copy_if_exists(source_hour_dir / "metadata.json", target_hour_dir / "metadata.json")

    print(json.dumps({
        "outputDir": str(output_dir),
        "cycle": index.get("cycle"),
        "cycleTimeISO": index.get("cycleTimeISO"),
        "readyForecastHours": len(coerce_hours(index.get("readyForecastHours"))),
    }, indent=2))


def main() -> None:
    args = parse_args()

    # If the user passed default artifact directory, we attempt to export both models if they exist.
    is_default_run = (args.artifact_dir == DEFAULT_ARTIFACT_DIR)

    if is_default_run:
        # 1. Export legacy/conus to output_dir (first, so it clears the root but keeps later subfolders)
        if DEFAULT_ARTIFACT_DIR.exists():
            try:
                print("Exporting CONUS artifacts (legacy root)...")
                export_static_api(DEFAULT_ARTIFACT_DIR, args.legacy_artifact_dir, args.output_dir)

                print("Exporting CONUS artifacts (regional)...")
                export_static_api(DEFAULT_ARTIFACT_DIR, args.legacy_artifact_dir, args.output_dir / "conus")
            except ValueError as exc:
                print(f"Warning: Skipping CONUS export: {exc}")
        else:
            print(f"CONUS artifacts dir not found: {DEFAULT_ARTIFACT_DIR}")

        # 2. Export Philippines to output_dir / "philippines" (Commented out to exclude from artifact generation)
        # ph_candidates = [
        #     PROJECT_ROOT / "backend" / "artifacts" / "latest_incremental_ecmwf_complete",
        # ]
        # ph_dir = next((path for path in ph_candidates if path.exists()), ph_candidates[0])
        # if ph_dir.exists():
        #     try:
        #         print("Exporting Philippines artifacts...")
        #         export_static_api(ph_dir, args.legacy_artifact_dir, args.output_dir / "philippines")
        #     except ValueError as exc:
        #         print(f"Warning: Skipping Philippines export: {exc}")
        # else:
        #     print(f"Philippines artifacts dir not found: {ph_dir}")
        pass
    else:
        try:
            export_static_api(args.artifact_dir, args.legacy_artifact_dir, args.output_dir)
        except ValueError as exc:
            raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
