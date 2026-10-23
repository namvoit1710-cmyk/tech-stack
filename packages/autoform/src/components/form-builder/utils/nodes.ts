import type { IField, IFieldConfig, ISchema } from "../../../types/schema";
import type {
  DesignerContainer,
  DesignerField,
  DesignerNode,
  PaletteFieldDef, PaletteWrapperDef,
} from "../types";

let _cnt = 0;
export const uid = () => `${++_cnt}_${Math.random().toString(36).slice(2, 7)}`;

// ─── Factory ──────────────────────────────────────────────────────────────────

export function createField(
  def: PaletteFieldDef,
  existingKeys: string[] = []
): DesignerField {
  const key = uniqueKey(def.type.replace("Control", "").toLowerCase(), existingKeys);
  return {
    _id: uid(),
    _kind: "field",
    key,
    outputType: def.defaultOutputType,
    fieldConfig: {
      fieldWrapper: def.defaultWrapper,
      fieldControl: def.type,
      wrapperProps: { label: def.label },
      controlProps: { ...(def.defaultControlProps ?? {}) },
      rules: [],
      condition: null,
    },
  };
}

export function createContainer(def: PaletteWrapperDef): DesignerContainer {
  const key = uid().slice(0, 8);
  const base: DesignerContainer = {
    _id: uid(),
    _kind: "container",
    wrapperType: def.wrapperType ?? "DivWrapper",
    key: def.wrapperType ? `${def.wrapperType?.toLowerCase().replace("wrapper", "")}_${key}` : uid(),
    outputType: def.defaultOutputType,
    fieldConfig: {
      fieldWrapper: def.wrapperType,
      wrapperProps: { label: def.label },
      controlProps: {},
      rules: [],
      condition: null,
    },
    children: [],
  };

  if (def.wrapperType === "OdataWrapper") {
    base.fieldConfig.wrapperProps = {
      ...base.fieldConfig.wrapperProps,
      isAdd: true,
      isEdit: true,
      isDelete: true,
      isShowColumnSetting: true,
      isShowSearch: true,
    };
  }

  if (def.wrapperType === "TabWrapper") {
    base.fieldConfig.wrapperProps = {
      ...base.fieldConfig.wrapperProps,
      variant: "default",
      orientation: "horizontal",
    };
  }

  if (def.containerMode === "slots") {
    const isSeq = def.wrapperType === "SequenceWrapper";
    const prefix = isSeq ? "step" : "tab";
    const labelWord = isSeq ? "Step" : "Tab";
    const slotKeys: string[] = [];
    base.slots = [1, 2].map((i) => {
      const key = makeSlotKey(prefix, slotKeys);
      slotKeys.push(key);
      return { _id: uid(), key, label: `${labelWord} ${i}`, children: [] };
    });
  } else if (def.wrapperType === "FragmentWrapper" && def.defaultOutputType === "array") {
    base.arrays = [];
  } else if (def.containerMode === "table") {
    base.columns = [];
  }

  return base;
}

function uniqueKey(base: string, existingKeys: string[]): string {
  let key = base;
  let i = 1;
  while (existingKeys.includes(key)) key = `${base}_${i++}`;
  return key;
}

/**
 * Sanitise an arbitrary string into a valid-ish JS identifier usable as a data key.
 * SA-1053 bug K: tab keys were `slot.label.toLowerCase().replace(/\s+/g,"_")`, so
 * `"Price (USD)"` became `price_(usd)` — a syntax error in action-engine expressions
 * (`formValues.price_(usd)`). This strips every char outside [a-z0-9_].
 */
export function sanitizeKey(raw: string): string {
  let s = (raw ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!s) s = "key";
  if (/^[0-9]/.test(s)) s = `_${s}`;
  return s;
}

/** A stable, unique slot key of the form `${prefix}_${n}` (e.g. `tab_1`, `step_2`). */
export function makeSlotKey(prefix: string, existingKeys: string[]): string {
  let i = 1;
  let key = `${prefix}_${i}`;
  while (existingKeys.includes(key)) key = `${prefix}_${++i}`;
  return key;
}

// ─── Tree helpers ─────────────────────────────────────────────────────────────

/** Collect all keys in the node tree (for uniqueness checks) */
export function collectAllKeys(nodes: DesignerNode[]): string[] {
  return nodes.flatMap((n) => {
    if (n._kind === "field") return [n.key];
    return [
      n.key,
      ...collectAllKeys(n.children),
      ...(n.slots ?? []).flatMap((s) => [s.key, ...collectAllKeys(s.children)]),
      ...(n.columns ?? []).map((c) => c.key),
      ...(n.arrays ?? []).map((c) => c.key),
    ];
  });
}

