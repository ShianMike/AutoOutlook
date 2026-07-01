"""Property-based tests for SPC hazard shape field well-formedness.

Feature: spc-hazard-outlook-archive, Property 2: SPC hazard shape fields are
well-formed. For any normalized SPC hazard outlook, every feature SHALL carry
exactly one hazard from {tornado, hail, wind, thunder}, a probabilityPercent in
the inclusive range [0, 100], and a boolean significantSevere.

Validates: Requirements 2.2
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

# Canonical hazard keys plus aliases and unknown keys, so the generator exercises
# both the accepted input space and defensively-dropped keys.
_KNOWN_HAZARD_KEYS = [
    "tornado",
    "torn",
    "hail",
    "wind",
    "thunder",
    "thunderstorm",
    "tstm",
    "TORNADO",
    " Hail ",
]
_UNKNOWN_HAZARD_KEYS = ["fog", "flood", "", "unknown", "123", None]

# A simple, always-valid polygon geometry. The normalizer only requires geometry
# to be truthy, but a realistic polygon keeps the generated GeoJSON well-formed.
_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-100.0, 34.0], [-95.0, 34.0], [-95.0, 38.0], [-100.0, 38.0], [-100.0, 34.0]]],
}


def _probability_strategy() -> st.SearchStrategy[Any]:
    """Generate probabilities including out-of-range and mixed representations."""
    return st.one_of(
        st.floats(min_value=-50.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        st.integers(min_value=-100, max_value=300),
        st.sampled_from(["5", "10%", "-5", "150", "0.15", "45%", "", "abc"]),
        st.none(),
    )


def _significant_severe_strategy() -> st.SearchStrategy[Any]:
    """Generate the varied truthy/falsey significant-severe markers the code reads."""
    return st.one_of(
        st.booleans(),
        st.integers(min_value=-1, max_value=3),
        st.sampled_from(["true", "yes", "sig", "false", "no", "1", "0", ""]),
        st.none(),
    )


@st.composite
def _hazard_feature(draw: st.DrawFn) -> dict[str, Any]:
    hazard_key = draw(st.sampled_from(_KNOWN_HAZARD_KEYS + _UNKNOWN_HAZARD_KEYS))
    props: dict[str, Any] = {}
    if hazard_key is not None:
        props["hazard"] = hazard_key

    # Probability may be provided under any of the keys the normalizer inspects.
    prob = draw(_probability_strategy())
    prob_key = draw(st.sampled_from(["probability", "probabilityPercent", "threshold"]))
    if prob is not None:
        props[prob_key] = prob

    sig = draw(_significant_severe_strategy())
    if sig is not None:
        sig_key = draw(st.sampled_from(["significantSevere", "sigSevere", "significant", "sig", "hatched"]))
        props[sig_key] = sig

    # Occasionally add a label that may itself imply significant-severe.
    if draw(st.booleans()):
        props["label"] = draw(st.sampled_from(["5%", "SIG", "10%", "hatched"]))

    feature: dict[str, Any] = {
        "type": "Feature",
        "geometry": _POLYGON,
        "properties": props,
    }
    return feature


@st.composite
def _hazard_geojson(draw: st.DrawFn) -> Any:
    # Sometimes generate degenerate inputs (None / non-mapping / missing features).
    shape = draw(st.integers(min_value=0, max_value=10))
    if shape == 0:
        return None
    if shape == 1:
        return {}
    if shape == 2:
        return {"type": "FeatureCollection", "features": "not-a-list"}

    features = draw(st.lists(_hazard_feature(), min_size=0, max_size=12))
    # Include some malformed features to exercise defensive skipping.
    if draw(st.booleans()):
        features.append({"type": "Feature"})  # missing geometry
    if draw(st.booleans()):
        features.append("not-a-feature")
    return {"type": "FeatureCollection", "features": features}


class SpcHazardShapeFieldsProperty(unittest.TestCase):
    """Property 2: SPC hazard shape fields are well-formed."""

    # Feature: spc-hazard-outlook-archive, Property 2: SPC hazard shape fields
    # are well-formed.
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @given(_hazard_geojson())
    def test_shape_fields_are_well_formed(self, hazard_geojson: Any) -> None:
        normalized = normalize_spc_hazard_outlook(hazard_geojson)

        features = normalized["features"]
        self.assertIsInstance(features, list)

        for feature in features:
            props = feature["properties"]

            # Exactly one hazard from the supported set.
            hazard = props["hazard"]
            self.assertIn(hazard, SPC_HAZARD_TYPES)

            # probabilityPercent in the inclusive range [0, 100].
            percent = props["probabilityPercent"]
            self.assertIsInstance(percent, float)
            self.assertGreaterEqual(percent, 0.0)
            self.assertLessEqual(percent, 100.0)

            # significantSevere is strictly a boolean.
            sig = props["significantSevere"]
            self.assertIsInstance(sig, bool)


if __name__ == "__main__":
    unittest.main()
