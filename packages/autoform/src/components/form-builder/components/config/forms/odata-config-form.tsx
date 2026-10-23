import type React from "react";
import { useId } from "react";

import { Input } from "@ldc/ui/components/input";

import { resolveContainerWrapperPropSchema } from "../../../constants/prop-keys";
import type { DesignerContainer, DesignerNode } from "../../../types";
import { ContainerDefaultField } from "../sections/container-default-field";
import { PropSchemaFields } from "../sections/prop-schema-fields";
import { RawPropsEditor } from "../sections/raw-props-editor";
import { RulesEditor } from "../sections/rules-editor";
import { CollapsibleSection } from "../shared/collapsible-section";
import { Section } from "../shared/panel-primitives";
import type { RuleEntry } from "../shared/prop-value-utils";
import { coerceRuleValue, ruleTakesValue } from "../../../utils/rules-utils";

/**
 * `actions` is a large JSON DSL (`odata-wrapper` hands it straight to the action runner).
 * No typed field models it, so it lives in Advanced — but it is pinned to `json` there:
 * it is the one prop real production OData tables genuinely depend on, and flipping it to
 * `string` would corrupt it. Same pin the original container grid applied via its
 * `autoJsonKeys`.
 */
const AUTO_JSON_KEYS = ["actions"];

/**
 * What the new-key autocomplete offers. Distinct from `AUTO_JSON_KEYS` on purpose: one is
 * "this key is ALWAYS json", the other is "this key is worth suggesting". They happen to
 * hold the same single entry today, and sharing a const would mean a future addition to
 * either list silently changed the other.
 */
const RAW_KEY_SUGGESTIONS = ["actions"];

/**
 * `label` is the node's DISPLAY NAME on the canvas (`canvas/container-node.tsx:75` and
 * `dnd-provider.tsx:363` both render `wrapperProps?.label ?? key`) — neither offers an
 * inline rename, so this panel is where it is edited. It gets its own first-class input in
 * the Table section, right under Key, which is why it counts as COVERED: covered means "a
 * typed control owns it", so it is not ALSO offered as a raw row. `RawPropsEditor` carries
 * it through on every flush (subject to the precedence note on `OdataConfigForm` below).
 */
const TYPED_NON_SCHEMA_KEYS = ["label"];

/** node.fieldConfig.rules → RuleEntry rows for the RulesEditor (mirrors field-config-form). */
function toRuleEntries(node: DesignerNode): RuleEntry[] {
    return (node.fieldConfig?.rules ?? []).map((r) => ({
        method: r.method,
        value: r.value === undefined || r.value === null ? "" : String(r.value),
        message: r.message ?? "",
    }));
}

export interface OdataConfigFormProps {
    node: DesignerContainer;
    updateNode: (id: string, fn: (n: DesignerNode) => DesignerNode) => void;
}

/**
 * The typed config panel for an OData table.
 *
 *   Table        — the node's key and canvas label, plus a read-only note about where the
 *                  data comes from.
 *   Capabilities — the schema's flags, rendered by the generic `PropSchemaFields`.
 *   Advanced     — `RawPropsEditor` over everything the typed sections do NOT cover.
 *
 * There is deliberately NO endpoint field. An OData table resolves its source at RUNTIME
 * from the form itself: `odata-wrapper.tsx:50-52` watches `__<key>_file_id` and
 * `:131` builds `odata/{fileId}/data` from it. A user-typed endpoint is dead in every
 * document that has an uploaded file, so the panel EXPLAINS the source rather than asking
 * for it. (A legacy `endpoint` in an old document is still read as a fallback and still
 * editable — under Advanced, where it belongs.)
 *
 * Both sections write through the SAME `flush`, which replaces `wrapperProps` wholesale
 * with an object built from the CURRENT props. `PropSchemaFields` spreads the props it did
 * not touch; `RawPropsEditor` (`raw-props-editor.tsx:193-200`) copies the covered keys out
 * of `value` before writing its rows. So neither section DROPS the other's keys.
 *
 * What is NOT guaranteed is precedence: `RawPropsEditor` writes the carried-over covered
 * keys FIRST and its rows second, so a raw row explicitly (re)named to a covered key —
 * `isAdd`, say — overwrites the typed value with the row's own. That is the same rule the
 * grid this replaces used ("what is in the grid is what the document gets"), and it is out
 * of reach in practice: covered keys are filtered out of the rows on seed and the key
 * autocomplete never suggests one, so a user has to type the collision by hand.
 */
