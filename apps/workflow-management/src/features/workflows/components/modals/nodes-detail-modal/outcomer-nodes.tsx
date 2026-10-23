import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { Button } from "@ldc/ui/components/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { useNodeDetailContext } from "./node-detail-provider";

const OutcomerNodes = () => {
    const outcomerNodes = useEditorStore(s => s.outgoerNodes);


    const { onSelectNode } = useNodeDetailContext()

    return (
        <TooltipProvider>
            <section className="absolute top-0 -right-6 w-12 h-full flex flex-col items-center justify-evenly gap-4 py-12">
                {outcomerNodes.map(node => (
                    <Tooltip key={node.id}>
                        <TooltipTrigger>
                            <Button
                                variant="outline"
                                size="icon"
                                className="shadow border border-gray-200"
                                onClick={() => {
                                    onSelectNode(node)
                                }}
                            >
                                <DynamicNodeIcon name={node.original.icon ?? ""} color={node.original.color} className="size-5" />
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">
                            <p>{node?.original?.title}</p>
                        </TooltipContent>
                    </Tooltip>
                ))}
            </section>
        </TooltipProvider>
    )
}

export default OutcomerNodes;