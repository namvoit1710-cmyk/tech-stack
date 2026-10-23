import type { PaginationState } from "@ldc/data-table";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useBuilderContext } from "../../../../contexts/builder.context";
import {
    buildPageArgs,
    buildPostBody,
    DEFAULT_SEARCH_ARG_KEY,
    GATEWAY_INVOKE_PAGED_DATAPATH,
    GATEWAY_INVOKE_TOTALPATH,
} from "../../../../lib/gateway-invoke";
import type { IServerOptionsConfig } from "../../../../types/server-option-config";
import type { OptionRecord } from "../types/types";

export interface IUseSelectTableOptionsProps {
    serverOptions?: IServerOptionsConfig;
    staticOptions?: OptionRecord[];
    enabled?: boolean;
}

const PAGE_SIZE = 10;
const DEBOUNCE_MS = 300;

const getByPath = (obj: unknown, path?: string): unknown => {
    if (!path) return obj;
    return path.split(".").reduce<unknown>((acc, key) => (acc as Record<string, unknown>)?.[key], obj);
};

/**
 * Decode URL-encoded JSON string or pass-through plain object.
 * Returns mapping of { apiKey: formFieldName } or undefined if invalid/absent.
 */
const decodeContextValuesMapping = (raw: string | undefined): Record<string, string> | undefined => {
    if (!raw) return undefined;
    try {
        return JSON.parse(decodeURIComponent(raw)) as Record<string, string>;
    } catch {
        return undefined;
    }
};

