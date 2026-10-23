import JsonView from "@/components/json-view/json-view"
import { useDownloadArtifact } from "@/features/workflows/hooks/use-download-artifact"
import type { IScopedVariable } from "@/features/workflows/types/node-detail"
import { useLanguage } from "@/hooks/use-language"
import { Button } from "@ldc/ui/components/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@ldc/ui/components/collapsible"
import { ChevronDown, Download, File, FileSpreadsheet, FileText, LoaderCircle } from "lucide-react"
import { useState } from "react"

interface ArtifactItemProps {
    paths: IScopedVariable["paths"]
    enableDragKey?: boolean
}

interface ArtifactIconProps {
    contentType: string
    className?: string
}

const ArtifactIcon = ({ contentType, className }: ArtifactIconProps) => {
    if (contentType.includes("csv") || contentType.includes("spreadsheet")) {
        return <FileSpreadsheet className={className} />
    }
    if (contentType.includes("text")) {
        return <FileText className={className} />
    }
    return <File className={className} />
}

interface ArtifactSample {
    file_id: string
    filename: string
    content_type: string
    size_bytes: number
    produced_by_node: string
    produced_by_task: string
    registered_at: string
}

const ArtifactItem = ({ paths, enableDragKey }: ArtifactItemProps) => {
    const { t } = useLanguage()
    const { downloadArtifact, isDownloading } = useDownloadArtifact()

    const formatBytes = (bytes: number) => {
        if (bytes === 0) return '0 Bytes'
        const k = 1024
        const sizes = ['Bytes', 'KB', 'MB', 'GB']
        const i = Math.floor(Math.log(bytes) / Math.log(k))
        return `${Math.round((bytes / Math.pow(k, i)) * 100) / 100} ${sizes[i]}`
    }

    const hasFileRef = Object.values(paths).some(pathData => pathData.kind === 'file_ref')
    const hasPreviewData = Object.values(paths).some(pathData => pathData.kind !== 'file_ref')

    if (!hasFileRef && !hasPreviewData) {
        return (
            <span className="text-sm text-gray-500 text-center block">
                {t("nodes.nodes_popup_no_artifacts")}
            </span>
        )
    }

    return (
        <div className="flex flex-col gap-3">
            <ArtifactCard
                pathData={paths}
                enableDragKey={enableDragKey}
                onDownload={downloadArtifact}
                isDownloading={isDownloading}
                formatBytes={formatBytes}
            />
        </div>
    )
}

interface ArtifactCardProps {
    pathData: IScopedVariable["paths"]
    enableDragKey?: boolean
    onDownload: (input: { fileId: string; filename?: string }) => void
    isDownloading: (fileId: string) => boolean
    formatBytes: (bytes: number) => string
}

const ArtifactCard = ({ pathData, enableDragKey = false, onDownload, isDownloading, formatBytes }: ArtifactCardProps) => {
    const [isOpen, setIsOpen] = useState(false)

    const fileRef = Object.values(pathData).find(data => data.kind === 'file_ref')?.sample as ArtifactSample | undefined
    const sampleData = Object.entries(pathData).filter(([_, data]) => data.kind !== 'file_ref').reduce<Record<string, unknown>>((acc, [key, data]) => {
        return { ...acc, [key]: data.sample }
    }, {})
    const hasPreviewData = Object.keys(sampleData).length > 0
    const isDownloadPending = fileRef?.file_id ? isDownloading(fileRef.file_id) : false

    if (!fileRef && hasPreviewData) {
        return (
            <div className="rounded-lg border border-gray-200 bg-white">
                <div className="p-3 bg-gray-50">
                    <JsonView
                        value={sampleData}
                        draggableKeys={enableDragKey}
                        raw={pathData}
                        replacePathKey="displayPath"
                    />
                </div>
            </div>
        )
    }

    return (
        <div className="rounded-lg border border-gray-200 bg-white">
            <Collapsible open={isOpen} onOpenChange={setIsOpen}>
                <div className="p-3 hover:bg-gray-50 transition-colors">
                    <div className="flex items-center justify-between gap-3">
                        <div className="flex min-w-0 items-center gap-3 flex-1 truncate">
                            <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                                <ArtifactIcon contentType={fileRef?.content_type ?? ''} className="size-5" />
                            </div>

                            <div className="min-w-0 flex-1">
                                <p className="truncate text-sm font-medium text-gray-900">
                                    {fileRef?.filename}
                                </p>
                                <div className="flex items-center gap-2 text-xs text-gray-500 mt-0.5">
                                    <span>{fileRef?.content_type}</span>
                                    {fileRef?.size_bytes && (
                                        <>
                                            <span>•</span>
                                            <span>{formatBytes(fileRef.size_bytes)}</span>
                                        </>
                                    )}
                                </div>
                            </div>
                        </div>

                        <div className="flex items-center gap-1 shrink-0">
                            {fileRef?.file_id && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="icon-sm"
                                    data-testid="artifact-download-button"
                                    disabled={isDownloadPending}
                                    onClick={() => onDownload({ fileId: fileRef.file_id, filename: fileRef.filename })}
                                >
                                    {isDownloadPending
                                        ? <LoaderCircle className="size-4 animate-spin" />
                                        : <Download className="size-4" strokeWidth={1.5} />}
                                </Button>
                            )}

                            {hasPreviewData && (
                                <CollapsibleTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon-sm"
                                        data-testid="artifact-preview-toggle"
                                    >
                                        <ChevronDown className={`size-4 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                                    </Button>
                                </CollapsibleTrigger>
                            )}
                        </div>
                    </div>
                </div>

                {hasPreviewData && (
                    <CollapsibleContent>
                        <div className="border-t border-gray-200 p-3 bg-gray-50">
                            <JsonView
                                value={sampleData}
                                draggableKeys={enableDragKey}
                                raw={pathData}
                                replacePathKey="displayPath"
                            />
                        </div>
                    </CollapsibleContent>
                )}
            </Collapsible>
        </div>
    )
}

export default ArtifactItem
