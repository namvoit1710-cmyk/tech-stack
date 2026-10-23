import { useWorkflowById, useWorkflowListInfinite } from "@/features/workflows/hooks/apis/workflows";
import type { IWorkflowListItem } from "@/features/workflows/types/workflows";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import {
    Command,
    CommandEmpty,
    CommandInput,
    CommandItem,
    CommandList,
} from "@ldc/ui/components/command";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@ldc/ui/components/popover";
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import { Check, ChevronsUpDown, ExternalLink } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

interface IWorkflowPickerControlProps {
    name: string;
    value?: string;
    onChange?: (event: { target: { name: string; value: string } }) => void;
    placeholder?: string;
    disabled?: boolean;
}

/**
 * Custom AutoForm control for the WORKFLOW (sub-workflow) node's
 * ``workflow_id`` field. Renders a searchable workflow dropdown plus a button
 * that opens the selected sub-workflow in a new tab.
 *
 * The list shows one row per workflow (latest version, PUBLISHED prioritized)
 * — that de-duplication is done server-side by the list endpoint; we only ask
 * for ``updated_at desc`` ordering and name search.
 *
 * Wired in via the backend NodeSpec (``field_control="WorkflowPickerControl"``)
 * and registered on the node-detail Builder's ``fieldControl`` map.
 */
const WorkflowPickerControl = ({
    name,
    value,
    onChange,
    placeholder,
    disabled,
}: IWorkflowPickerControlProps) => {
    const { t } = useLanguage();
    // Exclude the workflow currently being edited so it cannot pick itself as
    // a sub-workflow (which would recurse infinitely).
    const { workflowId: currentWorkflowId } = useParams();
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState("");
    const sentinelRef = useRef<HTMLDivElement>(null);

    const debouncedSetSearch = useDebounceCallback(setSearch, 300);

    const queryParams = useMemo(() => {
        const escaped = search.replace(/'/g, "''");
        return {
            $orderby: "updated_at desc",
            ...(search ? { $filter: `contains(name, '${escaped}')` } : {}),
        };
    }, [search]);

    const {
        data,
        isLoading,
        isFetchingNextPage,
        hasNextPage,
        fetchNextPage,
    } = useWorkflowListInfinite(queryParams, { enabled: open });

    const items: IWorkflowListItem[] = (
        (data)?.pages?.flatMap(
            (page) => page.data.items,
        ) ?? []
    ).filter((item) => item.id !== currentWorkflowId);

    // Resolve the selected workflow name even when it is not in the loaded
    // pages (e.g. value restored from a saved node on a different page).
    const { data: selectedWorkflow } = useWorkflowById(value ?? "", {
        enabled: !!value,
        meta: { silent: true },
    });

    const selectedName =
        items.find((item) => item.id === value)?.name ??
        selectedWorkflow?.data?.name;

    const selectedLabel = value
        ? selectedName
            ? `${selectedName} (${value})`
            : value
        : "";

    useEffect(() => {
        const sentinel = sentinelRef.current;
        if (!sentinel || !open) return;

        const observer = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
                void fetchNextPage();
            }
        });

        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [fetchNextPage, hasNextPage, isFetchingNextPage, open]);

    const handleSelect = (workflowId: string) => {
        setSearch("");
        setOpen(false);
        onChange?.({ target: { name, value: workflowId } });
    };

    const handleNavigate = () => {
        if (!value) return;
        // Open the selected sub-workflow in a new tab. The current path ends
        // with the current workflow id (route `workflow/:workflowId`), so we
        // swap the last segment, preserving whatever base the shell mounts us under.
        const path = window.location.pathname.replace(/[^/]+\/?$/, value);
        window.open(path, "_blank", "noopener,noreferrer");
    };

    return (
        <div className="flex w-full items-center gap-2">
            <Popover open={open} onOpenChange={setOpen} modal>
                <PopoverTrigger asChild>
                    <Button
                        type="button"
                        variant="outline"
                        role="combobox"
                        aria-expanded={open}
                        disabled={disabled}
                        className="h-10 min-w-0 flex-1 justify-between font-normal"
                    >
                        <span className="truncate">
                            {selectedLabel || (
                                <span className="text-muted-foreground">
                                    {placeholder ?? t("nodes.workflow_picker_placeholder")}
                                </span>
                            )}
                        </span>
                        <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
                    </Button>
                </PopoverTrigger>
                <PopoverContent
                    align="start"
                    className="w-(--radix-popover-trigger-width) p-0"
                >
                    <Command shouldFilter={false}>
                        <CommandInput
                            placeholder={t("nodes.workflow_picker_search_placeholder")}
                            onValueChange={debouncedSetSearch}
                        />
                        <CommandList>
                            {isLoading && (
                                <div className="p-2 text-sm text-muted-foreground">
                                    {t("nodes.workflow_picker_loading")}
                                </div>
                            )}
                            {!isLoading && items.length === 0 && (
                                <CommandEmpty>{t("nodes.workflow_picker_empty")}</CommandEmpty>
                            )}
                            {items.map((item) => (
                                <CommandItem
                                    key={item.id}
                                    value={item.id}
                                    onSelect={() => handleSelect(item.id)}
                                    className="flex items-center gap-2"
                                >
                                    <span className="flex-1 truncate">{item.name}</span>
                                    <span className="truncate text-xs text-muted-foreground">
                                        {item.id}
                                    </span>
                                    {value === item.id && (
                                        <Check className="size-4 shrink-0 text-green-500" />
                                    )}
                                </CommandItem>
                            ))}
                            {isFetchingNextPage && (
                                <div className="p-2 text-sm text-muted-foreground">
                                    {t("nodes.workflow_picker_loading")}
                                </div>
                            )}
                            <div ref={sentinelRef} className="h-1" />
                        </CommandList>
                    </Command>
                </PopoverContent>
            </Popover>

            <Button
                type="button"
                variant="outline"
                size="icon"
                disabled={!value}
                onClick={handleNavigate}
                title={t("nodes.workflow_picker_navigate")}
                aria-label={t("nodes.workflow_picker_navigate")}
                className="shrink-0"
            >
                <ExternalLink className="size-4" />
            </Button>
        </div>
    );
};

WorkflowPickerControl.displayName = "WorkflowPickerControl";

export default WorkflowPickerControl;