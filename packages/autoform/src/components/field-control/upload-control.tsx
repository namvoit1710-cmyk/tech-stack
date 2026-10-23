import { Button } from "@ldc/ui/components/button";
import {
    Upload,
    UploadDropzone,
    UploadFileList,
} from "@ldc/ui/components/upload";
import { Download, FileIcon, FileImage, FileSpreadsheet, FileText } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useFormContext } from "react-hook-form";
import { useBuilderContext } from "../../contexts/builder.context";
import type { FieldComponentProps } from "../../types/schema";
import { useBulkUpload } from "./use-bulk-upload";

export interface IUploadControlProps extends FieldComponentProps {
    onChange?: (event: { target: { name: string; value: string | string[] } }) => void;
    readOnly?: boolean;
    multiple?: boolean;
    accept?: string;
    maxSize?: number;
}
interface IFileInfoService {
    content_type: string;
    file_id: string;
    filename: string;
    size_bytes: number;
    status: "uploaded" | "processing" | "ready" | "error";
}

interface IFileDetailsResponse {
    current_version?: {
        original_filename?: string;
        content_type?: string;
        raw_size_bytes?: number | null;
    } | null;
}

interface IUploadedFileInfo {
    name: string | null;
    size: number | null;
    contentType: string | null;
}

const uploadedFileNameKey = (name: string) => `${name}_name`;
const uploadedFileSizeKey = (name: string) => `${name}_size`;

const asText = (value: unknown): string | null =>
    typeof value === "string" && value.trim() ? value : null;

const asSize = (value: unknown): number | null => {
    const size = typeof value === "string" && value.trim() ? Number(value) : value;
    return typeof size === "number" && Number.isFinite(size) && size >= 0 ? size : null;
};

