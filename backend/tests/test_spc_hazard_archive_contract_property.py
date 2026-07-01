"""Property-based tests for archive/live SPC hazard contract equivalence.

Feature: spc-hazard-outlook-archive, Property 3: Archived SPC hazard outlook
matches the live contract. For any SPC hazard GeoJSON, normalizing it for the
live endpoint and normalizing it for the archive artifact SHALL produce the same
threshold-to-color mapping, the same per-threshold labels, and the same
significant-severe entries.

Both paths converge on
:func:`backend.ml.merged_outlook.normalize_spc_hazard_outlook`: the live
endpoint normalizes the cached ``spc_day1_hazards.geojson`` directly, while the
archive path (:func:`backend.ml.enh_plus_archive._archive_spc_hazard_shapes`)
reads/parses the source GeoJSON, normalizes it, and writes the artifact. This
test drives the *actual* archive normalization path (write source -> archive ->
read artifact back) and compares it against the live normalization.

Validates: Requirements 3.8
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.ml.enh_plus_archive import _archive_spc_hazard_shapes, _read_json, _write_json
from backend.ml.merged_outlook import normalize_spc_hazard_outlook

# A simple, always-valid polygon so features are never dropped for lacking geometry.
_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-100.0, 34.0], [-95.0, 34.0], [-95.0, 38.0], [-100.0, 38.0], [-100.0, 34.0]]],
}

# Hazard label values a raw SPC hazard GeoJSON might carry: canonical names,
# aliases, casing variants, unknown/garbage keys, and missing values.
_hazard_label_strategy = st.one_of(
    st.sampled_from(["tornado", "hail", "wind", "thunder"]),
    st.sampled_from(["torn", "TORNADO", "Hail", "WIND", "thunderstorm", "tstm", "Thunder"]),
    st.sampled_from(["hurricane", "flood", "", "unknown", "123"]),
    st.none(),
)

# Probability values spanning valid fractions, out-of-range values, and junk.
_probability_strategy = st.one_of(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-10, max_value=200),
    st.sampled_from(["0.05", "0.15", "SIG", "", "n/a"]),
    st.none(),
)


@st.composite
def _hazard_feature(draw: st.DrawFn) -> dict[str, Any]:
    props: dict[str, Any] = {}
    label = draw(_hazard_label_strategy)
    if label is not None:
        props["hazard"] = label
    probability = draw(_probability_strategy)
    if probability is not None:
        props["probability"] = probability
    if draw(st.booleans()):
        props["significantSevere"] = draw(st.booleans())
    feature: dict[str, Any] = {"type": "Feature", "properties": props}
    # Usually attach geometry; occasionally omit it to exercise the defensive
    # skip path in the normalizer.
    if draw(st.integers(min_value=0, max_value=5)) != 0:
        feature["geometry"] = _POLYGON
    return feature


_hazard_geojson_strategy = st.one_of(
    st.none(),
    st.just({"type": "FeatureCollection", "features": []}),
    st.builds(
        lambda features: {"type": "FeatureCollection", "features": features},
        st.lists(_hazard_feature(), min_size=0, max_size=12),
    ),
)


def _contract_projection(collection: Any) -> list[tuple[str, str, str, int, bool]]:
    """Extract the contract fields the archive must preserve.

    Captures, per feature and in order: the hazard type, the per-threshold
    ``label``, the threshold-to-``color`` mapping, the ``thresholdPercent``, and
    the ``significantSevere`` flag. These are exactly the "threshold-to-color
    mapping, per-threshold labels, and significant-severe entries" of the
    property. Stable JSON scalar fields only, so comparison is unaffected by the
    archive path's JSON round trip.
    """
    features = collection.get("features", []) if isinstance(collection, dict) else []
    projection: list[tuple[str, str, str, int, bool]] = []
    for feature in features:
        props = feature["properties"]
        projection.append(
            (
                props["hazard"],
                props["label"],
                props["color"],
                int(props["thresholdPercent"]),
                bool(props["significantSevere"]),
            )
        )
    return projection


class SpcHazardArchiveContractProperty(unittest.TestCase):
    """Property 3: Archived SPC hazard outlook matches the live contract."""

    # Feature: spc-hazard-outlook-archive, Property 3: Archived SPC hazard
    # outlook matches the live contract.
    # Validates: Requirements 3.8
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(hazard_geojson=_hazard_geojson_strategy)
    def test_archive_matches_live_contract(self, hazard_geojson: Any) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "spc_day1_hazards.geojson"
            dst = Path(tmp) / "spc-hazard-shapes.geojson"

            # Persist the raw SPC hazard GeoJSON the same way the merged-D1
            # directory would hold it.
            _write_json(src, hazard_geojson)

            # Live endpoint normalizes the cached source GeoJSON directly.
            live = normalize_spc_hazard_outlook(_read_json(src))

            # Archive path reads/parses the source, normalizes, and writes the
            # artifact; we then read the artifact back exactly as the archive
            # endpoint would serve it.
            _archive_spc_hazard_shapes(src, dst)
            archived = _read_json(dst)

        # The archived artifact must expose the same threshold-to-color mapping,
        # per-threshold labels, and significant-severe entries as the live one.
        self.assertEqual(
            _contract_projection(archived),
            _contract_projection(live),
        )

        # The completeness contract (availableHazards + hazardsPresent) is also
        # identical between the two paths.
        self.assertEqual(
            archived["properties"]["availableHazards"],
            live["properties"]["availableHazards"],
        )
        self.assertEqual(
            archived["properties"]["hazardsPresent"],
            live["properties"]["hazardsPresent"],
        )


if __name__ == "__main__":
    unittest.main()
