import { useEffect, useRef } from "react";

import { BuilderProvider, useOptionalBuilderContext } from "../../../contexts/builder.context";
import type { BuilderServices } from "../../../hooks/use-builder-services";
import type { ISchema } from "../../../types/schema";
import { DesignerProvider } from "../hooks/use-designer-context";
import { useFormBuilder } from "../hooks/use-form-builder";
import { useContainerNarrow } from "../hooks/use-inspector-breakpoint";
import { PaletteTriggerProvider } from "../hooks/use-palette-trigger";
import { BuilderToolbar } from "./builder-toolbar";
import { FormCanvas } from "./canvas/form-canvas";
import { CommandPalette } from "./command-palette";
import { DesignerDragOverlay, DndProvider } from "./dnd-provider";
import { InspectorShell } from "./inspector-shell";
import { PreviewCanvas } from "./preview-canvas";

export interface FormBuilderProps {
    value: ISchema;
    onChange: (schema: ISchema) => void;
    /**
     * SA-2184 — optional host-provided services (e.g. `useBuilderServices(SERVICE_CONFIGS)`)
     * so design-time authoring UI (the Gateway App/Function pickers in the Choice-control
     * config panel) can fetch live data. Omit it when the builder is already rendered
     * inside a host `BuilderProvider` (the workflow node-config panel, the chat form
     * tool): the services on that context are inherited automatically.
     */
    services?: BuilderServices;
}

export function FormBuilder({ value, onChange, services }: FormBuilderProps) {
    // The builder installs its own BuilderProvider (below) for design-time state.
    // That provider would otherwise SHADOW a host provider that already carries
    // `services` — which is exactly how every real caller reaches the builder
    // (`FormBuilderControl` inside a node-config form, `FormDesignPopup`). Inherit
    // instead of shadowing, so the Gateway pickers can fetch without every call
    // site being re-plumbed.
    const hostContext = useOptionalBuilderContext();
    const resolvedServices = services ?? hostContext?.services;

    const {
        designer,
        activeId,
        overId,
        setActiveId,
        setOverId,
        view,
        setView,
        palette,
        setPaletteOpen,
        openPalette,
        handleContextMenu,
    } = useFormBuilder(value, onChange);

    const hasSelection = !!designer.selectedNode;

    const rootRef = useRef<HTMLDivElement>(null);
    const isNarrow = useContainerNarrow(rootRef);

    useEffect(() => {
        if (!hasSelection) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") designer.selectNode(null);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [hasSelection, designer]);

    return (
        <BuilderProvider value={{ schema: value, fieldComponent: {}, wrapperComponent: {}, services: resolvedServices }}>
            <DesignerProvider value={designer}>
                <PaletteTriggerProvider value={openPalette}>
                    <DndProvider onActiveId={setActiveId} onOverId={setOverId}>
                        <div
                            ref={rootRef}
                            className="flex flex-col overflow-hidden bg-background text-foreground h-full w-full"
                        >
                            <BuilderToolbar
                                view={view}
                                setView={setView}
                            />

                            <div className="flex-1 overflow-hidden" onContextMenu={handleContextMenu}>
                                {view === "builder" ? (
                                    <div className="relative flex h-full overflow-hidden">
                                        <div className="flex-1 min-w-0 overflow-hidden">
                                            <FormCanvas activeId={activeId} overId={overId} />
                                        </div>
                                        <InspectorShell isNarrow={isNarrow} />
                                    </div>
                                ) : (
                                    <PreviewCanvas />
                                )}
                            </div>
                        </div>

                        <CommandPalette state={palette} onOpenChange={setPaletteOpen} />

                        <DesignerDragOverlay activeId={activeId} nodes={designer.nodes} />
                    </DndProvider>
                </PaletteTriggerProvider>
            </DesignerProvider>
        </BuilderProvider>
    );
}

export default FormBuilder;