const SPREADSHEET_EXTENSIONS = new Set(["xlsx", "xls", "xlsm", "csv", "tsv", "ods"]);
const DOCUMENT_EXTENSIONS = new Set(["pdf", "doc", "docx", "txt", "md", "rtf", "json", "xml"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);

const fileExtension = (fileName: string | null): string => {
    if (!fileName) return "";
    const dot = fileName.lastIndexOf(".");
    return dot > 0 && dot < fileName.length - 1 ? fileName.slice(dot + 1).toLowerCase() : "";
};

const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const FileTypeIcon = ({ extension, contentType }: { extension: string; contentType: string | null }) => {
    const className = "size-5 shrink-0";
    if (SPREADSHEET_EXTENSIONS.has(extension) || contentType?.includes("spreadsheet") || contentType === "text/csv") {
        return <FileSpreadsheet className={`${className} text-green-600`} />;
    }
    if (IMAGE_EXTENSIONS.has(extension) || contentType?.startsWith("image/")) {
        return <FileImage className={`${className} text-purple-600`} />;
    }
    if (DOCUMENT_EXTENSIONS.has(extension) || contentType === "application/pdf") {
        return <FileText className={`${className} text-blue-600`} />;
    }
    return <FileIcon className={`${className} text-muted-foreground`} />;
};

function useUploadedFileInfo({
    name,
    value,
    enabled,
    services,
}: {
    name: string;
    value: unknown;
    enabled: boolean;
    services: ReturnType<typeof useBuilderContext>["services"];
}): IUploadedFileInfo {
    const formContext = useFormContext();
    const fileId = typeof value === "string" ? value : "";
    const active = enabled && !!fileId;
    const storedName = active ? asText(formContext?.getValues?.(uploadedFileNameKey(name))) : null;
    const storedSize = active ? asSize(formContext?.getValues?.(uploadedFileSizeKey(name))) : null;
    const [fetched, setFetched] = useState<(IUploadedFileInfo & { fileId: string }) | null>(null);
    const fetchFile = services?.file?.fetch;
    const needsDetails = active && (storedName === null || storedSize === null);

    useEffect(() => {
        if (!needsDetails || !fetchFile) return;
        let cancelled = false;
        fetchFile(`/files/${encodeURIComponent(fileId)}`)
            .then((details: IFileDetailsResponse) => {
                const version = details?.current_version;
                if (cancelled || !version) return;
                setFetched({
                    fileId,
                    name: asText(version.original_filename),
                    size: asSize(version.raw_size_bytes),
                    contentType: asText(version.content_type),
                });
            })
            .catch(() => undefined);
        return () => {
            cancelled = true;
        };
    }, [needsDetails, fileId, fetchFile]);

    const details = fetched?.fileId === fileId ? fetched : null;
    return {
        name: storedName ?? details?.name ?? null,
        size: storedSize ?? details?.size ?? null,
        contentType: details?.contentType ?? null,
    };
}

const UploadControl = (props: IUploadControlProps) => {
    const { name, value, onChange, field: _field, onFormActions, disabled, readOnly, multiple, ...rest } = props;

    const { services } = useBuilderContext();
    const uploadRequestIdRef = useRef(0);
    const [isUploading, setIsUploading] = useState(false);
    const uploadRef = useRef<HTMLInputElement | null>(null);
    const [hasFiles, setHasFiles] = useState(false);

    const handleFilesChange = useCallback(
        async (files: File[]) => {
            setHasFiles(files.length > 0);

            if (!files.length) {
                uploadRequestIdRef.current += 1;
                onChange?.({ target: { value: "", name } });
                return;
            }

            const file = files[0];
            if (!file) return;

            const requestId = ++uploadRequestIdRef.current;

            setIsUploading(true);
            try {
                onFormActions?.("uploading_file", {
                    file_name: file.name,
                });

                const formData = new FormData();
                formData.append("file", file);

                const response: IFileInfoService = await services?.file?.post(
                    `/upload?filename=${encodeURIComponent(file.name)}`,
                    formData,
                    {
                        "Content-Type": "multipart/form-data",
                    },
                );

                if (requestId !== uploadRequestIdRef.current) return;

                onFormActions?.("upload_success", { response, name });
                onChange?.({ target: { value: response.file_id, name } });
            } catch (error: unknown) {
                if (requestId !== uploadRequestIdRef.current) return;
                console.error(">>> [Upload] Upload FAILED:", error);
                onFormActions?.("upload_file_failed", { error });
            } finally {
                if (requestId === uploadRequestIdRef.current) {
                    setIsUploading(false);
                }
            }
        },
        [name, onChange, onFormActions, services],
    );

    // R5 (SA-2040 T9): bounded-concurrency multi-file upload for `multiple`.
    //
    // NOTE (deviation from plan): `services.file` here is the `ServiceHandler` built by
    // `use-builder-services.ts` — `post(endpoint, data, headers?: Record<string,string>)` —
    // which always wraps its 3rd arg as `{ headers: reqHeaders }` before calling the real
    // `APISdk`. It does NOT forward `onUploadProgress` / `cancelToken` / `retries`, and does
    // NOT expose `createCancelToken()` / `isCancel()` (those live only on the raw `APISdk`
    // instance one layer below, in `@ldc/api-sdk`, which this thin wrapper never re-exports).
    // Extending that wrapper is out of scope for this change (touch-only constraint), so
    // per-file progress and true request-level cancellation aren't available yet — the pool
    // still honours `abort()`/`retry()` at the state layer (stops launching queued items and
    // discards results for items whose controller was aborted).
    const uploadOne = useCallback(
        async (file: File): Promise<string> => {
            const formData = new FormData();
            formData.append("file", file);

            const response: IFileInfoService = await services?.file?.post(
                `/upload?filename=${encodeURIComponent(file.name)}`,
                formData,
                {
                    "Content-Type": "multipart/form-data",
                },
            );

            return response.file_id;
        },
        [services],
    );

    const bulk = useBulkUpload({
        uploadFile: uploadOne,
        concurrency: 5,
        onChange: (ids) => onChange?.({ target: { name, value: ids } }),
    });

    // Enqueue only files not already tracked (dedupe by name); clearing empties the value.
    const handleMultipleChange = useCallback(
        (files: File[]) => {
            setHasFiles(files.length > 0);

            if (!files.length) {
                bulk.reset();
                onChange?.({ target: { name, value: [] } });
                return;
            }
            // useUpload delivers the FULL current list. Reconcile both ways: a per-file removal
            // (trash icon) sends a shorter list — drop items no longer present so their file_id
            // leaves the value; then enqueue any genuinely new names (SA-2040 R5 review fix).
            const currentNames = new Set(files.map((f) => f.name));
            bulk.items.filter((it) => !currentNames.has(it.file.name)).forEach((it) => bulk.remove(it.id));
            const known = new Set(bulk.items.map((i) => i.file.name));
            const added = files.filter((f) => !known.has(f.name));
            if (added.length) bulk.start(added);
        },
        [bulk, name, onChange],
    );

    // Cancel any in-flight uploads on unmount. `bulk.abort` is referentially stable
    // (useCallback with empty deps inside useBulkUpload), so listing it as the sole
    // dependency here does not cause the effect to re-run on every item/progress update.
    useEffect(() => () => bulk.abort(), [bulk.abort]);

    const isBulkUploading = bulk.items.some((i) => i.status === "uploading" || i.status === "queued");

    const fileInfo = useUploadedFileInfo({ name, value, enabled: !!readOnly, services });
    const displayName = fileInfo.name ?? value;

    if (readOnly) {
        if (!value) {
            return <span className="text-muted-foreground text-sm">No file uploaded</span>;
        }

        const extension = fileExtension(fileInfo.name);
        const meta = [extension.toUpperCase(), fileInfo.size === null ? "" : formatFileSize(fileInfo.size)]
            .filter(Boolean)
            .join(" · ");

        return (
            <div className="flex items-center gap-3 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-background">
                    <FileTypeIcon extension={extension} contentType={fileInfo.contentType} />
                </div>
                <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-foreground" title={typeof displayName === "string" ? displayName : undefined}>
                        {displayName}
                    </span>
                    {meta && <span className="text-xs text-muted-foreground">{meta}</span>}
                </div>
                <Button
                    variant="ghost"
                    className="flex items-center cursor-pointer gap-1 text-primary hover:underline"
                    onClick={() => onFormActions?.("download_file", { file_id: value, name: displayName })}
                >
                    <Download className="size-4" />
                </Button>
            </div>
        );
    }

    return (
        <Upload
            {...rest}
            multiple={multiple}
            disabled={disabled || (multiple ? isBulkUploading : isUploading)}
            uploading={multiple ? isBulkUploading : isUploading}
            onFilesChange={multiple ? handleMultipleChange : handleFilesChange}
            fileInputRef={uploadRef}
        >
            {!hasFiles && <UploadDropzone />}
            <UploadFileList setHasFiles={setHasFiles} />
        </Upload>
    );
};

export default UploadControl;
