import { BaseNode, WorkflowEditorHandle } from "@common/components/ldc-workflow-editor";
import { RefObject, useCallback } from "react";
import { useEditorStore } from "../stores/editor-stores";

const useSelectNode = (editorRef: RefObject<WorkflowEditorHandle | null>) => {
    const setSelectedNode = useEditorStore((s) => s.setSelectedNode);
    const setPredecessorNodes = useEditorStore((s) => s.setPredecessorNodes);
    const setOutgoerNodes = useEditorStore((s) => s.setOutgoerNodes);
    const setIncomerNodes = useEditorStore((s) => s.setIncomerNodes);
    
    const onSelectedNode = useCallback((node: BaseNode) => {
        setSelectedNode(node ?? null);
        setPredecessorNodes(editorRef.current?.getPredecessorNodes(node.id));
        setIncomerNodes(editorRef.current?.getIncomerNodes(node.id));
        setOutgoerNodes(editorRef.current?.getOutGoerNodes(node.id));
    }, []);

    return {
        onSelectedNode
    }
}

export default useSelectNode;