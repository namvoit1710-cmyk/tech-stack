import type {
  IFieldComponent,
  IFieldConfig as ISchemaFieldConfig,
  IWrapperComponent,
  OutputType,
} from "../../../types/schema";
import type { IConditionConfig } from "../../../types/condition";

/** Designer field config: schema config but condition may be explicitly null while editing. */
export type IFieldConfig = Omit<ISchemaFieldConfig, "condition"> & {
  condition?: IConditionConfig | null;
};

/** Kind of item in the designer tree */
export type DesignerNodeKind = "field" | "container";

/** A single field node (maps to one IField) */
export interface DesignerField {
  _id: string;
  _kind: "field";
  key: string;
  outputType: OutputType;
  default?: unknown;
  fieldConfig: IFieldConfig;
}

/** A tab slot inside TabWrapper / step inside SequenceWrapper */
export interface DesignerSlot {
  _id: string;
  /**
   * Stable data key, independent of the display label (SA-1053 bug K).
   * This is the key the slot serialises to; renaming the label must NOT change it.
   * Sanitised to a valid identifier and unique among sibling slots.
   */
  key: string;
  label: string;
  /** Per-tab className — round-trips to TabItemWrapper's wrapperProps.className. */
  className?: string;
  children: DesignerNode[]; // fields or containers inside each slot
}

/** A container wrapper node */
export interface DesignerContainer {
  _id: string;
  _kind: "container";
  wrapperType: IWrapperComponent;
  key: string;
  outputType: OutputType;
  /** Container-level default value (object for Tabs, array for OData); seeds the form. */
  default?: unknown;
  fieldConfig: IFieldConfig;
  /** flat children — for FormItemWrapper/DivWrapper/FragmentWrapper grouping */
  children: DesignerField[];
  /** slot children — for TabWrapper, SequenceWrapper */
  slots?: DesignerSlot[];
  /** column defs — for TableWrapper */
  columns?: DesignerField[];
  /** array defs — for FragmentWrapper */
  arrays?: DesignerField[];
}

export type DesignerNode = DesignerField | DesignerContainer;

// ─── Palette item types ────────────────────────────────────────────────────────

export interface PaletteFieldDef {
  kind: "field";
  type: IFieldComponent;
  label: string;
  category: string;
  defaultOutputType: OutputType;
  defaultWrapper: IWrapperComponent;
  defaultControlProps?: Record<string, unknown>;
}

export interface PaletteWrapperDef {
  kind: "wrapper";
  wrapperType?: IWrapperComponent;
  label: string;
  description: string;
  isContainer: boolean;        // true = can hold child fields
  containerMode?: "flat" | "slots" | "table"; // how children are structured
  defaultOutputType: OutputType;
}

export type PaletteDef = PaletteFieldDef | PaletteWrapperDef;

// ─── DnD drag data ─────────────────────────────────────────────────────────────

export type DragData =
  | { type: "palette-field"; def: PaletteFieldDef }
  | { type: "palette-wrapper"; def: PaletteWrapperDef }
  | { type: "node"; nodeId: string; parentId: string | null; index: number };