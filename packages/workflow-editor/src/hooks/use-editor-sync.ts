import type { RefObject } from "react";
import { useEffect, useRef } from "react";
import type { BaseNode } from "../components/rete-editor/nodes/base-node";
import type { IEditorInstance, IEditorValue } from "../components/rete-editor/types";

interface UseEditorSyncOptions {
    onChange?: (value: IEditorValue) => void;
    onNodeSelected?: (node: BaseNode) => void;
    onNodeAdded?: () => void;
    onConnectionAdded?: () => void;
}

export function useEditorSync(
    editorInstance: IEditorInstance | undefined,
    isLoadingRef: RefObject<boolean>,
    options: UseEditorSyncOptions,
) {
    const { onChange, onNodeSelected, onNodeAdded, onConnectionAdded } = options;

    useEffect(() => {
        if (!editorInstance) return;

        const { editor } = editorInstance ?? {};
        if (!editor) return;

        let active = true;
        const pipe = (context: any) => {
            if (!active || isLoadingRef.current) return context;

            if (
                ["nodecreated", "noderemoved", "connectionremoved"]
                    .includes(context.type)
            ) {
                onChange?.(editorInstance.serializeNodes());
            }

            if (context.type === "nodecreated") onNodeAdded?.();
            if (context.type === "connectioncreate") {
                if (editorInstance.checkDuplicateConnection(context.data)) {
                    return;
                }
            };

            if (context.type === "connectioncreated") {
                onConnectionAdded?.();
                onChange?.(editorInstance.serializeNodes());
            }

            return context;
        }

        editor.addPipe(pipe);

        return () => {
            active = false;
        };
    }, [editorInstance]);

    const translateDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (!editorInstance) return;

        const { area } = editorInstance ?? {};
        if (!area) return;

        let areaActive = true;
        area.addPipe((context) => {
            if (!areaActive) return context;

            if (context.type === "nodepicked") {
                onNodeSelected?.(editorInstance.getNodeById(context.data.id)!);

                const isOutsideViewPort = !editorInstance.isNodeInViewport(context.data.id);
                if (isOutsideViewPort) {
                    editorInstance.centerOnNode(context.data.id);
                }
            }

            if (context.type === "nodetranslated") {
                if (translateDebounceRef.current) {
                    clearTimeout(translateDebounceRef.current);
                }

                translateDebounceRef.current = setTimeout(() => {
                    onChange?.(editorInstance.serializeNodes());
                }, 300);
            }

            return context;
        });

        return () => {
            areaActive = false;
            if (translateDebounceRef.current) {
                clearTimeout(translateDebounceRef.current);
            }
        };
    }, [editorInstance]);
}
