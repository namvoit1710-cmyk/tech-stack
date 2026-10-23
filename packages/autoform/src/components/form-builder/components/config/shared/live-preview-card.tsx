import { Component, memo, type ReactNode, useDeferredValue, useMemo } from "react";

import Builder from "../../../../builder/builder";
import type { DesignerNode } from "../../../types";
import { toISchema } from "../../../utils/nodes";

/**
 * Catches render errors from the previewed control so a control with a rendering bug (e.g. the
 * known @ldc/ui Upload hooks issue) can't take down the whole inspector. Keyed on the node id
 * by the caller, so it remounts (and clears any error) when a different node is selected.
 */
class PreviewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() {
        return { failed: true };
    }
    render() {
        if (this.state.failed) {
            return <p className="text-xs text-muted-foreground">Preview unavailable for this control.</p>;
        }
        return this.props.children;
    }
}

/**
 * A LIVE preview of the selected field: renders the real control via {@link Builder}, driven by
 * the node's CURRENT config, so edits (placeholder, label, default value, wrapper, …) are
 * reflected as they are made. This replaces the earlier static switch/text approximation, which
 * ignored control props like `placeholder` and never updated on edit.
 *
 * PERF (SA-2043): `Builder` is the full form runtime (builds a zod schema, mounts react-hook-form,
 * renders the field tree), and it re-ran on EVERY keystroke because `toISchema([node])` handed it a
 * fresh `schema.fields` reference each render, busting Builder's internal memos. We now defer the
 * node with `useDeferredValue` and memoise the serialisation, so the heavy rebuild happens at low
 * priority once typing settles — the config inputs stay responsive. `memo` guards against unrelated
 * parent re-renders.
 */
export const LivePreviewCard = memo(function LivePreviewCard({ node }: { node: DesignerNode }) {
    const deferredNode = useDeferredValue(node);
    const schema = useMemo(() => toISchema([deferredNode]), [deferredNode]);
    return (
        <div className="border border-border rounded-lg p-3.5">
            <PreviewBoundary key={deferredNode._id}>
                <Builder schema={schema} />
            </PreviewBoundary>
        </div>
    );
});