/** Find node by _id anywhere in the tree; returns [node, parent ref to its array] */
export function findNodeById(
  nodes: DesignerNode[],
  id: string
): { node: DesignerNode; list: DesignerNode[] } | null {
  for (const n of nodes) {
    if (n._id === id) return { node: n, list: nodes };
    if (n._kind === "container") {
      const inChildren = findNodeById(n.children, id);
      if (inChildren) return inChildren;
      for (const slot of n.slots ?? []) {
        const inSlot = findNodeById(slot.children, id);
        if (inSlot) return inSlot;
      }
      if (n.columns) {
        const inCols = findNodeById(n.columns, id);
        if (inCols) return inCols;
      }
      if (n.arrays) {
        const inArrays = findNodeById(n.arrays, id);
        if (inArrays) return inArrays;
      }
    }
  }
  return null;
}

/**
 * Find the container that directly holds the node with the given id, scanning
 * children, slot children, columns, and arrays. Returns null when the node is
 * at the root or not found.
 */
export function findParentContainer(
  nodes: DesignerNode[],
  id: string,
  parent: DesignerContainer | null = null,
): DesignerContainer | null {
  for (const n of nodes) {
    if (n._id === id) return parent;
    if (n._kind !== "container") continue;
    const lists: DesignerNode[][] = [
      n.children,
      ...(n.slots?.map((s) => s.children) ?? []),
      ...(n.columns ? [n.columns] : []),
      ...(n.arrays ? [n.arrays] : []),
    ];
    for (const list of lists) {
      const found = findParentContainer(list, id, n);
      if (found !== null) return found;
    }
  }
  return null;
}

/** Remove node by _id from tree; returns new list */
export function removeNodeById(nodes: DesignerNode[], id: string): DesignerNode[] {
  return nodes
    .filter((n) => n._id !== id)
    .map((n) => {
      if (n._kind !== "container") return n;
      return {
        ...n,
        children: removeNodeById(n.children, id) as DesignerField[],
        slots: n.slots?.map((s) => ({
          ...s,
          children: removeNodeById(s.children, id),
        })),
        columns: n.columns
          ? (removeNodeById(n.columns, id) as DesignerField[])
          : undefined,
        arrays: n.arrays
          ? (removeNodeById(n.arrays, id) as DesignerField[])
          : undefined,
      };
    });
}

/** Insert node into a specific list at index */
export function insertAt<T>(list: T[], item: T, index: number): T[] {
  const next = [...list];
  next.splice(index < 0 ? next.length : index, 0, item);
  return next;
}

/** Deep clone a node tree */
export function deepClone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj));
}

// ─── Schema serialiser ────────────────────────────────────────────────────────

function fieldToIField(node: DesignerField): IField {
  const fc = { ...node.fieldConfig };
  if (!fc.rules?.length) delete fc.rules;
  if (!fc.condition?.fieldKey) delete fc.condition;
  const wp = { ...fc.wrapperProps };
  const cp = { ...fc.controlProps };
  if (!Object.keys(wp).length) delete fc.wrapperProps; else fc.wrapperProps = wp;
  if (!Object.keys(cp).filter(k => cp[k] !== "" && cp[k] !== undefined).length) delete fc.controlProps; else fc.controlProps = cp;
  // condition is either deleted (empty) or a non-null IConditionConfig, so fc matches schema IFieldConfig.
  return {
    key: node.key,
    outputType: node.outputType,
    ...(node.default !== undefined ? { default: node.default } : {}),
    fieldConfig: fc as IFieldConfig,
  };
}

function containerToIField(node: DesignerContainer): IField {
  const fc = { ...node.fieldConfig };
  if (!fc.rules?.length) delete fc.rules;
  if (!fc.condition?.fieldKey) delete fc.condition;

  const iField: IField = {
    key: node.key,
    outputType: node.outputType,
    // Container-level default (object for Tabs, array for OData) — the runtime's
    // getDefaultValues uses it to seed the form (helpers.ts). Only emit when set.
    ...(node.default !== undefined ? { default: node.default } : {}),
    fieldConfig: { fieldWrapper: node.wrapperType, ...fc } as IFieldConfig,
    fields: [],
  };

  if (node.slots?.length) {
    // TabWrapper / SequenceWrapper: each slot → object child.
    // SA-1053 bug K: the data key is the slot's own STABLE key, never derived from the
    // (mutable, unsanitised) label — renaming a tab must not rewrite the payload shape.
    // Fallback + sanitise handles any legacy slot that predates the `key` field.
    const slotKeys: string[] = [];
    iField.fields = node.slots.map((slot) => {
      const key = uniqueKey(sanitizeKey(slot.key || slot.label), slotKeys);
      slotKeys.push(key);
      return {
        key,
        outputType: "object" as const,
        fieldConfig: {
          fieldWrapper: node.wrapperType === "TabWrapper" ? "TabItemWrapper" : "FragmentWrapper",
          wrapperProps: {
            label: slot.label,
            ...(slot.className ? { className: slot.className } : {}),
          },
        },
        fields: slot.children.map((c) =>
          c._kind === "container" ? containerToIField(c) : fieldToIField(c)
        ),
      };
    });
  } else if (node.columns) {
    // TableWrapper / OdataWrapper: one row-template child + slot fields.
    // SA-1053 bug M: emit the row template UNCONDITIONALLY (even with 0 columns) so
    // `fields[0]` is always the row template — the positional contract the runtime
    // (use-column-visibility reads `fields[0].fields`) and the importer both rely on.
    iField.fields = [{
      key: `${node.key}_row`,
      outputType: "object" as const,
      fieldConfig: { fieldWrapper: "FragmentWrapper", wrapperProps: {} },
      fields: node.columns.map(fieldToIField),
    }];
    // Add slot fields (like filter button) from container.children
    if (node.children?.length) {
      iField.fields = [...iField.fields, ...node.children.map(fieldToIField)];
    }
  } else if (node.arrays) {
    // Array: one child object wrapping the fields
    iField.fields = [{
      key: `${node.key}_row`,
      outputType: "object" as const,
      fieldConfig: { fieldWrapper: "FragmentWrapper", wrapperProps: {} },
      fields: node.arrays.map(fieldToIField),
    }];
  } else {
    // DivWrapper / FragmentWrapper: flat children
    iField.fields = node.children.map(fieldToIField);
  }

  return iField;
}

