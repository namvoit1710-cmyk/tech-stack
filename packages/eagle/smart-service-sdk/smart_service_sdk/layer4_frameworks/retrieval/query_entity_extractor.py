from __future__ import annotations

import re
import threading
from typing import Any

from smart_service_sdk.layer4_frameworks.retrieval.spacy_model_loader import (
    load_spacy_model,
)


class QueryEntityExtractor:
    _NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")
    _STOP_WORDS = {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }

    def __init__(
        self,
        *,
        spacy_model_name: str = "en_core_web_sm",
        nlp: Any | None = None,
        max_seed_count: int = 8,
    ):
        self._spacy_model_name = spacy_model_name
        self._nlp = nlp
        self._lock = threading.Lock()
        self._max_seed_count = max_seed_count

    def extract(self, query_text: str) -> list[str]:
        normalized_query = " ".join(str(query_text).split()).strip()
        if not normalized_query:
            return []

        seed_texts: list[str] = []
        for candidate in self._spacy_candidates(normalized_query):
            self._append_seed(seed_texts, candidate)
        if not seed_texts:
            for candidate in self._fallback_candidates(normalized_query):
                self._append_seed(seed_texts, candidate)
        return seed_texts[: self._max_seed_count]

    def _spacy_candidates(self, query_text: str) -> list[str]:
        try:
            nlp = self._ensure_nlp()
        except Exception:
            return []

        doc = nlp(query_text)
        seed_texts: list[str] = []
        for span in tuple(getattr(doc, "ents", ())):
            seed_texts.append(str(span.text))
        try:
            noun_chunks = tuple(getattr(doc, "noun_chunks", ()))
        except Exception:
            noun_chunks = ()
        for span in noun_chunks:
            seed_texts.append(str(span.text))
        return seed_texts

    def _ensure_nlp(self):
        if self._nlp is None:
            with self._lock:
                if self._nlp is None:
                    self._nlp = load_spacy_model(self._spacy_model_name)
        return self._nlp

    def _fallback_candidates(self, query_text: str) -> list[str]:
        tokens = [
            token
            for token in self._NORMALIZE_PATTERN.sub(" ", query_text.lower()).split()
            if token and token not in self._STOP_WORDS
        ]
        if not tokens:
            return []

        seed_texts = [" ".join(tokens[: min(len(tokens), 4)])]
        seed_texts.extend(tokens)
        return seed_texts

    def _append_seed(self, seed_texts: list[str], candidate: str) -> None:
        normalized_candidate = " ".join(str(candidate).split()).strip()
        if not normalized_candidate:
            return
        if normalized_candidate.lower() in self._STOP_WORDS:
            return
        if normalized_candidate not in seed_texts:
            seed_texts.append(normalized_candidate)
