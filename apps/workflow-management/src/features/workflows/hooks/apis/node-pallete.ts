import api, { apiV2 } from "@/lib/api";
import type { AxiosError } from "@ldc/api-sdk";
import type { QueryKey, UseQueryOptions } from "@ldc/tanstack-query";
import { queryKeyFactory, queryOptions, useQuery } from "@ldc/tanstack-query";
import type { IApiErrorBody } from "../../types/api";
import type { INodeCatalogDetailResponse, INodeCatalogResponse, INodePalleteResponse } from "../../types/node-pallete";

const _queryWorker = queryKeyFactory("workers")

export const nodepalleteKey = {
    ..._queryWorker,
    getAllNodePalletes: () => [..._queryWorker.lists(), "get-all-node-palletes"] as const,
    getNodeComprehensive: (nodeId: string, runId: string) => [..._queryWorker.details(), "node-comprehensive", nodeId, runId] as const,
    getOutputSchemaHierarchical: (nodeId: string, runId: string) => [..._queryWorker.details(), "output-schema-hierarchical", nodeId, runId] as const,
    getNodeCatalog: () => [..._queryWorker.lists(), "get-node-catalog"] as const,
}

type queryNodePalleteOptionsType = Omit<UseQueryOptions<INodePalleteResponse, AxiosError<IApiErrorBody>, INodePalleteResponse, QueryKey>, "queryKey" | "queryFn">

export const queryNodePalleteOptions = (options?: queryNodePalleteOptionsType) => {
    return queryOptions({
        queryKey: nodepalleteKey.getAllNodePalletes(),
        queryFn: () => api.get<INodePalleteResponse>("node-palette"),
        ...options
    })
}

export const useQueryNodePallete = (options?: queryNodePalleteOptionsType) => {
    return useQuery(queryNodePalleteOptions(options))
}

type queryNodeCatalogDetailOptionsType = Omit<UseQueryOptions<INodeCatalogDetailResponse, AxiosError<IApiErrorBody>, INodeCatalogDetailResponse, QueryKey>, "queryKey" | "queryFn">

export const useQueryNodeCatalogDetail = (params: { ref: string }, options?: queryNodeCatalogDetailOptionsType) => {
    return useQuery({
        queryKey: [...nodepalleteKey.getNodeCatalog(), params.ref] as const,
        queryFn: () => apiV2.get<INodeCatalogDetailResponse>(`/node-catalog/${params.ref}`),
        ...options
    })
}

type queryNodeCatalogOptionsType = Omit<UseQueryOptions<INodeCatalogResponse, AxiosError<IApiErrorBody>, INodeCatalogResponse, QueryKey>, "queryKey" | "queryFn">

export const useQueryNodeCatalog = (options?: queryNodeCatalogOptionsType) => {
    return useQuery({
        queryKey: nodepalleteKey.getNodeCatalog(),
        queryFn: () => apiV2.get<INodeCatalogResponse>("/node-catalog"),
        ...options
    })
}