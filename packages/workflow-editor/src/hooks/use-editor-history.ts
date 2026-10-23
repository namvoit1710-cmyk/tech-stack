import { useCallback, useEffect, useState } from "react";
import type { IEditorInstance } from "../components/rete-editor";
import type { HistoryDataType, HistoryType } from "../components/rete-editor/types";

const trackedEvents = [
    "nodecreated",
    "noderemoved",
    "connectioncreated",
    "connectionremoved",
] as const

const useEditorHistory = (editorInstance: IEditorInstance | undefined) => {

    const [canUndo, setCanUndo] = useState<boolean>(false);
    const [canRedo, setCanRedo] = useState<boolean>(false);

    const handleUpdateCanUndoAndRedo = useCallback((history: HistoryType) => {
        if (!history) return;

        const historyData = (history as unknown as HistoryDataType).history ?? {
            produced: [],
            reserved: [],
        };

        const canUndo = historyData.produced.length > 0;
        const canRedo = historyData.reserved.length > 0;

        setCanUndo(canUndo);
        setCanRedo(canRedo);
    }, [editorInstance])

    useEffect(() => {
        if (!editorInstance) return;

        const { history, editor, area } = editorInstance ?? {};
        if (!history) return;

        editor.addPipe((context) => {
            if ((trackedEvents as readonly string[]).includes(context.type)) {
                setTimeout(() => {
                    handleUpdateCanUndoAndRedo(history)
                }, 0)
            }
            return context;
        });

        area.addPipe((context) => {
            if (context.type === "nodetranslated") {
                setTimeout(() => {
                    handleUpdateCanUndoAndRedo(history)
                }, 0)
            }

            return context;
        });
    }, [editorInstance])

    return {
        canRedo,
        canUndo,
    }
}

export default useEditorHistory;