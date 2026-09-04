"""Deterministic hazardous-category rules pre-screen for SDS analysis.

This is the Software 5.0 "recipe" layer for SDS screening: for materials whose
text contains well-known hazardous signals, a deterministic keyword scan decides
"SDS required" without spending an LLM call. Only ambiguous materials fall through
to the model. Pure domain logic — framework-free, deterministic, unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleMatch:
    """A single hazardous-category hit produced by the rules pre-screen."""

    category: str
    matched_term: str


# Known hazardous / safety-regulated categories -> normalized trigger terms.
# Terms are stored already normalized (lowercase, no hyphens) so they compare
# directly against normalized material text. Keep terms specific enough to avoid
# false positives (word-boundary matched, e.g. "gas" will not match "gasket").
HAZARD_RULES: dict[str, tuple[str, ...]] = {
    "solvents": ("solvent", "acetone", "toluene", "xylene", "mek",
                 "methyl ethyl ketone", "thinner", "degreaser"),
    "fuels": ("fuel", "gasoline", "petrol", "diesel", "kerosene", "propane", "butane"),
    "lubricants": ("lubricant", "grease", "motor oil", "hydraulic oil", "cutting oil"),
    "pesticides": ("pesticide", "herbicide", "insecticide", "fungicide", "rodenticide"),
    "cleaning_agents": ("cleaning agent", "cleaner", "bleach", "detergent",
                        "disinfectant", "sanitizer"),
    "corrosives": ("corrosive", "acid", "caustic", "sodium hydroxide",
                   "sulfuric", "hydrochloric", "lye"),
    "flammables": ("flammable", "ignitable", "combustible"),
    "gases": ("gas", "nitrogen", "oxygen", "acetylene", "argon", "helium"),
    "aerosols": ("aerosol", "spray can", "propellant"),
    "batteries": ("battery", "batteries", "lithium", "li ion", "lead acid"),
    "oxidizers": ("oxidizer", "peroxide", "nitrate", "chlorate", "permanganate"),
    "toxins": ("toxic", "poison", "toxin", "hazardous substance"),
    "compressed_gases": ("compressed gas", "compressed air", "cylinder"),
    "chemicals": ("chemical", "reagent"),
}


def normalize(text: str) -> str:
    """Lowercase, turn hyphens/underscores into spaces, and collapse whitespace."""
    lowered = str(text).lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", lowered).strip()


def _contains_term(normalized_text: str, term: str) -> bool:
    # Word-boundary match so "gas" does not fire on "gasket" and "acid" does not
    # fire on "acidophilus"; multi-word terms match as a phrase.
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, normalized_text) is not None


def screen_text(text: str) -> list[RuleMatch]:
    """Return one RuleMatch per hazardous category found in ``text`` (deduped)."""
    normalized_text = normalize(text)
    if not normalized_text:
        return []
    matches: list[RuleMatch] = []
    for category, terms in HAZARD_RULES.items():
        for term in terms:
            if _contains_term(normalized_text, term):
                matches.append(RuleMatch(category=category, matched_term=term))
                break  # one hit per category is enough
    return matches
