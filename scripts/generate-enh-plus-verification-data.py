"""Generate hardcoded docs data for historical risk verification maps.

This is intentionally a local-only utility. It reads already-fetched historical
AutoOutlook hour tiles and SPC report files from backend/artifacts, recomputes
the merged outlook over the fixed event window, then writes a TypeScript data
module for the in-app documentation page. GitHub Actions should only build the
static result; it should not fetch HRRR archives or SPC reports for this view.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

import numpy as np
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ml.gridded_outlook import SPC_RISK_LABELS  # noqa: E402
from backend.ml.historical_event_verification import (  # noqa: E402
    DEFAULT_ENH_PLUS_EVENT_DATES,
    artifact_uses_model,
    event_slug,
    event_window_for_date,
    fetch_spc_daily_storm_reports,
    filter_spc_reports_for_event_window,
    max_spc_category,
    parse_event_date,
    report_counts,
)
from backend.ml.merged_outlook import merge_cycles_for_spc_window  # noqa: E402


ARTIFACT_ROOT = PROJECT_ROOT / "backend" / "artifacts" / "historical_enh_plus"
OUTPUT_PATH = PROJECT_ROOT / "src" / "data" / "historicalEnhPlusVerification.ts"
MODEL_METADATA_PATH = PROJECT_ROOT / "backend" / "models" / "metadata.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-date",
        action="append",
        help="Historical event date to include, YYYY-MM-DD. Defaults to the configured 2026 risk cases.",
    )
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    artifact_root = args.artifact_root if args.artifact_root.is_absolute() else PROJECT_ROOT / args.artifact_root
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    event_dates = (
        [parse_event_date(value) for value in args.event_date]
        if args.event_date
        else list(DEFAULT_ENH_PLUS_EVENT_DATES)
    )
    expected_model = read_json(MODEL_METADATA_PATH)
    events = [
        build_event_payload(event_date, artifact_root, expected_model)
        for event_date in event_dates
    ]
    write_typescript(output_path, events)
    print(f"Wrote {output_path.relative_to(PROJECT_ROOT)} with {len(events)} verification event(s).")


def build_event_payload(
    event_date: date,
    artifact_root: Path,
    expected_model: Mapping[str, Any],
) -> dict[str, Any]:
    window = event_window_for_date(event_date)
    source_dir = resolve_source_dir(event_date, artifact_root)
    source_index = read_json(source_dir / "index.json", default={})
    validate_source_artifact(source_dir, source_index, window.forecast_hours, expected_model)
    spc_geojson = read_json(source_dir / "spc_day1_cat.geojson")
    spc_label, spc_ordinal = max_spc_category(spc_geojson)
    spc_source = read_json(source_dir / "spc_source.json", default={})
    spc_hazards = load_or_fetch_spc_hazard_outlooks(source_dir, spc_source)
    source_model = source_index.get("model") if isinstance(source_index.get("model"), Mapping) else {}
    merged = generate_event_merge(source_dir, event_date, window, spc_geojson, spc_source)
    summary = merged["summary"]
    risk_polygons = merged["riskPolygons"]
    hazard_shapes = merged["hazardProbabilityShapes"]
    merged_tile = merged["probabilityTile"]

    reports = load_or_fetch_reports(source_dir, event_date)
    filtered_reports = filter_spc_reports_for_event_window(reports, window)
    report_summary = report_counts(filtered_reports)

    summary.update(
        {
            "eventDate": event_date.isoformat(),
            "eventWindowStartISO": window.start_iso,
            "eventWindowEndISO": window.end_iso,
            "d1WindowValidISO": window.start_iso,
            "d1WindowExpireISO": window.end_iso,
            "cycleTimeISO": window.cycle_iso,
            "verificationForecastHours": list(window.forecast_hours),
            "mergedCycles": [f"HRRR 00Z {event_date.strftime('%Y%m%d')}"],
            "mergeMethod": "maximum",
            "verificationGridSource": "shared_merged_outlook_generator_event_00z_f12_f36",
            "spcDay1Url": spc_source.get("day1Url"),
            "spcGeojsonZipUrl": spc_source.get("geojsonZipUrl"),
            "spcFetchedAtISO": spc_source.get("fetchedAtISO"),
            "spcFetchedAfterPredictionArtifacts": True,
            "spcMaxCategory": spc_label,
            "spcMeetsEnhPlus": spc_ordinal >= SPC_RISK_LABELS.index("ENH"),
            "spcReportCounts": report_summary,
            "gridStride": int_value(source_index.get("gridStride")),
            "tileStride": int_value(merged_tile.get("stride") or source_index.get("tileStride")),
            "tileShape": merged_tile.get("shape") or [],
            "model": dict(source_model),
            "modelVersion": source_model.get("version"),
            "featureSchemaVersion": source_model.get("featureSchemaVersion"),
            "trainingRows": int_value(source_model.get("trainingRows")),
            "leakageGuard": "Official SPC outlook and storm reports were fetched only after AutoOutlook prediction artifacts were generated.",
        }
    )

    return {
        "id": event_slug(event_date),
        "label": format_event_label(event_date, spc_label),
        "eventDate": event_date.isoformat(),
        "cycleTimeISO": window.cycle_iso,
        "eventWindowStartISO": window.start_iso,
        "eventWindowEndISO": window.end_iso,
        "forecastHours": list(window.forecast_hours),
        "maxSpcCategory": spc_label,
        "gridStride": int_value(source_index.get("gridStride")),
        "tileStride": int_value(merged_tile.get("stride") or source_index.get("tileStride")),
        "tileShape": merged_tile.get("shape") or [],
        "sourceArtifactDir": str(source_dir.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "summary": summary,
        "riskPolygons": risk_polygons,
        "hazardProbabilityShapes": hazard_shapes,
        "spcDay1": spc_geojson,
        "spcHazardProbabilityShapes": spc_hazards,
        "stormReports": [frontend_report(report) for report in filtered_reports],
    }


def generate_event_merge(
    source_dir: Path,
    event_date: date,
    window: Any,
    spc_geojson: Mapping[str, Any],
    spc_source: Mapping[str, Any],
) -> dict[str, Any]:
    def cached_spc_fetch(_session: Any, _output_dir: Path | None) -> dict[str, Any]:
        return {
            "day1Url": spc_source.get("day1Url"),
            "geojsonZipUrl": spc_source.get("geojsonZipUrl"),
            "fetchedAtISO": spc_source.get("fetchedAtISO"),
            "categoryGeojson": spc_geojson,
        }

    with TemporaryDirectory(prefix="autooutlook-enh-plus-merge-") as tmp:
        output_dir = Path(tmp)
        summary = merge_cycles_for_spc_window(
            [source_dir],
            spc_fetch_fn=cached_spc_fetch,
            output_dir=output_dir,
            target_date=event_date,
            window_valid=window.start_time,
            window_expire=window.end_time,
        )
        if "error" in summary:
            raise RuntimeError(f"Merged outlook generation failed for {event_date}: {summary['error']}")
        return {
            "summary": summary,
            "riskPolygons": read_json(output_dir / "merged_risk_polygons.geojson"),
            "hazardProbabilityShapes": read_json(output_dir / "merged_hazard_probability_shapes.geojson"),
            "probabilityTile": read_json(output_dir / "merged_probability_tile.json"),
        }


def resolve_source_dir(event_date: date, artifact_root: Path) -> Path:
    candidates = [
        artifact_root / event_slug(event_date),
        artifact_root / f"{event_slug(event_date)}_complete",
    ]
    for candidate in candidates:
        if (candidate / "hours").exists() and (candidate / "spc_day1_cat.geojson").exists():
            return candidate
    tried = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"No local historical event artifacts found for {event_date}: {tried}")


def validate_source_artifact(
    source_dir: Path,
    source_index: Mapping[str, Any],
    forecast_hours: tuple[int, ...],
    expected_model: Mapping[str, Any],
) -> None:
    ready = {int(value) for value in source_index.get("readyForecastHours", [])}
    missing = sorted(set(forecast_hours) - ready)
    if source_index.get("status") != "complete" or missing:
        raise RuntimeError(
            f"Historical source {source_dir} is incomplete; missing forecast hours: {missing}"
        )
    if not artifact_uses_model(source_index, expected_model):
        source_model = source_index.get("model")
        source_version = source_model.get("version") if isinstance(source_model, Mapping) else None
        raise RuntimeError(
            f"Historical source {source_dir} uses model {source_version!r}; "
            f"expected {expected_model.get('version')!r}"
        )


def load_or_fetch_reports(source_dir: Path, event_date: date) -> list[Mapping[str, Any]]:
    reports_path = source_dir / "spc_storm_reports.json"
    if reports_path.exists():
        payload = read_json(reports_path)
        reports = payload.get("reports")
        if isinstance(reports, list):
            return reports
    return fetch_spc_daily_storm_reports(event_date)


def load_or_fetch_spc_hazard_outlooks(
    source_dir: Path,
    spc_source: Mapping[str, Any],
) -> dict[str, Any]:
    hazards_path = source_dir / "spc_day1_hazards.geojson"
    if hazards_path.exists():
        payload = read_json(hazards_path)
        if isinstance(payload, Mapping) and isinstance(payload.get("features"), list):
            return dict(payload)

    zip_url = spc_source.get("geojsonZipUrl")
    if not isinstance(zip_url, str) or not zip_url:
        return {"type": "FeatureCollection", "features": []}

    response = requests.get(zip_url, timeout=20)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        features: list[dict[str, Any]] = []
        for hazard, archive_name in (("tornado", "torn"), ("hail", "hail"), ("wind", "wind")):
            member_name = select_geojson_member(zf.namelist(), archive_name)
            if member_name is None:
                continue
            payload = json.loads(zf.read(member_name).decode("utf-8"))
            features.extend(normalize_spc_hazard_features(hazard, payload))

    collection = {"type": "FeatureCollection", "features": features}
    hazards_path.write_text(json.dumps(collection), encoding="utf-8")
    return collection


def select_geojson_member(names: list[str], hazard_name: str) -> str | None:
    suffix = f"_{hazard_name}.nolyr.geojson"
    specific = [name for name in names if name.endswith(suffix) and name.startswith("day1otlk_")]
    generic = [name for name in names if name == f"day1otlk_{hazard_name}.nolyr.geojson"]
    return (specific or generic or [None])[0]


def normalize_spc_hazard_features(hazard: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    thresholds = {
        "tornado": [0.02, 0.05, 0.10, 0.15, 0.30, 0.45, 0.60],
        "hail": [0.05, 0.15, 0.30, 0.45, 0.60],
        "wind": [0.05, 0.15, 0.30, 0.45, 0.60],
    }[hazard]
    colors = {
        "tornado": ["#3b9b3b", "#a87d4f", "#d4ad7c", "#cf2727", "#c43eb1", "#6e0099", "#4b006b"],
        "hail": ["#a87d4f", "#f6c842", "#cf2727", "#c43eb1", "#6e0099"],
        "wind": ["#a87d4f", "#f6c842", "#cf2727", "#c43eb1", "#6e0099"],
    }[hazard]
    features: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        if not isinstance(feature, Mapping) or not feature.get("geometry"):
            continue
        props = feature.get("properties") or {}
        try:
            probability = float(props.get("LABEL"))
        except (TypeError, ValueError):
            continue
        if probability <= 0:
            continue
        bucket = min(
            range(len(thresholds)),
            key=lambda idx: abs(thresholds[idx] - probability),
        )
        label = f"{int(round(probability * 100))}%"
        features.append({
            "type": "Feature",
            "geometry": feature.get("geometry"),
            "properties": {
                "hazard": hazard,
                "hazardLabel": hazard,
                "probability": probability,
                "threshold": probability,
                "thresholdPercent": int(round(probability * 100)),
                "bucket": bucket,
                "label": label,
                "color": str(props.get("fill") or colors[bucket]),
                "forecastHour": 0,
                "validTimeISO": props.get("VALID_ISO"),
                "vectorization": {
                    "method": "official_spc_day1_probability_geojson",
                    "supportSource": "official_spc_hazard",
                    "forecaster": props.get("FORECASTER"),
                    "issueTimeISO": props.get("ISSUE_ISO"),
                    "stroke": props.get("stroke"),
                },
            },
        })
    return sorted(features, key=lambda item: (str(item["properties"]["hazard"]), int(item["properties"]["bucket"])))


def frontend_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": report.get("type"),
        "time": report.get("time", ""),
        "value": report.get("value", ""),
        "location": report.get("location", ""),
        "lat": report.get("lat"),
        "lon": report.get("lon"),
        "comment": report.get("comment", ""),
    }


def format_event_label(event_date: date, spc_label: str) -> str:
    month = event_date.strftime("%b")
    suffix = " (Moderate)" if spc_label in {"MDT", "MOD"} else ""
    return f"{month} {event_date.day}, {event_date.year}{suffix}"


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def write_typescript(output: Path, events: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(events, indent=2, ensure_ascii=True, default=json_default)
    text = (
        "/* This file is generated by scripts/generate-enh-plus-verification-data.py. */\n"
        "import type {\n"
        "  OutlookArtifactFeatureCollection,\n"
        "  OutlookProbabilityShapeFeatureCollection,\n"
        "  SpcCategoryFeatureCollection,\n"
        "  SpcStormReport,\n"
        "} from '../types/outlookArtifacts';\n\n"
        "export interface HistoricalEnhPlusEvent {\n"
        "  id: string;\n"
        "  label: string;\n"
        "  eventDate: string;\n"
        "  cycleTimeISO: string;\n"
        "  eventWindowStartISO: string;\n"
        "  eventWindowEndISO: string;\n"
        "  forecastHours: number[];\n"
        "  maxSpcCategory: string;\n"
        "  gridStride?: number | null;\n"
        "  tileStride?: number | null;\n"
        "  tileShape?: number[];\n"
        "  sourceArtifactDir: string;\n"
        "  summary: Record<string, unknown>;\n"
        "  riskPolygons: OutlookArtifactFeatureCollection;\n"
        "  hazardProbabilityShapes: OutlookProbabilityShapeFeatureCollection;\n"
        "  spcDay1: SpcCategoryFeatureCollection;\n"
        "  spcHazardProbabilityShapes: OutlookProbabilityShapeFeatureCollection;\n"
        "  stormReports: SpcStormReport[];\n"
        "}\n\n"
        f"const RAW_HISTORICAL_ENH_PLUS_EVENTS = {payload};\n\n"
        "export const HISTORICAL_ENH_PLUS_EVENTS = RAW_HISTORICAL_ENH_PLUS_EVENTS as unknown as HistoricalEnhPlusEvent[];\n\n"
        f"export const HISTORICAL_ENH_PLUS_RISK_LABELS = {json.dumps(SPC_RISK_LABELS)};\n"
    )
    output.write_text(text, encoding="utf-8", newline="\n")


def json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    main()
