"""Property-based tests for SPC hazard outlook normalization.

Feature: spc-hazard-outlook-archive
"""
from __future__ import annotations

import unittest
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.ml.merged_outlook import (
    SPC_HAZARD_TYPES,
    normalize_spc_hazard_outlook,
)

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
def _hazard_feature(draw: Any) -> dict[str, Any]:
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


class TestSpcHazardTypeCompleteness(unittest.TestCase):
    # Feature: spc-hazard-outlook-archive, Property 1
    # Property 1: SPC hazard response includes every hazard type. For any SPC
    # hazard GeoJSON input (including empty or partial ones), the normalized
    # output contains all four hazard types in availableHazards, and a hazard
    # with no shapes is present as an empty subset rather than omitted.
    # Validates: Requirements 2.3
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(hazard_geojson=_hazard_geojson_strategy)
    def test_all_hazard_types_always_present(self, hazard_geojson: Any) -> None:
        result = normalize_spc_hazard_outlook(hazard_geojson)

        properties = result["properties"]
        available = properties["availableHazards"]
        hazards_present = properties["hazardsPresent"]

        # Every one of the four supported hazard types is advertised, regardless
        # of how empty or partial the input was.
        self.assertEqual(set(available), set(SPC_HAZARD_TYPES))
        for hazard in SPC_HAZARD_TYPES:
            self.assertIn(hazard, available)
            self.assertIn(hazard, hazards_present)

        # A hazard with no shapes is represented as an empty subset (flag False),
        # never omitted from the completeness contract.
        emitted = {feature["properties"]["hazard"] for feature in result["features"]}
        for hazard in SPC_HAZARD_TYPES:
            self.assertEqual(hazards_present[hazard], hazard in emitted)


if __name__ == "__main__":
    unittest.main()