export function toISchema(nodes: DesignerNode[]): ISchema {
  return {
    fields: nodes.map((n) =>
      n._kind === "field" ? fieldToIField(n) : containerToIField(n)
    ),
  };
}

// ─── Import from ISchema ──────────────────────────────────────────────────────

export function fromISchema(schema: ISchema): DesignerNode[] {
  return (schema.fields ?? []).map(importField);
}

function importField(f: IField): DesignerNode {
  const isContainer = !!f.fields?.length || [
    "TableWrapper", "OdataWrapper", "TabWrapper", "SequenceWrapper", "DivWrapper", "FragmentWrapper",
  ].includes(f.fieldConfig?.fieldWrapper ?? "");

  if (!isContainer) {
    return {
      _id: uid(),
      _kind: "field",
      key: f.key,
      outputType: f.outputType,
      default: f.default,
      fieldConfig: {
        ...f.fieldConfig,
        wrapperProps: { ...(f.fieldConfig?.wrapperProps ?? {}) },
        controlProps: { ...(f.fieldConfig?.controlProps ?? {}) },
        rules: [...(f.fieldConfig?.rules ?? [])],
        condition: f.fieldConfig?.condition ? { ...f.fieldConfig.condition } : null,
      },
    };
  }

  const wrapperType = f.fieldConfig?.fieldWrapper ?? "DivWrapper";
  const c: DesignerContainer = {
    _id: uid(),
    _kind: "container",
    wrapperType: wrapperType,
    key: f.key,
    outputType: f.outputType,
    ...(f.default !== undefined ? { default: f.default } : {}),
    fieldConfig: {
      fieldWrapper: wrapperType,
      wrapperProps: { ...(f.fieldConfig?.wrapperProps ?? {}) },
      controlProps: { ...(f.fieldConfig?.controlProps ?? {}) },
      rules: [...(f.fieldConfig?.rules ?? [])],
      condition: f.fieldConfig?.condition ? { ...f.fieldConfig.condition } : null,
    },
    children: [],
  };

  if (wrapperType === "TabWrapper" || wrapperType === "SequenceWrapper") {
    c.slots = (f.fields ?? []).map((slot) => ({
      _id: uid(),
      // SA-1053 bug K: preserve the slot's stable data key across the round-trip, so a
      // hand-authored `key: "general"` does not come back out as `general_data`.
      key: slot.key,
      label: slot.fieldConfig?.wrapperProps?.label ?? slot.key,
      className: slot.fieldConfig?.wrapperProps?.className as string | undefined,
      children: (slot.fields ?? []).map(importField),
    }));
  } else if (wrapperType === "TableWrapper" || wrapperType === "OdataWrapper") {
    // fields[0] = row template (its children are the columns); fields[1..] = slot fields.
    // SA-1053 bug M: restore BOTH. The old code restored only columns, silently dropping
    // the filter / more-action / row-more-action slot fields on every import round-trip.
    const [rowTemplate, ...slotFields] = f.fields ?? [];
    c.columns = (rowTemplate?.fields ?? []).map(importField).filter((n) => n._kind === "field") as DesignerField[];
    c.children = slotFields.map(importField).filter((n) => n._kind === "field") as DesignerField[];
  } else if (f.outputType === "array") {
    c.arrays = (f.fields?.[0]?.fields ?? []).map(importField).filter(n => n._kind === "field");
  } else {
    c.children = (f.fields ?? []).map(importField).filter(n => n._kind === "field");
  }

  return c;
}