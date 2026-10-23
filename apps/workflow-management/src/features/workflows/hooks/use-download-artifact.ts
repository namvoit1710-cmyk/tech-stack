import { toast } from "@ldc/ui/blocks/toast/toast"
import { useCallback, useRef, useState } from "react"
import { getFileById } from "./apis/file"

interface DownloadArtifactInput {
    fileId: string
    filename?: string
}

export const useDownloadArtifact = () => {
    const [downloadingId, setDownloadingId] = useState<string | null>(null)
    const latestRequestIdRef = useRef(0)

    const downloadArtifact = useCallback(async ({ fileId, filename }: DownloadArtifactInput) => {
        const requestId = latestRequestIdRef.current + 1
        latestRequestIdRef.current = requestId
        setDownloadingId(fileId)

        let blobUrl: string | null = null
        let link: HTMLAnchorElement | null = null

        try {
            const blob = await getFileById(fileId)
            blobUrl = window.URL.createObjectURL(blob)
            link = document.createElement("a")

            link.href = blobUrl
            link.download = filename ?? `artifact-${fileId}`

            document.body.appendChild(link)
            link.click()
        } catch {
            toast.error("Download failed")
        } finally {
            link?.remove()

            if (blobUrl) {
                window.URL.revokeObjectURL(blobUrl)
            }

            if (latestRequestIdRef.current === requestId) {
                setDownloadingId(null)
            }
        }
    }, [])

    return {
        downloadArtifact,
        downloadingId,
        isDownloading: (fileId: string) => downloadingId === fileId
    }
}
