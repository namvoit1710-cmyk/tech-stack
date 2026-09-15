"""NodeSpec core model — the single source of truth for node configuration.

One frozen ``NodeSpec`` per node type declares everything that today lives
in five drifting catalogs (design: docs/design/node-spec-single-source.md):

* which config fields exist (key, type, default, choices, visibility),
* which keys are runtime input bindings vs design-time config,
* the node's outputs, ports, and palette metadata,
* how handlers read config (``parse_config`` → typed dataclass).

Everything else — the FE form schema, the parameters split, save-time
validation — is *derived* from the spec by sibling modules, never
hand-listed again.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from enum import Enum
from typing import Any, TypeVar

from app.layer1_domain.value_objects.node_class import NodeClass
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.port import Port


class NodeRuntime(str, Enum):
    """Where a node executes — the single source of truth for the engine's
    inline / pending / worker classification.

    The orchestrator derives ``BUILTIN_NODE_TYPES`` / ``PENDING_NODE_TYPES``
    from this field (see ``node_spec.registry.node_types_for_runtime``), so a
    new node type is classified by declaring its ``runtime`` on the spec —
    there is no hand-maintained list to keep in sync. (A hand-list is exactly
    how PROMPT and OUTPUT silently drifted into the worker-dispatch path.)

    * ``ENGINE``  — the control plane runs it inline via its handler
      (CONDITION, SWITCH, LOOP, MERGE, COMPUTE, WORKFLOW, PROMPT, OUTPUT, …).
      Maps to handler shapes ``PURE_INLINE`` / ``ASYNC_INLINE``.
    * ``PENDING`` — the run pauses and waits for a user action
      (INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD). Handler shape
      ``PENDING_USER``.
    * ``WORKER``  — work is dispatched to an external worker (TASK).
      Handler shape ``DISPATCH_EXTERNAL``.
    """

    ENGINE = "engine"
    PENDING = "pending"
    WORKER = "worker"


class FieldType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    JSON = "json"  # textarea/editor holding parseable JSON


class Widget(str, Enum):
    """Form-control override; ``None`` on a field means infer from type."""

    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    SELECT = "select"
    RADIO = "radio"                  # RadioPopupControl, choices as {id, value}
    SWITCH = "switch"
    ARRAY_TABLE = "array_table"      # TABLE_ROW outer / TABLE_CELL items
    ARRAY_INLINE = "array_inline"    # FRAGMENT outer / FORM_ITEM items
    OBJECT_EDITOR = "object_editor"  # CodeControl JSON editor


@dataclass(frozen=True)
class Choice:
    value: str  # canonical stored value (what the handler sees)
    label: str  # FE display label


@dataclass(frozen=True)
class VisibleWhen:
    """POSITIVE visibility: show the field when ``field == equals``.

    The form renderer inverts this to the FE's NOT_EQUALS hide-when
    convention; validation uses it to make ``required`` conditional.
    """

    field: str
    equals: str | tuple[str, ...]

    def matches(self, sibling_values: dict[str, Any]) -> bool:
        actual = sibling_values.get(self.field)
        if isinstance(self.equals, tuple):
            return actual in self.equals
        return actual == self.equals


@dataclass(frozen=True)
class FormGroup:
    """A tab in a tab-grouped form (e.g. DATA_EDIT Configuration/Settings)."""

    key: str
    label: str


@dataclass(frozen=True)
class ConfigField:
    """One design-time config key, routed to ``WorkflowNode.config``."""

    key: str
    type: FieldType
    label: str
    required: bool = False  # enforced only when visible (per visible_when)
    default: Any = None  # engine-side default (parse_config / validation)
    choices: tuple[Choice, ...] = ()  # non-empty => enum-validated
    expression: bool = False  # value may contain {{...}} / $node refs
    visible_when: VisibleWhen | None = None
    widget: Widget | None = None
    placeholder: str = ""
    description: str = ""
    item_fields: tuple["ConfigField", ...] = ()  # element shape for ARRAY
    internal: bool = False  # engine-consumed, never rendered in the form
    store_as: str | None = None  # split-time value transform, e.g. "dict"
    # ── render-only hints (no engine semantics) ──
    # Form default rendered into controlProps.value. Kept separate from
    # ``default`` so a field can have an engine default without changing
    # the served form (e.g. TRIGGER.trigger_type defaults to "manual" for
    # parse_config, but the palette form still shows a placeholder).
    form_default: Any = None
    # Extra wrapperProps merged into the rendered field (layout spans etc.).
    render_props: dict[str, Any] | None = None
    # Tab key (must match a NodeSpec.form_tabs entry) for tab-grouped forms.
    tab: str | None = None
    # FE AutoForm ``fieldControl`` override (render-only, no engine
    # semantics). When set, the form renderer emits a text-style field that
    # delegates rendering to a custom control component registered on the FE
    # Builder (e.g. WORKFLOW.workflow_id -> "WorkflowPickerControl").
    field_control: str | None = None


@dataclass(frozen=True)
class InputField:
    """One runtime data binding, routed to ``WorkflowNode.input_binding``."""

    key: str
    type: FieldType = FieldType.STRING
    label: str = ""
    required: bool = False
    expression: bool = True  # bindings are expressions by default
    placeholder: str = ""


@dataclass(frozen=True)
class OutputField:
    key: str
    type: FieldType
    label: str


@dataclass(frozen=True)
class NodeSpec:
    node_type: NodeType
    name: str
    description: str
    category: str  # e.g. "control_flow.concurrency"
    runtime: str  # "engine" | "pending" | "worker"
    node_class: NodeClass
    icon: str
    color: str
    in_ports: tuple[Port, ...] = ()
    out_ports: tuple[Port, ...] = ()
    min_out_ports: int | None = None
    max_out_ports: int | None = None
    config_fields: tuple[ConfigField, ...] = ()
    input_fields: tuple[InputField, ...] = ()
    output_fields: tuple[OutputField, ...] = ()
    # Cross-field rule: exactly one key per group may be set.
    requires_one_of: tuple[tuple[str, ...], ...] = ()
    # Tab grouping for the rendered form (empty = flat form).
    form_tabs: tuple[FormGroup, ...] = ()
    form_tabs_key: str = "tabs"  # key of the synthetic TAB container field
    spec_version: str = "1.0.0"

    def __post_init__(self) -> None:
        self._check_invariants()

    def _check_invariants(self) -> None:
        """Fail at construction (import time) on a malformed spec."""
        errors: list[str] = []
        keys = [f.key for f in self.config_fields]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            errors.append(f"duplicate config field keys: {sorted(dupes)}")
        key_set = set(keys)
        tab_keys = {t.key for t in self.form_tabs}
        for f in self.config_fields:
            if f.visible_when and f.visible_when.field not in key_set:
                errors.append(
                    f"{f.key}: visible_when references unknown field "
                    f"{f.visible_when.field!r}"
                )
            choice_values = [c.value for c in f.choices]
            if len(choice_values) != len(set(choice_values)):
                errors.append(f"{f.key}: duplicate choice values")
            if f.tab is not None and f.tab not in tab_keys:
                errors.append(f"{f.key}: unknown tab {f.tab!r}")
            if f.internal and f.tab is not None:
                errors.append(f"{f.key}: internal fields cannot sit in a tab")
        if self.form_tabs:
            untabbed = [
                f.key for f in self.config_fields
                if not f.internal and f.tab is None
            ]
            if untabbed:
                errors.append(
                    f"form_tabs set but fields lack a tab: {untabbed}"
                )
        for group in self.requires_one_of:
            unknown = [k for k in group if k not in key_set]
            if unknown:
                errors.append(f"requires_one_of references unknown: {unknown}")
        binding_keys = [f.key for f in self.input_fields]
        clash = key_set & set(binding_keys)
        if clash:
            errors.append(f"keys in both config and input: {sorted(clash)}")
        if self.runtime not in {r.value for r in NodeRuntime}:
            errors.append(f"invalid runtime {self.runtime!r}")
        if errors:
            raise ValueError(
                f"invalid NodeSpec for {self.node_type}: " + "; ".join(errors)
            )

    def config_field(self, key: str) -> ConfigField | None:
        for f in self.config_fields:
            if f.key == key:
                return f
        return None

    @property
    def config_keys(self) -> frozenset[str]:
        return frozenset(f.key for f in self.config_fields)


# ──────────────────────────── typed config parsing ────────────────────────────

T = TypeVar("T")


def _is_template(value: Any) -> bool:
    return isinstance(value, str) and "{{" in value


def coerce_to_type(value: Any, target: FieldType | str) -> Any:
    """Deterministic best-effort coercion to a declared field type.

    Forgiving by design — save-time validation rejects what coercion
    can't fix. Template expressions pass through untouched (resolved at
    runtime). Accepts a ``FieldType`` or its string value (``"number"``,
    ``"boolean"``, …) for callers coercing against AutoForm
    ``outputType`` declarations.
    """
    if value is None or value == "" or _is_template(value):
        return value
    target_value = target.value if isinstance(target, FieldType) else str(target)
    try:
        if target_value == FieldType.NUMBER.value:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value
            f = float(str(value))
            return int(f) if f == int(f) else f
        if target_value == FieldType.BOOLEAN.value:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes"):
                    return True
                if value.lower() in ("false", "0", "no"):
                    return False
            if isinstance(value, (int, float)):
                return bool(value)
        if target_value == FieldType.JSON.value and isinstance(value, str):
            import json

            return json.loads(value)
    except (ValueError, TypeError):
        pass
    return value


def coerce_value(spec_field: ConfigField, value: Any) -> Any:
    """Coerce ``value`` to ``spec_field``'s declared type (see coerce_to_type)."""
    return coerce_to_type(value, spec_field.type)


def parse_config(spec: NodeSpec, cls: type[T], node: Any) -> T:
    """Build the typed ``XxxConfig`` for a node from canonical keys only.

    * missing keys take the spec default (or the dataclass default when
      the spec default is ``None``);
    * values are coerced per the field's declared type;
    * unknown keys in ``node.config`` are ignored — they were already
      rejected at save time.

    ``node`` is duck-typed (needs ``.config``) to keep layer-1 imports
    acyclic with the workflow entity module.
    """
    raw = getattr(node, "config", None) or {}
    cls_fields = {f.name for f in dataclass_fields(cls)}  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}
    for f in spec.config_fields:
        if f.key not in cls_fields:
            continue
        if f.key in raw:
            kwargs[f.key] = coerce_value(f, raw[f.key])
        elif f.default is not None:
            kwargs[f.key] = f.default
        # else: let the dataclass default stand
    return cls(**kwargs)
