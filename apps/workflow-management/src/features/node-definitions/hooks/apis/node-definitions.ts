import { IApiErrorBody } from "@/features/workflows/types/api";
import api from "@/lib/api";
import { AxiosError } from "@ldc/api-sdk";
import { QueryKey, queryKeyFactory, useMutation, UseMutationOptions, useQuery, UseQueryOptions } from "@ldc/tanstack-query";
import {
    ICreateNodeDefinitionPayload,
    INodeDefinitionDetailResponse,
    INodeDefinitionListResponse,
    INodeDefinitionParams,
    ITestNodeDefinitionDataResponse,
    ITestNodeDefinitionPayload,
    IUpdateNodeDefinitionPayload
} from "../../types/node-definition";

const _nodeDefinitionKey = queryKeyFactory("node-definitions");

export const nodeDefinitionKey = {
    ..._nodeDefinitionKey,
    getAll: (params?: INodeDefinitionParams) => [..._nodeDefinitionKey.lists(), params] as const,
    getById: (id: string) => [..._nodeDefinitionKey.details(), id] as const,
};

// Query: List node definitions
export const useNodeDefinitionList = (
    params?: INodeDefinitionParams,
    options?: Omit<UseQueryOptions<INodeDefinitionListResponse, AxiosError<IApiErrorBody>, INodeDefinitionListResponse, QueryKey>, "queryKey" | "queryFn">
) => {
    return useQuery({
        queryKey: nodeDefinitionKey.getAll(params),
        queryFn: (): Promise<INodeDefinitionListResponse> => api.get("/node-definitions", { params }),
        ...options,
    });
};

// Query: Get single node definition
export const useNodeDefinitionById = (
    id: string,
    options?: Omit<UseQueryOptions<INodeDefinitionDetailResponse, AxiosError<IApiErrorBody>, INodeDefinitionDetailResponse, QueryKey>, "queryKey" | "queryFn">
) => {
    return useQuery({
        queryKey: nodeDefinitionKey.getById(id),
        queryFn: (): Promise<INodeDefinitionDetailResponse> => api.get(`/node-definitions/${id}`),
        enabled: !!id,
        ...options,
    });
};

// Mutation: Create node definition
export const useCreateNodeDefinition = (
    options?: Omit<
        UseMutationOptions<INodeDefinitionDetailResponse, AxiosError<IApiErrorBody>, ICreateNodeDefinitionPayload>,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (payload: ICreateNodeDefinitionPayload): Promise<INodeDefinitionDetailResponse> =>
            api.post("/node-definitions", payload),
        ...options,
    });
};

// Mutation: Update node definition
export const useUpdateNodeDefinition = (
    options?: Omit<
        UseMutationOptions<
            INodeDefinitionDetailResponse,
            AxiosError<IApiErrorBody>,
            { id: string; payload: IUpdateNodeDefinitionPayload }
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ id, payload }: { id: string; payload: IUpdateNodeDefinitionPayload }): Promise<INodeDefinitionDetailResponse> =>
            api.patch(`/node-definitions/${id}`, payload),
        ...options,
    });
};

// Mutation: Delete node definition
export const useDeleteNodeDefinition = (
    options?: Omit<
        UseMutationOptions<{ ok: boolean }, AxiosError<IApiErrorBody>, string>,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (id: string): Promise<{ ok: boolean }> => api.delete(`/node-definitions/${id}`),
        ...options,
    });
};

// Mutation: Test node definition
export const useTestNodeDefinition = (
    options?: Omit<
        UseMutationOptions<
            ITestNodeDefinitionDataResponse,
            AxiosError<IApiErrorBody>,
            { id: string; payload: ITestNodeDefinitionPayload }
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: ({ id, payload }: { id: string; payload: ITestNodeDefinitionPayload }): Promise<ITestNodeDefinitionDataResponse> =>
            api.post(`/node-definitions/${id}/test`, payload, { timeout: 120_000 }),
        ...options,
    });
};
