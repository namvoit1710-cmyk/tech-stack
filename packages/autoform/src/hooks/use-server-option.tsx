import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useBuilderContext } from "../contexts/builder.context";
import { IServerOptionsConfig } from "../types/server-option-config";
import { PaginationState } from "@ldc/data-table";
import { buildPageArgs, buildPostBody } from "../lib/gateway-invoke";

// ============================================================================
// Interfaces
// ============================================================================

export interface IPaginationConfig {
    pageSize?: number;
}

// ============================================================================
// Utility
// ============================================================================

/**
 * Resolve nested value by dot-notation path.
 * getByPath({ data: { items: [1,2] } }, "data.items") => [1,2]
 */
const getByPath = (obj: any, path?: string): any => {
    if (!path) return obj;
    return path.split(".").reduce((acc, key) => acc?.[key], obj);
};

// buildPostBody now lives in lib/gateway-invoke.ts (SA-2184 — the shared
// transport every Choice-control server-option hook imports). Re-exported
// here, behaviour identical, so this module's existing test keeps working.
export { buildPostBody };

// ============================================================================
// Hook
// ============================================================================

export const useServerOptions = (
    serverOptions?: IServerOptionsConfig,
    paginationConfig?: IPaginationConfig
) => {
    const { services } = useBuilderContext();
    const { control } = useFormContext();
    const [data, setData] = useState<Record<string, unknown>[]>([]);
    const [loading, setLoading] = useState(false);
    const [totalCount, setTotalCount] = useState(0);
    const abortRef = useRef<AbortController | null>(null);

    const { endpoint, service, dependencies = [], responseMapping, method = "GET", body, pageArgKeys } = serverOptions ?? {};

    const enablePagination = !!paginationConfig;

    // --- Dynamic paths with sensible defaults ---
    const dataPath = responseMapping?.dataPath ?? "data.items";
    const totalPath = responseMapping?.totalPath ?? "data.count";

    const [pagination, setPagination] = useState<PaginationState>({
        pageIndex: 0,
        pageSize: paginationConfig?.pageSize ?? 10,
    });

    const [refetchVersion, setRefetchVersion] = useState(0);

    const refetch = useCallback(async () => {
        setRefetchVersion((v) => v + 1);
    }, []);

    const depValues = useWatch({
        control,
        name: dependencies.length > 0 ? dependencies : [],
        disabled: !dependencies.length,
    });

    const params = useMemo(() => {
        if (!dependencies.length) return {};
        return dependencies.reduce((acc, dep, index) => {
            const value = Array.isArray(depValues) ? depValues[index] : depValues;
            if (value != null && value !== "") {
                acc[dep] = value;
            }
            return acc;
        }, {} as Record<string, any>);
    }, [dependencies, depValues]);

    // The whole handler (fetch/post), so POST sources (e.g. gateway function invoke) work too.
    const serviceEntry = useMemo(() => {
        if (!service || !services) return undefined;
        const resolved = service.split(".").reduce<any>(
            (current, segment) => current?.[segment],
            services
        );
        return typeof resolved?.fetch === "function" ? resolved : undefined;
    }, [services, service]);

    useEffect(() => {
        if (enablePagination) {
            setPagination((prev) => ({ ...prev, pageIndex: 0 }));
        }
    }, [JSON.stringify(params), enablePagination]);

    useEffect(() => {
        if (!endpoint || !serviceEntry) return;

        if (dependencies.length > 0) {
            const hasAllDeps = dependencies.every(
                (dep) => params[dep] != null && params[dep] !== ""
            );
            if (!hasAllDeps) {
                setData([]);
                setTotalCount(0);
                return;
            }
        }

        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;

        let cancelled = false;
        setLoading(true);

        // On the gateway (POST) path the page-arg NAMES are the upstream's own — bound per
        // function, OData by default — and $count must be the string "true" (the gateway
        // percent-encodes booleans as "True"/"False", which OData rejects). Legacy GET
        // keeps the OData names and the real boolean, byte-for-byte as before.
        const isGateway = method === "POST";
        const requestParams = enablePagination
            ? { ...params, ...buildPageArgs(pagination, isGateway ? pageArgKeys : undefined, isGateway ? "true" : true) }
            : params;

        // GET sends query params. POST sends the static body, now MERGED with the
        // dependency values (SA-1551 "POST args" phase 2) so options can depend on a
        // sibling field — e.g. a credential_ref the user just picked, or a team_id.
        // The dependency values are merged under `params.args` (the invoke-proxy shape
        // the worker-function endpoint expects); a plain-object base body is preserved.
        //
        // `requestParams`, not `params`: the POST branch used to merge only the dependency
        // values, so a paginated gateway source silently sent NO page args at all and every
        // page returned the first one. Dormant today (no caller enables pagination on this
        // hook) but a trap for whoever does.
        const postBody = buildPostBody(body, requestParams);
        const request = isGateway
            ? serviceEntry.post(endpoint, postBody)
            : serviceEntry.fetch(endpoint, requestParams);

        request
            .then((response: any) => {
                if (!cancelled) {
                    const items = getByPath(response, dataPath);
                    setData(Array.isArray(items) ? items : []);

                    const total = getByPath(response, totalPath);
                    setTotalCount(typeof total === "number" ? total : 0);
                }
            })
            .catch((error: any) => {
                if (!cancelled) {
                    console.error("Error fetching server options:", error);
                    setData([]);
                    setTotalCount(0);
                }
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [
        endpoint,
        serviceEntry,
        method,
        JSON.stringify(body),
        JSON.stringify(pageArgKeys),
        JSON.stringify(params),
        pagination.pageIndex,
        pagination.pageSize,
        refetchVersion,
        dataPath,
        totalPath,
    ]);

    return {
        data,
        loading,
        pagination,
        setPagination,
        totalCount,
        refetch,
    };
};