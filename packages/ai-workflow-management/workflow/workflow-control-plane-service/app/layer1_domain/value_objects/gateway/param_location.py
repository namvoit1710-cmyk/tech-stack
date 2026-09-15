from __future__ import annotations

from enum import Enum


class ParamLocation(str, Enum):
    """Where a function parameter is placed when building the upstream request."""

    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    BODY = "body"
