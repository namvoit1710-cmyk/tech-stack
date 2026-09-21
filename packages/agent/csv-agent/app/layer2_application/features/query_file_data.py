from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Dict
from urllib.parse import parse_qsl, urlencode

from app.layer1_domain.exceptions import FileProcessingError, InvalidInputError
from app.layer2_application.interfaces.file_preview_gateway import IFilePreviewGateway
from app.layer2_application.validators.input_validators import validate_file_id

logger = logging.getLogger(__name__)

_ALLOWED_ODATA_KEYS = {"$filter", "$select", "$orderby", "$top", "$skip", "$count"}
_DEFAULT_TOP = 50
_MAX_TOP = 500
_MAX_ODATA_VALUE_LENGTH = 1000
_MAX_ODATA_QUERY_LENGTH = 2500
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_.-]*"
_IDENTIFIER_RE = re.compile(rf"^{_IDENTIFIER}$")
_ORDERBY_ITEM_RE = re.compile(
    rf"^\s*{_IDENTIFIER}(?:\s+(?:asc|desc))?\s*$", re.IGNORECASE
)
_FILTER_ALLOWED_CHARS_RE = re.compile(r"^[A-Za-z0-9_.$'\"\s(),.:-]+$")
_DISALLOWED_FILTER_RE = re.compile(
    r"(--|/\*|\*/|;|\b(?:drop|insert|update|delete|alter|truncate|exec|execute|union)\b)",
    re.IGNORECASE,
)


def _normalize_key(key: Any) -> str:
    raw_key = str(key or "").strip()
    if not raw_key:
        raise InvalidInputError(
            "odata_query", "OData query contains an empty parameter name", raw_key
        )
    return raw_key if raw_key.startswith("$") else f"${raw_key}"


def _normalize_int_option(
    value: Any,
    *,
    field_name: str,
    default: int,
    max_value: int | None = None,
) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(field_name, "Must be an integer", value) from exc
    if parsed < 0:
        raise InvalidInputError(field_name, "Must be >= 0", value)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return str(parsed if value not in (None, "") else default)


def _normalize_top(value: Any, *, default: int, max_top: int) -> str:
    if value in (None, ""):
        value = default
    return _normalize_int_option(
        value, field_name="$top", default=default, max_value=max_top
    )


def _normalize_skip(value: Any) -> str:
    if value in (None, ""):
        value = 0
    return _normalize_int_option(value, field_name="$skip", default=0)


def _validate_length(key: str, value: str) -> str:
    if len(value) > _MAX_ODATA_VALUE_LENGTH:
        raise InvalidInputError(
            key, f"Too long, maximum {_MAX_ODATA_VALUE_LENGTH} characters", value[:100]
        )
    return value


def _validate_select(value: str) -> str:
    value = _validate_length("$select", value.strip())
    if not value:
        return value
    columns = [part.strip() for part in value.split(",")]
    if not all(columns) or not all(_IDENTIFIER_RE.match(column) for column in columns):
        raise InvalidInputError(
            "$select",
            "Must be a comma-separated list of safe column identifiers",
            value,
        )
    return ",".join(columns)


def _validate_orderby(value: str) -> str:
    value = _validate_length("$orderby", value.strip())
    if not value:
        return value
    items = [part.strip() for part in value.split(",")]
    if not all(items) or not all(_ORDERBY_ITEM_RE.match(item) for item in items):
        raise InvalidInputError(
            "$orderby",
            "Must be '<column> [asc|desc]' entries separated by commas",
            value,
        )
    return ",".join(items)


def _quotes_are_balanced(value: str) -> bool:
    single = 0
    double = 0
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'":
            single ^= 1
        elif char == '"':
            double ^= 1
    return single == 0 and double == 0


def _validate_filter(value: str) -> str:
    value = _validate_length("$filter", value.strip())
    if not value:
        return value
    if not _FILTER_ALLOWED_CHARS_RE.match(value):
        raise InvalidInputError(
            "$filter",
            "Contains unsupported characters for the safe OData filter grammar",
            value[:100],
        )
    if _DISALLOWED_FILTER_RE.search(value):
        raise InvalidInputError(
            "$filter", "Contains disallowed query/control keywords", value[:100]
        )
    if not _quotes_are_balanced(value):
        raise InvalidInputError("$filter", "Contains unbalanced quotes", value[:100])
    if value.count("(") != value.count(")"):
        raise InvalidInputError(
            "$filter", "Contains unbalanced parentheses", value[:100]
        )
    return value


