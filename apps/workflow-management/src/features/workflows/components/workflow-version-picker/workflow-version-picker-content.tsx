import { useWorkflowVersionListInfinite } from "@/features/workflows/hooks/apis/workflows";
import { UNSAVED_VERSION_SENTINEL } from "@/features/workflows/hooks/use-workflow-detail-page";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import type { IWorkflowListItem, IWorkflowListResponse } from "@/features/workflows/types/workflows";
import { formatShortDate, formatVersionLabel } from "@/features/workflows/utils/version-utils";
import { useLanguage } from "@/hooks/use-language";
import type { InfiniteData } from "@ldc/tanstack-query";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@ldc/ui/components/command";
import { Separator } from "@ldc/ui/components/separator";
import { Skeleton } from "@ldc/ui/components/skeleton";
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import { CheckIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface IWorkflowVersionPickerContentProps {
    workflowId: string;
    selectedVersionId: string | null;
    onSelect: (versionId: string) => void;
}

const VersionTableHeader = ({ t }: { t: (key: string) => string }) => (
    <div className="grid grid-cols-[1fr_1fr_80px_24px] gap-2 px-2 py-1 text-xs font-semibold text-muted-foreground border-b sticky top-0 bg-background z-10">
        <span>{t("version_col_version")}</span>
        <span>{t("version_col_created")}</span>
        <span>{t("version_col_status")}</span>
        <span />
    </div>
);

const VersionSkeleton = () => (
    <div className="px-2 py-1.5">
        <Skeleton className="h-5 w-full" />
    </div>
);

const WorkflowVersionPickerContent = ({
    workflowId,
    selectedVersionId,
    onSelect,
}: IWorkflowVersionPickerContentProps) => {
    const { t } = useLanguage();
    const [search, setSearch] = useState("");
    const sentinelRef = useRef<HTMLDivElement>(null);
    const unsavedDraft = useEditorStore((s) => s.unsavedDraft);
    const unsavedDraftBaseVersion = useEditorStore((s) => s.unsavedDraftBaseVersion);
    const isUnsavedSelected = selectedVersionId === UNSAVED_VERSION_SENTINEL;

    const debouncedSetSearch = useDebounceCallback(setSearch, 300);

    const {
        data,
        isLoading,
        isError,
        isFetchingNextPage,
        hasNextPage,
        fetchNextPage,
        refetch,
    } = useWorkflowVersionListInfinite(workflowId, { search }, { meta: { silent: true } });

    const items: IWorkflowListItem[] = (data as InfiniteData<IWorkflowListResponse> | undefined)?.pages?.flatMap((page) => page.data.items) ?? [];

    useEffect(() => {
        const sentinel = sentinelRef.current;
        if (!sentinel) return;

        const observer = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
                void fetchNextPage();
            }
        });

        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

    return (
        <Command shouldFilter={false}>
            <CommandInput
                placeholder={t("version_picker_search_placeholder")}
                onValueChange={debouncedSetSearch}
            />
            <CommandList className="flex-2 max-h-auto min-h-0 overflow-y-auto">
                {!!unsavedDraft && (
                    <>
                        <CommandItem
                            value={UNSAVED_VERSION_SENTINEL}
                            onSelect={() => onSelect(UNSAVED_VERSION_SENTINEL)}
                            className="grid grid-cols-[1fr_1fr_24px] gap-2 items-center text-amber-600 font-medium"
                        >
                            <span className="flex items-center gap-2 whitespace-nowrap">
                                <span className="size-2 rounded-full bg-amber-500 shrink-0" />
                                {t("version_picker_unsaved")}
                            </span>

                            <span className="text-xs text-amber-500/70 truncate">
                                {unsavedDraftBaseVersion
                                    ? t("version_unsaved_based_on", { version: formatVersionLabel(unsavedDraftBaseVersion) })
                                    : "—"}
                            </span>

                            <span>{isUnsavedSelected && <CheckIcon className="size-4 text-green-500 shrink-0" />}</span>
                        </CommandItem>

                        <Separator className="my-1" />
                    </>
                )}

                <VersionTableHeader t={t} />

                {isLoading && (
                    <>
                        <VersionSkeleton />
                        <VersionSkeleton />
                        <VersionSkeleton />
                    </>
                )}

                {isError && (
                    <div className="flex flex-col items-center gap-2 p-4">
                        <p className="text-sm text-muted-foreground">{t("version_list_error")}</p>
                        <Button variant="outline" size="sm" onClick={() => refetch()}>
                            {t("retry")}
                        </Button>
                    </div>
                )}

                {!isLoading && !isError && items.length === 0 && (
                    <CommandEmpty>{t("version_picker_empty")}</CommandEmpty>
                )}

                {items.map((item) => (
                    <CommandItem
                        key={item.version}
                        value={item.version}
                        onSelect={() => item.version && onSelect(item.version)}
                        className="grid grid-cols-[1fr_1fr_80px_24px] gap-2 items-center"
                    >
                        <span className="truncate font-mono text-sm">{formatVersionLabel(item.version)}</span>
                        <span className="text-xs text-muted-foreground truncate">
                            {item.created_at ? formatShortDate(item.created_at) : "—"}
                        </span>
                        <span>
                            {item.status === "PUBLISHED" && (
                                <Badge variant="secondary" className="text-xs">
                                    {t("version_picker_published")}
                                </Badge>
                            )}
                            {item.status === "DRAFT" && (
                                <Badge variant="outline" className="text-xs">
                                    {t("version_picker_draft")}
                                </Badge>
                            )}
                        </span>
                        <span>{selectedVersionId === item.version && <CheckIcon className="size-4 text-green-500 shrink-0" />}</span>
                    </CommandItem>
                ))}

                {isFetchingNextPage && <VersionSkeleton />}

                <div ref={sentinelRef} className="h-1" />
            </CommandList>
        </Command>
    );
};

export default WorkflowVersionPickerContent;
