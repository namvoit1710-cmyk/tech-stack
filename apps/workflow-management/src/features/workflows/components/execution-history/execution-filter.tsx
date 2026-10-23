import { WORKFLOW_EXECUTION_STATUS_OPTIONS } from "@/constants/workflows";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { Popover, PopoverContent, PopoverTrigger } from "@ldc/ui/components/popover";
import { Spinner } from "@ldc/ui/components/spinner";
import { cn } from "@ldc/ui";
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import { ChevronDownIcon, SearchIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useWorkflowListInfinite } from "../../hooks/apis/workflows";
import { useEnvironmentList } from "../../../environments/hooks/apis/environments";

import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@ldc/ui/components/command";

// SA-2704: accept a full run UUID only; a partial/typo id must not fire a query
// or write a broken runId into the shareable URL.
const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

export interface IExecutionFilterProps {
    isActive?: boolean;
    initialFilter?: {
        workflowId: string;
        executionStatus: string;
        runId?: string;
        environmentId?: string;
    };
    setWorkflowId: (workflowId: string) => void;
    setExecutionStatus: (executionStatus: string) => void;
    setRunId?: (runId: string) => void;
    setEnvironmentId?: (environmentId: string) => void;
}

const ExecutionFilter = ({ isActive, initialFilter, setWorkflowId, setExecutionStatus, setRunId, setEnvironmentId }: IExecutionFilterProps) => {
    const { workflowId, executionStatus, runId, environmentId } = initialFilter ?? {};

    const { t } = useLanguage();

    // SA-2704: local text state for the run-id input; the applied filter (URL /
    // query) is only updated for a valid UUID (or when cleared).
    const [runIdText, setRunIdText] = useState(runId ?? "");
    useEffect(() => {
        setRunIdText(runId ?? "");
    }, [runId]);
    const trimmedRunId = runIdText.trim();
    const runIdInvalid = trimmedRunId.length > 0 && !UUID_RE.test(trimmedRunId);
    // While a run-id filter is applied, the workflow/status selectors are moot.
    const runIdActive = (runId ?? "").trim().length > 0;

    const applyRunId = (value: string) => {
        const v = value.trim();
        if (v.length === 0) {
            setRunId?.("");
        } else if (UUID_RE.test(v)) {
            setRunId?.(v);
        }
    };
    const clearRunId = () => {
        setRunIdText("");
        setRunId?.("");
    };

    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [workflowSearch, setWorkflowSearch] = useState("");
    const [debouncedWorkflowSearch, setDebouncedWorkflowSearch] = useState("");
    const debouncedSetWorkflowSearch = useDebounceCallback(setDebouncedWorkflowSearch, 300);

    const {
        data: workflowInfiniteData,
        fetchNextPage,
        hasNextPage,
        isFetchingNextPage,
    } = useWorkflowListInfinite(
        {
            $filter: debouncedWorkflowSearch ? `contains(name, '${debouncedWorkflowSearch}')` : undefined,
        },
        { enabled: isActive }
    );

    const workflowOptions = useMemo(() => {
        const pages = workflowInfiniteData?.pages ?? [];
        return pages.flatMap((page) => page.data.items ?? []).map((w) => ({
            value: w.id,
            label: w.name,
        }));
    }, [workflowInfiniteData]);

    const sentinelRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        const el = sentinelRef.current;
        if (!el) return;
        const observer = new IntersectionObserver(
            (entries) => {
                if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
                    void fetchNextPage();
                }
            },
            { threshold: 0.1 }
        );
        observer.observe(el);
        return () => observer.disconnect();
    }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

    const selectedWorkflowLabel = useMemo(() => {
        if (!workflowId || workflowId === "all") return t("all_workflows");
        return workflowOptions.find((o) => o.value === workflowId)?.label ?? workflowId;
    }, [workflowId, workflowOptions, t]);

    const [statusOpen, setStatusOpen] = useState(false);

    const selectedStatusLabel = useMemo(() => {
        if (!executionStatus) return t("all_statuses");
        return (
            WORKFLOW_EXECUTION_STATUS_OPTIONS.find((o) => String(o.value) === executionStatus)?.label ?? executionStatus
        );
    }, [executionStatus, t]);

    // SA-2723: environment filter — options are the tenant's Environment
    // Collections; selecting one narrows the run list to runs that used it.
    const [envOpen, setEnvOpen] = useState(false);
    const { data: environmentData } = useEnvironmentList(undefined, { enabled: isActive });
    const environmentOptions = useMemo(() => {
        const items = environmentData?.data?.items ?? [];
        return items.map((e) => ({
            value: e.id,
            label: e.section ? `${e.section} / ${e.name}` : e.name,
        }));
    }, [environmentData]);
    const selectedEnvLabel = useMemo(() => {
        if (!environmentId || environmentId === "all") return "All Environments";
        return environmentOptions.find((o) => o.value === environmentId)?.label ?? "All Environments";
    }, [environmentId, environmentOptions]);

    return (
        <div className="flex items-center gap-3 justify-end">
            {/* SA-2704: Run ID filter — paste a run UUID to pin the list to that
                run (+ its sub-runs). URL-backed, so the link can be shared. */}
            <div className="relative w-[320px]">
                <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input
                    value={runIdText}
                    onChange={(e) => setRunIdText(e.target.value)}
                    onBlur={() => applyRunId(runIdText)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") applyRunId(runIdText);
                        if (e.key === "Escape") clearRunId();
                    }}
                    spellCheck={false}
                    placeholder={t("run_id_filter_placeholder")}
                    aria-label={t("run_id_filter_placeholder")}
                    aria-invalid={runIdInvalid}
                    className={cn("h-9 pl-8 pr-8 font-mono text-xs", runIdInvalid && "border-red-500 focus-visible:ring-red-500")}
                />
                {runIdText.length > 0 ? (
                    <button
                        type="button"
                        aria-label={t("clear_filter")}
                        title={t("clear_filter")}
                        onClick={clearRunId}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                        <XIcon className="size-3.5" />
                    </button>
                ) : null}
                {runIdInvalid ? (
                    <p className="absolute -bottom-4 left-1 text-[10px] text-red-600">{t("invalid_run_id")}</p>
                ) : null}
            </div>

            <Popover
                open={workflowOpen}
                onOpenChange={(o) => {
                    setWorkflowOpen(o);
                    if (!o) {
                        setWorkflowSearch("");
                        setDebouncedWorkflowSearch("");
                    }
                }}
            >
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={workflowOpen}
                        disabled={runIdActive}
                        className="min-w-[200px] justify-between font-normal"
                    >
                        <span className="truncate">{selectedWorkflowLabel}</span>
                        <ChevronDownIcon
                            className="text-muted-foreground/80 ml-2 size-4 shrink-0"
                            aria-hidden="true"
                        />
                    </Button>
                </PopoverTrigger>

                <PopoverContent className="min-w-[250px] p-0" align="end" side="bottom">
                    <Command shouldFilter={false} className="space-y-2">
                        <CommandInput
                            placeholder={t("search_workflow")}
                            value={workflowSearch}
                            onValueChange={(val) => {
                                setWorkflowSearch(val);
                                debouncedSetWorkflowSearch(val);
                            }}
                        />

                        <CommandList>
                            <CommandEmpty>{t("no_workflows_found")}</CommandEmpty>

                            <CommandItem
                                value="all"
                                data-checked={!workflowId || workflowId === "all"}
                                onSelect={() => {
                                    setWorkflowId("all");
                                    setWorkflowOpen(false);
                                }}
                            >
                                {t("all_workflows")}
                            </CommandItem>

                            {workflowOptions.map((workflow) => (
                                <CommandItem
                                    key={workflow.value}
                                    value={workflow.value}
                                    data-checked={workflowId === workflow.value}
                                    onSelect={() => {
                                        setWorkflowId(workflow.value);
                                        setWorkflowOpen(false);
                                    }}
                                >
                                    {workflow.label}
                                </CommandItem>
                            ))}

                            <div ref={sentinelRef} className="h-px" />

                            {isFetchingNextPage && (
                                <div className="flex justify-center py-2">
                                    <Spinner className="size-4" />
                                </div>
                            )}
                        </CommandList>
                    </Command>
                </PopoverContent>
            </Popover>

            <Popover open={statusOpen} onOpenChange={setStatusOpen}>
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={statusOpen}
                        disabled={runIdActive}
                        className="min-w-[160px] justify-between font-normal"
                    >
                        <span className="truncate">{selectedStatusLabel}</span>
                        <ChevronDownIcon
                            className="text-muted-foreground/80 ml-2 size-4 shrink-0"
                            aria-hidden="true"
                        />
                    </Button>
                </PopoverTrigger>

                <PopoverContent className="w-[160px] p-0" align="end" side="bottom">
                    <Command>
                        <CommandList>
                            <CommandItem
                                value=""
                                data-checked={!executionStatus}
                                onSelect={() => {
                                    setExecutionStatus("");
                                    setStatusOpen(false);
                                }}
                            >
                                {t("all_statuses")}
                            </CommandItem>

                            {WORKFLOW_EXECUTION_STATUS_OPTIONS.map((option) => (
                                <CommandItem
                                    key={option.value}
                                    value={option.value}
                                    data-checked={executionStatus === option.value}
                                    onSelect={() => {
                                        setExecutionStatus(option.value);
                                        setStatusOpen(false);
                                    }}
                                >
                                    {option.label}
                                </CommandItem>
                            ))}
                        </CommandList>
                    </Command>
                </PopoverContent>
            </Popover>

            {/* SA-2723: environment filter — narrow the run list to one environment. */}
            <Popover open={envOpen} onOpenChange={setEnvOpen}>
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={envOpen}
                        disabled={runIdActive}
                        className="min-w-[190px] justify-between font-normal"
                    >
                        <span className="truncate">{selectedEnvLabel}</span>
                        <ChevronDownIcon
                            className="text-muted-foreground/80 ml-2 size-4 shrink-0"
                            aria-hidden="true"
                        />
                    </Button>
                </PopoverTrigger>

                <PopoverContent className="min-w-[220px] p-0" align="end" side="bottom">
                    <Command>
                        <CommandInput placeholder="Search environment…" />
                        <CommandList>
                            <CommandEmpty>No environments</CommandEmpty>
                            <CommandItem
                                value="all"
                                data-checked={!environmentId || environmentId === "all"}
                                onSelect={() => {
                                    setEnvironmentId?.("all");
                                    setEnvOpen(false);
                                }}
                            >
                                All Environments
                            </CommandItem>
                            {environmentOptions.map((option) => (
                                <CommandItem
                                    key={option.value}
                                    value={option.label}
                                    data-checked={environmentId === option.value}
                                    onSelect={() => {
                                        setEnvironmentId?.(option.value);
                                        setEnvOpen(false);
                                    }}
                                >
                                    {option.label}
                                </CommandItem>
                            ))}
                        </CommandList>
                    </Command>
                </PopoverContent>
            </Popover>
        </div>
    );
};

export default ExecutionFilter;