export function useSelectTableOptions({
    serverOptions,
    staticOptions = [],
    enabled = false,
}: IUseSelectTableOptionsProps) {
    const { services } = useBuilderContext();
    const { control } = useFormContext();

    const [serverData, setServerData] = useState<OptionRecord[]>([]);
    const [loading, setLoading] = useState(false);
    const [totalCount, setTotalCount] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [searchValue, setSearchValueRaw] = useState("");
    const [pagination, setPagination] = useState<PaginationState>({
        pageIndex: 0,
        pageSize: PAGE_SIZE,
    });

    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const abortRef = useRef<AbortController | null>(null);

    const {
        endpoint,
        service,
        dependencies = [],
        mode = "default",
        responseMapping,
        odataConfig,
        queryParams,
        method = "GET",
        body,
        searchArgKey,
        pageArgKeys,
    } = serverOptions ?? {};

    // POST (gateway) responses are a PAGED envelope one level deeper than the legacy
    // GET default: rows under data.result.body.data, count under ...total_count.
    const isGateway = method === "POST";
    const dataPath = responseMapping?.dataPath ?? (isGateway ? GATEWAY_INVOKE_PAGED_DATAPATH : "data");
    const totalPath = responseMapping?.totalPath ?? (isGateway ? GATEWAY_INVOKE_TOTALPATH : "count");

    // Extract contextValues from queryParams (URL-encoded JSON string) and decode to { apiKey: formFieldName }
    const rawContextValues = queryParams?.contextValues as string | undefined;
    const contextValuesMapping = useMemo(
        () => decodeContextValuesMapping(rawContextValues),
        [rawContextValues]
    );

    // Form field names to watch (values of the mapping)
    const contextValueFields = useMemo(
        () => (contextValuesMapping ? Object.values(contextValuesMapping) : []),
        [contextValuesMapping]
    );

    const depValues = useWatch({
        control,
        name: dependencies.length > 0 ? dependencies : [],
        disabled: !dependencies.length,
    });

    const contextFieldValues = useWatch({
        control,
        name: contextValueFields.length > 0 ? contextValueFields : [],
        disabled: !contextValueFields.length,
    });

    const depParams = useMemo(() => {
        if (!dependencies.length) return {};
        return dependencies.reduce((acc, dep, index) => {
            const value = Array.isArray(depValues) ? depValues[index] : depValues;
            if (value != null && value !== "") acc[dep] = value;
            return acc;
        }, {} as Record<string, unknown>);
    }, [dependencies, depValues]);

    // Build resolved contextValues JSON string: { apiKey: resolvedFormValue }
    const contextValuesParam = useMemo(() => {
        if (!contextValuesMapping || contextValueFields.length === 0) return undefined;
        const resolved: Record<string, unknown> = {};
        Object.keys(contextValuesMapping).forEach((apiKey, index) => {
            const value = Array.isArray(contextFieldValues) ? contextFieldValues[index] : contextFieldValues;
            resolved[apiKey] = value ?? "";
        });
        return JSON.stringify(resolved);
    }, [contextValuesMapping, contextValueFields, contextFieldValues]);

    // Static queryParams without contextValues (handled separately above)
    const staticQueryParams = useMemo(() => {
        if (!queryParams) return {};
        const { contextValues: _omit, ...rest } = queryParams as Record<string, unknown> & { contextValues?: unknown };
        return rest;
    }, [queryParams]);

    interface Handler {
        fetch: (endpoint: string, params: Record<string, unknown>) => Promise<unknown>;
        post: (endpoint: string, data: unknown) => Promise<unknown>;
    }
    // Keep the whole handler (need .post for the gateway branch) — a plain `.fetch`
    // reference (as GET-only used to grab) can't reach the POST/invoke transport.
    const serviceEntry = useMemo<Handler | undefined>(() => {
        if (!service || !services) return undefined;
        const resolved = service.split(".").reduce<unknown>(
            (current, segment) => (current as Record<string, unknown>)?.[segment],
            services
        );
        return typeof (resolved as { fetch?: unknown })?.fetch === "function"
            ? (resolved as Handler)
            : undefined;
    }, [services, service]);

    const fetchData = useCallback(
        (search: string, page: PaginationState) => {
            if (!endpoint || !serviceEntry) return;

            if (dependencies.length > 0) {
                const hasAllDeps = dependencies.every((dep) => depParams[dep] != null);
                if (!hasAllDeps) {
                    setServerData([]);
                    setTotalCount(0);
                    return;
                }
            }

            abortRef.current?.abort();
            const controller = new AbortController();
            abortRef.current = controller;
            const cancelled = false;
            setLoading(true);

            const params: Record<string, unknown> = {
                ...staticQueryParams,          // static config params (minus contextValues)
                ...depParams,                  // flat dep values
                // Pagination always wins. On the gateway path the arg NAMES are the
                // upstream's own (bound per function, OData by default), and $count must be
                // the string "true" — the gateway urlencodes booleans as "True", which
                // file-service rejects. Legacy GET keeps the OData names + real boolean.
                ...buildPageArgs(page, isGateway ? pageArgKeys : undefined, isGateway ? "true" : true),
                ...(contextValuesParam !== undefined && { contextValues: contextValuesParam }),
            };

            if (search) {
                if (mode === "odata" && odataConfig?.searchField) {
                    const existing = odataConfig.defaultFilter ? `(${odataConfig.defaultFilter}) and ` : "";
                    params.$filter = `${existing}contains(toLower(${odataConfig.searchField}), '${search.toLowerCase()}')`;
                } else {
                    // Gateway typeahead: the typed term rides args[searchArgKey] (default
                    // "$search"), so it can match an upstream that names it otherwise.
                    // Legacy REST keeps the literal "$search" key it has always sent.
                    params[isGateway ? (searchArgKey ?? DEFAULT_SEARCH_ARG_KEY) : "$search"] = search;
                }
            } else if (mode === "odata") {
                if (odataConfig?.defaultFilter) params.$filter = odataConfig.defaultFilter;
                if (odataConfig?.defaultOrderBy) params.$orderby = odataConfig.defaultOrderBy;
                if (odataConfig?.defaultSelect) params.$select = odataConfig.defaultSelect;
            }

            // Gateway (POST): keep the whole service handler (need `.post`) and pack every
            // query arg under body.params.args via buildPostBody. The page-arg names and
            // the string "true" for count are already applied above by buildPageArgs.
            const request = isGateway
                ? serviceEntry.post(endpoint, buildPostBody(body, params))
                : serviceEntry.fetch(endpoint, params);

            request
                .then((response) => {
                    if (!cancelled) {
                        setError(null);
                        const items = getByPath(response, dataPath);
                        setServerData(Array.isArray(items) ? (items as OptionRecord[]) : []);
                        const total = getByPath(response, totalPath);
                        setTotalCount(typeof total === "number" ? total : 0);
                    }
                })
                .catch((err: unknown) => {
                    if (!cancelled) {
                        const message = err instanceof Error ? err.message : "Unknown error";
                        setError(message);
                        setServerData([]);
                        setTotalCount(0);
                    }
                })
                .finally(() => {
                    if (!cancelled) setLoading(false);
                });
        },
        [
            endpoint,
            serviceEntry,
            isGateway,
            body,
            searchArgKey,
            JSON.stringify(pageArgKeys),
            JSON.stringify(depParams),
            mode,
            JSON.stringify(odataConfig),
            dataPath,
            totalPath,
            JSON.stringify(staticQueryParams),
            contextValuesParam,
        ]
    );

    // Set when a rewind-to-page-1 is about to happen, so the pagination effect below
    // skips the duplicate request its caller already issued.
    const depResetInFlightRef = useRef(false);
    // The pagination effect also runs once on mount, where the initial load is owned by
    // the dependency effect. Tracked separately from the rewind guard: conflating the two
    // is what made a real page change get swallowed.
    const paginationEffectRanRef = useRef(false);

    useEffect(() => {
        if (!enabled || !serverOptions) return;
        setPagination((prev) => {
            // Arm the guard ONLY when the rewind actually changes pageIndex — otherwise
            // nothing re-runs the pagination effect to clear it, and the flag survives to
            // eat the user's next real page change instead.
            if (prev.pageIndex === 0) return prev;
            depResetInFlightRef.current = true;
            return { ...prev, pageIndex: 0 };
        });
        fetchData(searchValue, { pageIndex: 0, pageSize: PAGE_SIZE });
    }, [enabled, JSON.stringify(depParams), contextValuesParam]);

    useEffect(() => {
        // Mount run: the dependency effect above owns the initial load, so skip — but
        // record it unconditionally, so a later page change is never mistaken for mount.
        const isMountRun = !paginationEffectRanRef.current;
        paginationEffectRanRef.current = true;

        if (!enabled || !serverOptions) return;
        if (isMountRun) return;
        if (depResetInFlightRef.current) {
            depResetInFlightRef.current = false;
            return;
        }
        fetchData(searchValue, pagination);
    }, [pagination.pageIndex, pagination.pageSize]);

    const setSearchValue = useCallback(
        (val: string) => {
            setSearchValueRaw(val);
            if (!serverOptions) return;
            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => {
                // Same rewind-vs-guard rule as the dependency effect: searching from
                // page 2+ moves pageIndex back to 0, which would otherwise make the
                // pagination effect fire a second, identical request on top of the one
                // issued right below.
                setPagination((prev) => {
                    if (prev.pageIndex === 0) return prev;
                    depResetInFlightRef.current = true;
                    return { ...prev, pageIndex: 0 };
                });
                fetchData(val, { pageIndex: 0, pageSize: PAGE_SIZE });
            }, DEBOUNCE_MS);
        },
        [serverOptions, fetchData]
    );

    const clientFilteredData = useMemo(() => {
        if (serverOptions) return serverData;
        if (!searchValue) return staticOptions;
        const lower = searchValue.toLowerCase();
        return staticOptions.filter((row) =>
            Object.values(row).some((v) => String(v).toLowerCase().includes(lower))
        );
    }, [serverOptions, serverData, staticOptions, searchValue]);

    const effectiveTotalCount = serverOptions ? totalCount : clientFilteredData.length;

    const effectiveData = useMemo(() => {
        if (serverOptions) return clientFilteredData;
        const start = pagination.pageIndex * pagination.pageSize;
        return clientFilteredData.slice(start, start + pagination.pageSize);
    }, [serverOptions, clientFilteredData, pagination]);

    return {
        data: effectiveData,
        loading,
        error,
        totalCount: effectiveTotalCount,
        searchValue,
        setSearchValue,
        pagination,
        setPagination,
    };
}
