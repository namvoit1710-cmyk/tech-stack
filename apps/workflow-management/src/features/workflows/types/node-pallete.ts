import type { IField } from "@ldc/autoform";

export interface IGroupNodePallete {
    _id: string;
    name: string;
    description: string;
    icon: string;
}

export interface IFieldConfig {
    label: string;
    description: string;
    fieldControl: string;
    fieldWrapper?: string;
    controlProps?: Record<string, any>;
}

export interface INodeInputSchema {
    key: string;
    outputType: string;
    fieldConfig: IFieldConfig;
    rules?: {
        isRequired?: boolean;
        [key: string]: any;
    };
    default?: any;
    show_when?: {
        field: string;
        operator: string;
        value: any;
    };
}

export interface IWorkerPortDetail {
    id: string;
    label: string;
    required: boolean;
    description: string;
    data_type?: string;
}

export interface INodePorts {
    in: IWorkerPortDetail[];
    out: IWorkerPortDetail[];
}

export interface INodeTypeInfo {
    node_type: string;
    node_class: string;
    icon: string;
    color: string;
    ports: INodePorts;
}

export interface INodePalleteResponse {
    data: {
        categories: Record<string, INodePallete[]>;
        total: number;
        node_types: INodeTypeInfo[];
    },
    ok: boolean
}

export interface INodeCatalogPort {
    id: string;
    label: string;
}

export interface INodeCatalogPorts {
    inputs: INodeCatalogPort[];
    outputs: INodeCatalogPort[];
}

/**
 * SA-1930/SA-1734: what a node definition IS — the BE already types this field as
 * `NodeProviderKind` (layer2_application/contracts/node_definition_dto.py). Typing it
 * here too (instead of `string`) makes `bandOfCollection`'s `Set<NodeProvider>` reject a
 * typo like "wroker" at compile time instead of silently always-false at runtime.
 */
export type NodeProvider = "builtin" | "worker" | "business";

/** SA-1734: the functional-kind vocabulary. Mirrors NodeKind(str, Enum) in the BE
 *  (control-plane, executor and worker-sdk all define it — the wire carries the string). */
export type NodeKind = "trigger" | "action" | "read" | "logic" | "human" | "transform" | "util";

export interface INodePallete {
    id: string;
    name: string;
    description: string;
    provider: NodeProvider;
    node_type: string;
    category: string;
    /** SA-1734: functional kind — trigger|action|read|logic|human|transform|util. */
    kind: NodeKind;
    /** SA-1734: browse axis — "Core" | the connector ("Jira") | a user collection. */
    collection: string;
    icon: string;
    color: string;
    tags: string[];
    spec_version: string;
    visibility: string;
    runtime: string;
    ports: INodeCatalogPorts;
}

/** SA-1930: origin band, derived from the collection's `order` (see palette-taxonomy.bandOf). */
export type PaletteBandId = "core" | "connector" | "mine";

export interface ICategoryMetadata {
    label: string;
    order: number;
}

export interface INodeCatalogResponse {
    status: string;
    data: {
        definitions: INodePallete[];
        category_tree: Record<string, string[]>;
        categories: Record<string, ICategoryMetadata>;
        generated_at: string;
        total: number;
    };
}

export interface INodeCapability {
    domain: string;
    action: string;
}

export interface INodeCatalogDetail extends Omit<INodePallete, "ports"> {
    ports: INodeCatalogPorts;
    input_schema: IField[];
    output_schema: IField[];
    capabilities: INodeCapability[];
    worker_type: string | null;
    base_worker_id: string | null;
    base_node_type: string | null;
    parent_definition_id: string | null;
    locked_defaults: Record<string, any>;
    input_mapping: Record<string, any>;
    output_mapping: Record<string, any>;
    data_mode: string;
    edit_mode: string;
    // T7: credential affinity exposed by the control plane (Part B T6). When present,
    // the node-config drawer injects a CredentialPickerControl for `credential_ref`.
    supported_credential_kinds?: string[];
    credential_provider?: string | null;
}

export interface INodeCatalogDetailResponse {
    status: string;
    data: INodeCatalogDetail;
}

export interface IMenuItem {
    icon?: string
    text: string
    /** SA-1930: the palette tree is IPaletteBand/-Collection/-Section now; a menu item is only ever a leaf. */
    type: "Node"
    original?: INodePallete
}

/**
 * SA-1930. One kind section (a connector's ACTIONS) or one Core bucket (Flow & Logic).
 * `label` is the BE's category label; `labelKey` (Core buckets only) is an i18n key the
 * component resolves — the hook stays i18n-free so it is testable without a provider.
 */
export interface IPaletteSection {
    key: string;
    label: string;
    labelKey?: string;
    /** SA-1734: the section's kind, for tinting/labeling. NOT typed `NodeKind` — this is
     *  built FE-side from an arbitrary wire string (a category-tree leaf-key suffix, or a
     *  Core bucket's representative kind), and Never-Worse requires it to still carry a
     *  kind the BE hasn't been taught the union for yet (see palette-taxonomy CORE_BUCKETS/
     *  kindOf). Forcing `NodeKind` here would need an `as NodeKind` lie at every call site
     *  that builds a section — see use-merge-nodes.ts. */
    kind: string;
    icon?: string;
    count: number;
    items: IMenuItem[];
    /** "Condition · Loop · Merge · Switch" — Core buckets only. */
    preview?: string;
}

export interface IPaletteCollection {
    key: string;
    label: string;
    /** Presentation copy from the FE VENDOR_BLURB map; absent for most connectors. */
    vendor?: string;
    icon?: string;
    count: number;
    sections: IPaletteSection[];
}

export interface IPaletteBand {
    id: PaletteBandId;
    collections: IPaletteCollection[];
}

