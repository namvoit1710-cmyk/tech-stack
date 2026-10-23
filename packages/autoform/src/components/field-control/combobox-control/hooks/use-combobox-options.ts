import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useBuilderContext } from "../../../../contexts/builder.context";
import { buildPostBody, DEFAULT_SEARCH_ARG_KEY, GATEWAY_INVOKE_DATAPATH } from "../../../../lib/gateway-invoke";
import type { IServerOptionsConfig } from "../../../../types/server-option-config";
import type { IComboboxOption, OptionRecord } from "../types/types";

export interface IUseComboboxOptionsProps {
    options?: IComboboxOption[] | OptionRecord[];
    valueKey?: string;
    labelKey?: string;
    serverOptions?: IServerOptionsConfig;
}

const DEBOUNCE_MS = 300;

const getByPath = (obj: unknown, path?: string): unknown => {
    if (!path) return obj;
    return path.split(".").reduce<unknown>((acc, key) => (acc as Record<string, unknown>)?.[key], obj);
};

export function useComboboxOptions({
    options = [],
    valueKey = "id",
    labelKey = "value",
    serverOptions,
}: IUseComboboxOptionsProps) {
    const { services } = useBuilderContext();
    const { control } = useFormContext();
    const [serverData, setServerData] = useState<IComboboxOption[]>([]);
    const [loading, setLoading] = useState(false);
    const [inputValue, setInputValue] = useState("");
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        return () => {
            if (debounceRef.current) clearTimeout(debounceRef.current);
            abortRef.current?.abort();
        };
    }, []);

    const normalize = useCallback(
        (raw: OptionRecord): IComboboxOption => ({
            id: String(raw[valueKey]),
            value: String(raw[labelKey]),
        }),
        [valueKey, labelKey]
    );

    const staticOptions = useMemo<IComboboxOption[]>(
        () => (Array.isArray(options) ? (options as OptionRecord[]) : []).map(normalize),
        [options, normalize]
    );

    const {
        endpoint,
        service,
        dependencies = [],
        mode = "default",
        responseMapping,
        odataConfig,
        method = "GET",
        body,
        searchArgKey,
    } = serverOptions ?? {};

    // POST (gateway) responses double-wrap: real payload at data.result.body.
    // GET (legacy REST) keeps the pre-existing data.items default.
    const dataPath = responseMapping?.dataPath ?? (method === "POST" ? GATEWAY_INVOKE_DATAPATH : "data.items");

    const depValues = useWatch({
        control,
        name: dependencies.length > 0 ? dependencies : [],
        disabled: !dependencies.length,
    });

    const depParams = useMemo(() => {
        if (!dependencies.length) return {};
        return dependencies.reduce((acc, dep, index) => {
            const value = Array.isArray(depValues) ? depValues[index] : depValues;
            if (value != null && value !== "") acc[dep] = value;
            return acc;
        }, {} as Record<string, unknown>);
    }, [dependencies, depValues]);

    interface Handler {
        fetch: (endpoint: string, params: Record<string, unknown>) => Promise<unknown>;
        post: (endpoint: string, data: unknown) => Promise<unknown>;
    }
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

    const fetchOptions = useCallback(
        (search: string) => {
            if (!endpoint || !serviceEntry) return;

            if (dependencies.length > 0) {
                const hasAllDeps = dependencies.every((dep) => depParams[dep] != null && depParams[dep] !== "");
                if (!hasAllDeps) {
                    setServerData([]);
                    return;
                }
            }

            abortRef.current?.abort();
            const controller = new AbortController();
            abortRef.current = controller;
            let cancelled = false;
            setLoading(true);

            const params: Record<string, unknown> = { ...depParams };

            if (search) {
                if (mode === "odata" && odataConfig?.searchField) {
                    const existing = odataConfig.defaultFilter ? `(${odataConfig.defaultFilter}) and ` : "";
                    const escapedSearch = search.toLowerCase().replace(/'/g, "''");
                    params.$filter = `${existing}contains(toLower(${odataConfig.searchField}), '${escapedSearch}')`;
                    params.$top = 50;
                    params.$skip = 0;
                    params.$count = true;
                } else if (mode === "default") {
                    params.$search = search;
                }
            } else if (mode === "odata") {
                if (odataConfig?.defaultFilter) params.$filter = odataConfig.defaultFilter;
                if (odataConfig?.defaultOrderBy) params.$orderby = odataConfig.defaultOrderBy;
                if (odataConfig?.defaultSelect) params.$select = odataConfig.defaultSelect;
            }

            // POST = gateway function-invoke: the typed term rides args[searchArgKey] (default
            // "$search"), merged with the dependency values under body.params.args — NOT the
            // mode-driven $search/$filter query building, which is legacy-REST (GET) only.
            // GET sends search/dep params as query params.
            const request = method === "POST"
                ? serviceEntry.post(
                      endpoint,
                      buildPostBody(body, { ...depParams, ...(search ? { [searchArgKey ?? DEFAULT_SEARCH_ARG_KEY]: search } : {}) })
                  )
                : serviceEntry.fetch(endpoint, params);

            request
                .then((response) => {
                    if (!cancelled) {
                        const items = getByPath(response, dataPath);
                        setServerData(Array.isArray(items) ? (items as OptionRecord[]).map(normalize) : []);
                    }
                })
                .catch(() => {
                    if (!cancelled) setServerData([]);
                })
                .finally(() => {
                    if (!cancelled) setLoading(false);
                });

            return () => {
                cancelled = true;
                controller.abort();
            };
        },
        [endpoint, serviceEntry, method, body, dependencies, depParams, mode, odataConfig, dataPath, normalize, searchArgKey]
    );

    useEffect(() => {
        if (!serverOptions) return;
        return fetchOptions(inputValue);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [JSON.stringify(depParams)]);

    const handleSetInputValue = useCallback(
        (val: string) => {
            setInputValue(val);
            if (!serverOptions) return;
            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => fetchOptions(val), DEBOUNCE_MS);
        },
        [serverOptions, fetchOptions]
    );

    const filteredOptions = useMemo(() => {
        if (serverOptions) return serverData;
        if (!inputValue) return staticOptions;
        const lower = inputValue.toLowerCase();
        return staticOptions.filter((o) => o.value.toLowerCase().includes(lower));
    }, [serverOptions, serverData, staticOptions, inputValue]);

    return {
        filteredOptions,
        loading,
        inputValue,
        setInputValue: handleSetInputValue,
    };
}
