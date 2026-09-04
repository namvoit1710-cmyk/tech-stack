from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def load_spacy_model(preferred_model_name: str) -> Any:
    import spacy

    attempted_model_names: list[str] = []
    for model_name in _candidate_model_names(preferred_model_name):
        attempted_model_names.append(model_name)
        try:
            return spacy.load(model_name)
        except OSError:
            continue

    attempted = ", ".join(attempted_model_names) or "<none>"
    raise RuntimeError(
        "Graph extraction requires an installed spaCy English model. "
        f"Tried: {attempted}"
    )


def _candidate_model_names(preferred_model_name: str) -> Iterable[str]:
    seen: set[str] = set()
    for model_name in (
        preferred_model_name,
        "en_core_web_lg",
        "en_core_web_md",
        "en_core_web_sm",
    ):
        normalized = str(model_name).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        yield normalized