def _validate_count(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false"}:
        raise InvalidInputError("$count", "Must be true or false", value)
    return normalized


def _validate_value(key: str, value: Any) -> str:
    raw_value = str(value).strip() if value is not None else ""
    if key == "$filter":
        return _validate_filter(raw_value)
    if key == "$select":
        return _validate_select(raw_value)
    if key == "$orderby":
        return _validate_orderby(raw_value)
    if key == "$top":
        return _normalize_top(raw_value, default=_DEFAULT_TOP, max_top=_MAX_TOP)
    if key == "$skip":
        return _normalize_skip(raw_value)
    if key == "$count":
        return _validate_count(raw_value or "true")
    raise InvalidInputError("odata_query", f"Unsupported OData option {key!r}", key)


def _iter_query_pairs(odata_query: Any) -> list[tuple[Any, Any]]:
    if odata_query is None:
        return []

    if isinstance(odata_query, Mapping):
        return list(odata_query.items())

    if isinstance(odata_query, str):
        query = odata_query.strip()
        if len(query) > _MAX_ODATA_QUERY_LENGTH:
            raise InvalidInputError(
                "odata_query",
                f"Too long, maximum {_MAX_ODATA_QUERY_LENGTH} characters",
                query[:100],
            )
        if query.startswith("?"):
            query = query[1:]
        return list(parse_qsl(query, keep_blank_values=True))

    if isinstance(odata_query, Sequence) and not isinstance(
        odata_query, (str, bytes, bytearray)
    ):
        pairs: list[tuple[Any, Any]] = []
        for item in odata_query:
            if (
                isinstance(item, Sequence)
                and not isinstance(item, (str, bytes, bytearray))
                and len(item) == 2
            ):
                pairs.append((item[0], item[1]))
            else:
                raise InvalidInputError(
                    "odata_query",
                    "OData query sequence items must be key/value pairs",
                    odata_query,
                )
        return pairs

    raise InvalidInputError(
        "odata_query",
        f"Must be a query string or mapping, got {type(odata_query).__name__}",
        odata_query,
    )


def normalize_odata_query(
    odata_query: Any,
    *,
    default_top: int = _DEFAULT_TOP,
    max_top: int = _MAX_TOP,
) -> str:
    """Normalize and guard a user supplied OData query.

    Supports only read-only OData options for /api/v1/odata/{file_id}/data:
    $filter, $select, $orderby, $top, $skip, and $count.

    A default $top is added to avoid accidentally returning very large files.
    """

    normalized: dict[str, str] = {}

    for raw_key, value in _iter_query_pairs(odata_query):
        key = _normalize_key(raw_key)
        if key not in _ALLOWED_ODATA_KEYS:
            raise InvalidInputError(
                "odata_query",
                f"Unsupported OData option '{raw_key}'. Allowed: {', '.join(sorted(_ALLOWED_ODATA_KEYS))}",
                raw_key,
            )
        normalized[key] = _validate_value(key, value)

    normalized["$top"] = _normalize_top(
        normalized.get("$top", default_top),
        default=default_top,
        max_top=max_top,
    )
    normalized["$skip"] = _normalize_skip(normalized.get("$skip", "0"))
    normalized["$count"] = _validate_count(normalized.get("$count", "true"))

    return urlencode(normalized)


def _wanted_fields(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw.split(",")
    if isinstance(value, Mapping):
        value = value.keys()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value: Any = payload
    for key in ("data", "value", "rows", "items"):
        if isinstance(value, dict) and key in value:
            value = value[key]
            break
    if isinstance(value, dict):
        value = value.get("data") or value.get("value") or value.get("rows") or []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _pick(row: dict[str, Any], field: str) -> tuple[bool, Any]:
    target = field.strip().lower()
    for key, value in row.items():
        if str(key).strip().lower() == target:
            return True, value
    return False, None


def _candidate_usable(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "n/a"}:
        return False
    return True


def _candidate_key(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value).strip().lower()


def _extracted_fields_payload(payload: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    rows = _rows(payload)

    if fields == ["*"]:
        seen_keys: set[str] = set()
        requested: list[str] = []
        for row in rows:
            for key in row.keys():
                normalized = str(key).strip().lower()
                if normalized and normalized not in seen_keys:
                    seen_keys.add(normalized)
                    requested.append(str(key))
    else:
        requested = fields

    extracted: dict[str, Any] = {}

    for field in requested:
        candidates: list[Any] = []
        seen_values: set[str] = set()

        for row in rows:
            found, value = _pick(row, field)
            if not found or not _candidate_usable(value):
                continue
            key = _candidate_key(value)
            if key not in seen_values:
                seen_values.add(key)
                candidates.append(value)

        if len(candidates) == 1:
            extracted[field] = {"value": candidates[0], "found": True}
        elif len(candidates) > 1:
            extracted[field] = {
                "value": None,
                "found": False,
                "status": "ambiguous",
                "candidates": candidates,
                "candidate_count": len(candidates),
            }
        else:
            extracted[field] = {"value": None, "found": False, "status": "not_found"}

    return {
        "output_contract": "extracted_fields.v1",
        "extracted_fields": extracted,
    }


def _query_keys(query: str) -> list[str]:
    return sorted(key for key, _ in parse_qsl(query, keep_blank_values=True))


def query_file_data_impl(
    file_id: str,
    odata_query: Any = "",
    *,
    file_preview_gateway: IFilePreviewGateway,
    default_top: int = _DEFAULT_TOP,
    max_top: int = _MAX_TOP,
    extract_fields: Any = None,
) -> Dict[str, Any]:
    """Read rows from the File Service OData data endpoint without modifying files."""

    normalized_file_id = validate_file_id(file_id, "file_id")
    normalized_query = normalize_odata_query(
        odata_query,
        default_top=default_top,
        max_top=max_top,
    )

    logger.info(
        "Running read-only OData query against file service: file_id=%s keys=%s",
        normalized_file_id,
        _query_keys(normalized_query),
    )

    try:
        payload = file_preview_gateway.query_odata_data(
            normalized_file_id, normalized_query
        )
    except Exception as exc:
        raise FileProcessingError(
            normalized_file_id,
            "query_odata_data",
            str(exc),
        ) from exc

    if not isinstance(payload, dict):
        payload = {"data": payload}

    payload.setdefault("file_id", normalized_file_id)
    payload["odata_query"] = normalized_query
    payload["read_only"] = True

    fields = _wanted_fields(extract_fields)
    if fields:
        payload.update(_extracted_fields_payload(payload, fields))

    return payload
