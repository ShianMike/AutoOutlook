"""SPC probability plus Conditional Intensity Group category conversion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
Hazard = Literal["tornado", "hail", "wind"]

CATEGORY_ORDER: tuple[Category, ...] = ("NONE", "TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH")
CATEGORY_ORDINAL = {category: ordinal for ordinal, category in enumerate(CATEGORY_ORDER)}

_NORMALIZED_CATEGORY_ALIASES = {
    "MOD": "MDT",
    "MODERATE": "MDT",
    "MARGINAL": "MRGL",
    "SLIGHT": "SLGT",
    "ENHANCED": "ENH",
}

_TABLES: dict[Hazard, tuple[tuple[float, tuple[Category | None, ...]], ...]] = {
    "tornado": (
        (0.60, ("ENH", "HIGH", "HIGH", "HIGH")),
        (0.45, ("ENH", "MDT", "HIGH", "HIGH")),
        (0.30, ("ENH", "MDT", "HIGH", "HIGH")),
        (0.15, ("ENH", "ENH", "MDT", "MDT")),
        (0.10, ("SLGT", "ENH", "ENH", "ENH")),
        (0.05, ("SLGT", "SLGT", "ENH", None)),
        (0.02, ("MRGL", "MRGL", "SLGT", None)),
    ),
    "wind": (
        (0.90, ("ENH", "MDT", "HIGH", "HIGH")),
        (0.75, ("ENH", "MDT", "HIGH", "HIGH")),
        (0.60, ("ENH", "MDT", "HIGH", "HIGH")),
        (0.45, ("ENH", "ENH", "MDT", "HIGH")),
        (0.30, ("SLGT", "ENH", "ENH", None)),
        (0.15, ("SLGT", "SLGT", "ENH", None)),
        (0.05, ("MRGL", "MRGL", "SLGT", None)),
    ),
    "hail": (
        (0.60, ("ENH", "MDT", "MDT")),
        (0.45, ("ENH", "ENH", "MDT")),
        (0.30, ("SLGT", "ENH", "ENH")),
        (0.15, ("SLGT", "SLGT", "ENH")),
        (0.05, ("MRGL", "MRGL", "SLGT")),
    ),
}

_MAX_CIG_BY_HAZARD: dict[Hazard, int] = {
    "tornado": 3,
    "wind": 3,
    "hail": 2,
}


@dataclass(frozen=True)
class CategoryConversion:
    hazard: Hazard
    probability: float
    cig: int
    category: Category
    probabilityThreshold: float | None
    clamped: bool = False
    reason: str | None = None


def normalize_category(category: str) -> Category:
    normalized = category.strip().upper()
    normalized = _NORMALIZED_CATEGORY_ALIASES.get(normalized, normalized)
    if normalized not in CATEGORY_ORDINAL:
        raise ValueError(f"Unknown SPC category: {category}")
    return normalized  # type: ignore[return-value]


def category_ordinal(category: str) -> int:
    return CATEGORY_ORDINAL[normalize_category(category)]


def normalize_probability(probability: float) -> float:
    out = float(probability)
    if out > 1.0:
        out = out / 100.0
    return max(0.0, min(1.0, out))


def normalize_cig(hazard: str, cig: str | int | float | None) -> tuple[Hazard, int, bool, str | None]:
    normalized_hazard = hazard.strip().lower()
    if normalized_hazard not in _TABLES:
        raise ValueError(f"Unknown SPC hazard: {hazard}")
    hazard_key = normalized_hazard  # type: ignore[assignment]

    if cig is None:
        return hazard_key, 0, False, None
    if isinstance(cig, str):
        raw = cig.strip().upper().replace("INTENSITY LEVEL", "").replace("LEVEL", "").strip()
        if raw in {"", "NONE", "BELOW", "BELOW_CIG1", "<CIG1", "NO_CIG"}:
            value = 0
        elif raw.startswith("CIG"):
            value = int(raw.replace("CIG", "").strip())
        else:
            value = int(float(raw))
    else:
        value = int(cig)

    clamped = False
    reason = None
    if value < 0:
        value = 0
        clamped = True
        reason = "cig_below_supported_range"
    max_cig = _MAX_CIG_BY_HAZARD[hazard_key]
    if value > max_cig:
        value = max_cig
        clamped = True
        reason = "cig_above_supported_range"
    return hazard_key, value, clamped, reason


def category_conversion_from_probability_and_cig(
    hazard: str,
    probability: float,
    cig: str | int | float | None,
) -> CategoryConversion:
    hazard_key, cig_value, clamped, reason = normalize_cig(hazard, cig)
    probability_value = normalize_probability(probability)
    table = _TABLES[hazard_key]

    for threshold, categories in table:
        if probability_value < threshold:
            continue
        if cig_value >= len(categories):
            valid_categories = [category for category in categories if category is not None]
            return CategoryConversion(
                hazard=hazard_key,
                probability=probability_value,
                cig=cig_value,
                category=max(valid_categories, key=category_ordinal),
                probabilityThreshold=threshold,
                clamped=True,
                reason="cig_not_used_for_probability_row",
            )
        category = categories[cig_value]
        if category is None:
            valid_categories = [item for item in categories if item is not None]
            return CategoryConversion(
                hazard=hazard_key,
                probability=probability_value,
                cig=cig_value,
                category=max(valid_categories, key=category_ordinal),
                probabilityThreshold=threshold,
                clamped=True,
                reason="cig_not_used_for_probability_row",
            )
        return CategoryConversion(
            hazard=hazard_key,
            probability=probability_value,
            cig=cig_value,
            category=category,
            probabilityThreshold=threshold,
            clamped=clamped,
            reason=reason,
        )

    return CategoryConversion(
        hazard=hazard_key,
        probability=probability_value,
        cig=cig_value,
        category="TSTM" if probability_value > 0.0 else "NONE",
        probabilityThreshold=None,
        clamped=clamped,
        reason=reason,
    )


def category_from_probability_and_cig(
    hazard: str,
    probability: float,
    cig: str | int | float | None,
) -> Category:
    return category_conversion_from_probability_and_cig(hazard, probability, cig).category
