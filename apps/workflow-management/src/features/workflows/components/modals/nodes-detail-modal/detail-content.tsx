import { ResizablePanel, ResizablePanelGroup } from "@ldc/ui/components/resizable"
import NodeDetailForm from "./detail-form"
import NodeDetailInput from "./detail-input"
import NodeDetailOutput from "./detail-output"
import IncomerNodes from "./incomer-nodes"
import OutcomerNodes from "./outcomer-nodes"

const NodesPopupContent = () => {

    return (
        <>
            <ResizablePanelGroup className="[&>div[data-panel]]:h-full! [&>div[data-panel]]:w-full! [&>div[data-panel]]:overflow-hidden!">
                <ResizablePanel minSize={300} className="border-r border-gray-200 h-full w-full overflow-hidden!">
                    <NodeDetailInput />
                </ResizablePanel>
                <ResizablePanel minSize={500} defaultSize={800} className="border-r border-gray-200  h-full w-full overflow-hidden!">
                    <NodeDetailForm />
                </ResizablePanel>
                <ResizablePanel minSize={300} className="h-full w-full overflow-hidden!">
                    <NodeDetailOutput />
                </ResizablePanel>
            </ResizablePanelGroup>

            <IncomerNodes />
            <OutcomerNodes />
        </>
    )
}

export default NodesPopupContent