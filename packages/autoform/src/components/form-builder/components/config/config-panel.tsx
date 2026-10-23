import { useDesignerCtx } from "../../hooks/use-designer-context";
import type { DesignerContainer } from "../../types";
import { FieldConfigForm } from "./forms/field-config-form";
import { OdataConfigForm } from "./forms/odata-config-form";
import { TabsConfigForm } from "./forms/tabs-config-form";
import { PanelHeader } from "./shared/panel-primitives";

/**
 * The config panel is a DISPATCHER: it picks the form for the selected node and renders it.
 * Every form owns its own hooks, so the panel itself holds none beyond `useDesignerCtx` —
 * which it calls ONCE, at the top, before any branch.
 *
 * That ordering is the point. The old `NodeConfigForm` opened with two container
 * early-returns and only then called `useDesignerCtx` / `useForm` / `useFieldArray` /
 * `useEffect`. It never crashed because the container branch happened to call zero stateful
 * hooks, so React fell back to the mount dispatcher — it survived on luck, not on rules.
 * Splitting the branches into components retires that hazard structurally.
 */
export function ConfigPanel() {
    const { selectedNode, updateNode, updateSlotLabel, updateSlotClassName, addSlot, removeSlot } =
        useDesignerCtx();

    if (!selectedNode) {
        return (
            <div className="flex flex-col h-full bg-background">
                <PanelHeader>Field config</PanelHeader>
                <div className="flex-1 flex items-center justify-center text-xs! text-muted-foreground">
                    Select a node to configure
                </div>
            </div>
        );
    }

    const isContainer = selectedNode._kind === "container";
    const wrapperType = selectedNode._kind === "container" ? selectedNode.wrapperType : null;

    const title = isContainer
        ? `${wrapperType?.replace("Wrapper", "")} wrapper`
        : `${selectedNode.fieldConfig.fieldControl}`;

    return (
        <div className="flex flex-col h-full bg-background overflow-hidden">
            <PanelHeader>{title}</PanelHeader>
            <div className="flex-1 overflow-y-auto">
                {wrapperType === "OdataWrapper" ? (
                    <OdataConfigForm node={selectedNode as DesignerContainer} updateNode={updateNode} />
                ) : wrapperType === "TabWrapper" ? (
                    <TabsConfigForm
                        node={selectedNode as DesignerContainer}
                        updateNode={updateNode}
                        updateSlotLabel={updateSlotLabel}
                        updateSlotClassName={updateSlotClassName}
                        addSlot={addSlot}
                        removeSlot={removeSlot}
                    />
                ) : (
                    <FieldConfigForm node={selectedNode} updateNode={updateNode} />
                )}
            </div>
        </div>
    );
}
