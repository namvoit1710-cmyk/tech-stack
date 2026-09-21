"""Enums for the agent's controlled (non-external) response fields.

`str, Enum` so members are their wire strings (JSON-serialise to the value and
compare equal to the raw string).
"""
from __future__ import annotations

from enum import Enum


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class RuleValidity(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"


class TransportState(str, Enum):
    RECEIVED = "RECEIVED"