export function OdataConfigForm({ node, updateNode }: OdataConfigFormProps) {
    const uid = useId();
    const keyId = `${uid}-key`;
    const labelId = `${uid}-label`;

    const schema = resolveContainerWrapperPropSchema("OdataWrapper");
    const wrapperProps = node.fieldConfig.wrapperProps ?? {};

    const flush = (next: Record<string, unknown>) =>
        updateNode(node._id, (n) => ({ ...n, fieldConfig: { ...n.fieldConfig, wrapperProps: next } }));

    const setKey = (key: string) => updateNode(node._id, (n) => ({ ...n, key }));

    const setDefault = (next: unknown) => updateNode(node._id, (n) => ({ ...n, default: next }));

    // Coerce RuleEntry rows back to node.fieldConfig.rules (mirrors field-config-actions.flushRules).
    const flushRules = (entries: RuleEntry[]) =>
        updateNode(node._id, (n) => ({
            ...n,
            fieldConfig: {
                ...n.fieldConfig,
                rules: entries
                    .filter((e) => e.method)
                    .map((e) => ({
                        method: e.method,
                        ...(ruleTakesValue(e.method) && e.value !== "" ? { value: coerceRuleValue(e.method, e.value) } : {}),
                        ...(e.message ? { message: e.message } : {}),
                    })),
            },
        }));

    /**
     * An EMPTY label deletes the key rather than storing `""`: the canvas renders
     * `wrapperProps?.label ?? container.key`, and an empty string is not nullish — it would
     * survive the `??` and leave the node nameless. Absent means "fall back to the key".
     */
    const setLabel = (label: string) => {
        const next = { ...wrapperProps };
        if (label === "") delete next.label;
        else next.label = label;
        flush(next);
    };

    const coveredKeys = [...schema.map((p) => p.key), ...TYPED_NON_SCHEMA_KEYS];
    const hiddenCount = Object.keys(wrapperProps).filter((k) => !coveredKeys.includes(k)).length;

    return (
        <div className="py-2 space-y-0">
            <Section title="Table" />
            <div className="flex flex-col gap-1 px-3 mb-2">
                <label className="text-[10px] font-medium text-muted-foreground" htmlFor={keyId}>
                    Key
                </label>
                <Input
                    id={keyId}
                    className="h-8 text-xs!"
                    placeholder="e.g. tblProcess"
                    value={node.key ?? ""}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setKey(e.target.value)}
                />
            </div>

            <div className="flex flex-col gap-1 px-3 mb-2">
                <label className="text-[10px] font-medium text-muted-foreground" htmlFor={labelId}>
                    Label
                </label>
                <Input
                    id={labelId}
                    className="h-8 text-xs!"
                    // The key is what the canvas shows when there is no label, so it is the
                    // honest placeholder for "what this node will be called".
                    placeholder={node.key ?? "Display name"}
                    // A hand-authored document could hold a non-string here; only a
                    // string/number has a sane single-line rendering ("[object Object]" is not
                    // an editable value).
                    value={
                        typeof wrapperProps.label === "string" || typeof wrapperProps.label === "number"
                            ? String(wrapperProps.label)
                            : ""
                    }
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setLabel(e.target.value)}
                />
                <p className="text-[10px] text-muted-foreground/80">
                    The table's name on the canvas. Empty falls back to the key.
                </p>
            </div>

            <div className="px-3 mb-2">
                <div className="rounded-md border border-border bg-muted/30 p-2.5">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                        Data source
                    </p>
                    <p className="text-xs text-muted-foreground leading-relaxed">
                        Resolved from the form at runtime:{" "}
                        <code className="text-foreground">{`__${node.key ?? ""}_file_id`}</code> →{" "}
                        <code className="text-foreground">odata/&#123;fileId&#125;/data</code>
                    </p>
                    <p className="text-[10px] text-muted-foreground/80 mt-1">
                        Upload the file in the form; there is nothing to configure here.
                    </p>
                </div>
            </div>

            <Section title="Capabilities" />
            <PropSchemaFields schema={schema} value={wrapperProps} onChange={flush} />

            <Section title="Default value" />
            <ContainerDefaultField
                key={node._id}
                value={node.default}
                onChange={setDefault}
                placeholder="[ ]"
            />

            {/* Validation — rules for the OData field value (e.g. a refine that blocks
                submit while a row carries a validation error). */}
            <RulesEditor nodeId={node._id} rules={toRuleEntries(node)} onChange={flushRules} />

            <CollapsibleSection
                label="Advanced (raw props)"
                count={hiddenCount > 0 ? hiddenCount : undefined}
                countTestId="advanced-hidden-count"
            >
                <RawPropsEditor
                    nodeId={node._id}
                    value={wrapperProps}
                    coveredKeys={coveredKeys}
                    suggestions={RAW_KEY_SUGGESTIONS}
                    autoJsonKeys={AUTO_JSON_KEYS}
                    onChange={flush}
                />
            </CollapsibleSection>
        </div>
    );
}

export default OdataConfigForm;
