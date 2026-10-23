import type { IScopedVariable } from "@/features/workflows/types/node-detail"
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import ArtifactItem from "./artifact-item"

const downloadArtifactMock = rs.fn()
const isDownloadingMock = rs.fn().mockReturnValue(false)

rs.mock("@/hooks/use-language", () => ({
    useLanguage: () => ({
        t: (key: string) => key,
    }),
}))

rs.mock("@/components/json-view/json-view", () => ({
    default: ({ draggableKeys }: { draggableKeys?: boolean }) => (
        <div data-testid="json-view" data-draggable={String(!!draggableKeys)} />
    ),
}))

rs.mock("@/features/workflows/hooks/use-download-artifact", () => ({
    useDownloadArtifact: () => ({
        downloadArtifact: downloadArtifactMock,
        downloadingId: null,
        isDownloading: isDownloadingMock,
    }),
}))

const artifactPaths: IScopedVariable["paths"] = {
    artifact: {
        kind: "file_ref",
        outputType: "object",
        displayPath: "artifact",
        sample: {
            file_id: "file-1",
            filename: "report.csv",
            content_type: "text/csv",
            size_bytes: 12,
            produced_by_node: "node",
            produced_by_task: "task",
            registered_at: "2026-06-01T00:00:00Z",
        },
    },
    preview: {
        kind: "sample",
        outputType: "object",
        displayPath: "preview",
        sample: { total: 2 },
    },
}

describe("ArtifactItem", () => {
    beforeEach(() => {
        downloadArtifactMock.mockClear()
        isDownloadingMock.mockReset()
        isDownloadingMock.mockReturnValue(false)
    })

    afterEach(() => {
        cleanup()
    })

    it("passes draggable preview keys through for input mode", () => {
        render(<ArtifactItem paths={artifactPaths} enableDragKey />)

        fireEvent.click(screen.getByTestId("artifact-preview-toggle"))

        expect(screen.getByTestId("json-view")).toHaveAttribute("data-draggable", "true")
    })

    it("disables draggable preview keys for output mode", () => {
        render(<ArtifactItem paths={artifactPaths} enableDragKey={false} />)

        fireEvent.click(screen.getByTestId("artifact-preview-toggle"))

        expect(screen.getByTestId("json-view")).toHaveAttribute("data-draggable", "false")
    })

    it("delegates file_ref downloads to the shared hook", () => {
        render(<ArtifactItem paths={artifactPaths} enableDragKey />)

        fireEvent.click(screen.getByTestId("artifact-download-button"))

        expect(downloadArtifactMock).toHaveBeenCalledTimes(1)
        expect(downloadArtifactMock).toHaveBeenCalledWith({
            fileId: "file-1",
            filename: "report.csv",
        })
    })

    it("renders preview data without a download action when no file_ref is present", () => {
        render(
            <ArtifactItem
                paths={{
                    preview: {
                        kind: "sample",
                        outputType: "object",
                        displayPath: "preview",
                        sample: { total: 2 },
                    },
                }}
            />,
        )

        expect(screen.getByTestId("json-view")).toBeInTheDocument()
        expect(screen.queryByTestId("artifact-download-button")).toBeNull()
        expect(screen.queryByText("nodes.nodes_popup_no_artifacts")).toBeNull()
    })
})