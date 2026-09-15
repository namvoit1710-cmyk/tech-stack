"""Domain model for a governance template field.

A neutral projection of one entry from
``GET /api/v1/templates/{id}/fields``. The governance client maps the raw
API payload onto this dataclass; the schema builder consumes it. Keeping the
mapping in the client isolates the (still-to-be-confirmed) API key casing —
see the open items in ``docs/template-data-editor-node.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Binding kinds that mark a value-help field as a (possibly dependent) lookup.
DIRECT_LOOKUP = "DIRECT_LOOKUP"
DEPENDENT_LOOKUP = "DEPENDENT_LOOKUP"

# CDM ids carry a positional suffix (``city_1``). It is only used to build the
# search-help URL path; the row values themselves are keyed by the API field
# name (see :attr:`TemplateFieldConfig.result_key`), not the CDM id.
_CDM_SUFFIX_RE = re.compile(r"_\d+$")


@dataclass(frozen=True)
class TemplateFieldConfig:
    """One column's worth of template configuration.

    ``field`` is the API field key (e.g. ``cityName``); it names the
    search-help action and is the fallback column key. The requester-CSV
    column header — what the table actually binds to — is the ``alias`` (see
    :attr:`column_key`). ``cdm_id`` is the **BDM field name** (e.g. ``city_1``)
    used in the search-help URL path. ``dependency_fields`` lists the **CDM
    ids** this field depends on.
    """

    field: str
    alias: str = ""
    section: str = ""
    cdm_id: str = ""
    value_help: bool = False
    binding_kind: str = ""
    dependency_fields: tuple[str, ...] = ()
    order: int = 0
    flatten_name: str = ""
    required: bool = False
    payload_field: str = ""

    @property
    def is_lookup(self) -> bool:
        """True when the field should render as a search-help dropdown."""
        return bool(self.value_help and self.cdm_id)

    @property
    def label(self) -> str:
        return self.alias or self.field

    @property
    def column_key(self) -> str:
        """The requester-file CSV column header the table binds to.

        Governance uses the field ``alias`` as the header, so the AutoForm
        cell ``key`` must equal the alias to line up with the file's rows;
        falls back to ``field`` when the alias is empty.
        """
        return self.alias or self.field

    @property
    def result_key(self) -> str:
        """Key under which this field's value appears in a search-help row.

        Governance returns flat search-help rows keyed by the API ``field``
        name (e.g. ``cityName``) — there is no ``payload`` nesting. So the
        value-help control's ``valueKey``/``labelKey`` and the ``update_row``
        expression (``${rowData.<result_key>}``) all read ``field``.

        An explicit ``payload_field`` still overrides; the suffix-stripped
        ``cdm_id`` (``city_1`` → ``city``) is only a last resort when ``field``
        is absent.
        """
        if self.payload_field:
            return self.payload_field
        if self.field:
            return self.field
        return _CDM_SUFFIX_RE.sub("", self.cdm_id)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "TemplateFieldConfig":
        """Build from a governance API field dict (defensive key mapping).

        Accepts both camelCase API keys and the spreadsheet-style headers
        seen in ``sample/sample_field_config.csv``. Unknown keys are ignored.
        """

        def pick(*keys: str, default: Any = None) -> Any:
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return default

        deps_raw = pick("dependencyFields", "Dependency Fields", "dependency_fields", default="")
        if isinstance(deps_raw, str):
            deps = tuple(d.strip() for d in deps_raw.split(",") if d.strip())
        elif isinstance(deps_raw, (list, tuple)):
            deps = tuple(str(d).strip() for d in deps_raw if str(d).strip())
        else:
            deps = ()

        return cls(
            field=str(pick("field", "Field", default="")),
            alias=str(pick("alias", "Alias", default="")),
            section=str(pick("section", "Section", default="")),
            cdm_id=str(pick("cdmId", "cdm_id", "CDM ID", default="")),
            value_help=_as_bool(pick("valueHelp", "value_help", "Value Help", default=False)),
            binding_kind=str(pick("bindingKind", "binding_kind", "Binding Kind", default="")),
            dependency_fields=deps,
            order=_as_int(pick("order", "Order", default=0)),
            flatten_name=str(pick("flattenName", "flatten_name", "Flatten Name", default="")),
            required=_as_bool(pick("required", "Required", default=False)),
            payload_field=str(pick("payloadField", "payload_field", "Payload Field", default="")),
        )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y")
    return bool(value)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
