import type React from "react";
import { Plus, X } from "lucide-react";

import { Input } from "@ldc/ui/components/input";

import { resolveContainerWrapperPropSchema } from "../../../constants/prop-keys";
import type { DesignerContainer, DesignerNode } from "../../../types";
import { ContainerDefaultField } from "../sections/container-default-field";
import { PropSchemaFields } from "../sections/prop-schema-fields";
import { RawPropsEditor } from "../sections/raw-props-editor";
import { CollapsibleSection } from "../shared/collapsible-section";
import { Section } from "../shared/panel-primitives";

export interface TabsConfigFormProps {
    node: DesignerContainer;
    updateNode: (id: string, fn: (n: DesignerNode) => DesignerNode) => void;
    updateSlotLabel: (containerId: string, slotId: string, label: string) => void;
    updateSlotClassName: (containerId: string, slotId: string, className: string) => void;
    addSlot: (containerId: string) => void;
    removeSlot: (containerId: string, slotId: string) => void;
}

/**
 * The typed config panel for a Tabs container.
 *
 *   Identity   — the node's key.
 *   Appearance — the TabWrapper schema (variant, orientation) via the generic
 *                `PropSchemaFields`, so they are typed selects, not free text.
 *   Tabs       — one row per slot: label + per-tab className (both write straight through
 *                the designer's slot mutators, mirroring the canvas), plus the derived data
 *                key shown read-only.
 *   Advanced   — `RawPropsEditor` over everything the typed sections do NOT cover.
 *
 * `className` is deliberately NOT a container control: `tab-wrapper.tsx` hardcodes the
 * container className and never reads `wrapperProps.className` (a legacy one would still
 * surface under Advanced, where it belongs).
 */
export function TabsConfigForm({
    node,
    updateNode,
    updateSlotLabel,
    updateSlotClassName,
    addSlot,
    removeSlot,
}: TabsConfigFormProps) {
    const schema = resolveContainerWrapperPropSchema("TabWrapper");
    const wrapperProps = node.fieldConfig.wrapperProps ?? {};
    const slots = node.slots ?? [];

    const flush = (next: Record<string, unknown>) =>
        updateNode(node._id, (n) => ({ ...n, fieldConfig: { ...n.fieldConfig, wrapperProps: next } }));

    const setDefault = (next: unknown) => updateNode(node._id, (n) => ({ ...n, default: next }));

    // `label` is the node's canvas name, owned elsewhere — kept out of Advanced like OData.
    const coveredKeys = [...schema.map((p) => p.key), "label"];

    return (
        <div className="py-2 space-y-0">
            <Section title="Identity" />
            <div className="flex flex-col gap-1 px-3 mb-2">
                <label className="text-[10px] font-medium text-muted-foreground" htmlFor="tabs-key">
                    Key
                </label>
                <Input
                    id="tabs-key"
                    className="h-8 text-xs!"
                    placeholder="e.g. details"
                    value={node.key ?? ""}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        updateNode(node._id, (n) => ({ ...n, key: e.target.value }))
                    }
                />
            </div>

            <Section title="Appearance" />
            <PropSchemaFields schema={schema} value={wrapperProps} onChange={flush} />

            <div className="flex items-center justify-between mt-3 pt-3 px-3 border-t border-border">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
                    Tabs
                </p>
                <button
                    type="button"
                    onClick={() => addSlot(node._id)}
                    className="flex items-center gap-0.5 text-[10px] text-primary hover:underline"
                >
                    <Plus className="w-2.5 h-2.5" /> Add
                </button>
            </div>

            <div className="px-3 mt-1.5 space-y-2">
                {slots.map((slot) => (
                    <div
                        key={slot._id}
                        className="rounded-md border border-border/60 bg-muted/20 p-2 space-y-1"
                    >
                        <div className="flex items-center gap-1">
                            <Input
                                aria-label={`label for ${slot.label}`}
                                className="h-8 text-xs! flex-1"
                                placeholder="Tab label"
                                value={slot.label}
                                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                    updateSlotLabel(node._id, slot._id, e.target.value)
                                }
                            />
                            {slots.length > 1 && (
                                <button
                                    type="button"
                                    aria-label={`remove ${slot.label}`}
                                    onClick={() => removeSlot(node._id, slot._id)}
                                    className="p-1 text-muted-foreground hover:text-destructive shrink-0"
                                >
                                    <X className="w-3 h-3" />
                                </button>
                            )}
                        </div>

                        <Input
                            aria-label={`className for ${slot.label}`}
                            className="h-8 text-xs!"
                            placeholder="className (optional)"
                            value={slot.className ?? ""}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                                updateSlotClassName(node._id, slot._id, e.target.value)
                            }
                        />

                        <Input
                            aria-label={`key for ${slot.label}`}
                            readOnly
                            className="h-8 text-[10px] text-muted-foreground bg-muted/40"
                            value={slot.key}
                        />
                    </div>
                ))}
                <p className="text-[10px] text-muted-foreground/80">
                    The data key is stable — renaming a tab does not change it.
                </p>
            </div>

            <Section title="Default value" />
            <ContainerDefaultField
                key={node._id}
                value={node.default}
                onChange={setDefault}
                placeholder="{ }"
            />

            <CollapsibleSection label="Advanced (raw props)">
                <RawPropsEditor
                    nodeId={node._id}
                    value={wrapperProps}
                    coveredKeys={coveredKeys}
                    suggestions={[]}
                    onChange={flush}
                />
            </CollapsibleSection>
        </div>
    );
}

export default TabsConfigForm;
