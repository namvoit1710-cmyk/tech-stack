from __future__ import annotations

from enum import Enum


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"

    @classmethod
    def coerce(cls, value: "HttpMethod | str") -> "HttpMethod":
        """Normalize a method (case-insensitive) into the enum.

        Raises ``ValueError`` (the standard Enum failure) on an unknown method,
        which the entity factory/mutators surface as a domain ``ValidationError``.
        """
        if isinstance(value, cls):
            return value
        return cls(str(value).strip().upper())
