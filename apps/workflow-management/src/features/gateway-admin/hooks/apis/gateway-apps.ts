import api from "@/lib/api";
import { AxiosError } from "@ldc/api-sdk";
import {
    QueryKey,
    queryKeyFactory,
    useMutation,
    UseMutationOptions,
    useQuery,
    UseQueryOptions,
} from "@ldc/tanstack-query";
import {
    ICreateGatewayAppPayload,
    IGatewayAppDetailResponse,
    IGatewayAppListResponse,
    IGatewayDeleteResponse,
    IGatewayErrorBody,
    IGatewayMutateResponse,
    IGatewaySyncResponse,
    IGatewayTestResponse,
    IUpdateGatewayAppPayload,
} from "../../types/gateway";

export interface IGatewayAppListParams {
    name_contains?: string;
    include_inactive?: boolean;
    skip?: number;
    top?: number;
}

const enc = encodeURIComponent;

const _gatewayAppKey = queryKeyFactory("gateway-apps");

export const gatewayAppKey = {
    ..._gatewayAppKey,
    list: (params?: IGatewayAppListParams) => [..._gatewayAppKey.lists(), params] as const,
    detail: (name: string) => [..._gatewayAppKey.details(), name] as const,
};

type Err = AxiosError<IGatewayErrorBody>;

// Query: list apps
export const useGatewayAppList = (
    params?: IGatewayAppListParams,
    options?: Omit<UseQueryOptions<IGatewayAppListResponse, Err, IGatewayAppListResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: gatewayAppKey.list(params),
        queryFn: (): Promise<IGatewayAppListResponse> => api.get("/gateway/apps", { params }),
        ...options,
    });

// Query: get one app by name
export const useGatewayApp = (
    name: string,
    options?: Omit<UseQueryOptions<IGatewayAppDetailResponse, Err, IGatewayAppDetailResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: gatewayAppKey.detail(name),
        queryFn: (): Promise<IGatewayAppDetailResponse> => api.get(`/gateway/apps/${enc(name)}`),
        enabled: !!name,
        ...options,
    });

// Mutation: create app
export const useCreateGatewayApp = (
    options?: Omit<UseMutationOptions<IGatewayMutateResponse, Err, ICreateGatewayAppPayload>, "mutationFn">
) =>
    useMutation({
        mutationFn: (payload: ICreateGatewayAppPayload): Promise<IGatewayMutateResponse> =>
            api.post("/gateway/apps", payload),
        ...options,
    });

// Mutation: update app — optimistic concurrency via If-Match: <version>
export const useUpdateGatewayApp = (
    options?: Omit<
        UseMutationOptions<IGatewayMutateResponse, Err, { name: string; version: number; payload: IUpdateGatewayAppPayload }>,
        "mutationFn"
    >
) =>
    useMutation({
        mutationFn: ({ name, version, payload }): Promise<IGatewayMutateResponse> =>
            api.patch(`/gateway/apps/${enc(name)}`, payload, { headers: { "If-Match": String(version) } }),
        ...options,
    });

// Mutation: delete (soft) app
export const useDeleteGatewayApp = (
    options?: Omit<UseMutationOptions<IGatewayDeleteResponse, Err, string>, "mutationFn">
) =>
    useMutation({
        mutationFn: (name: string): Promise<IGatewayDeleteResponse> => api.delete(`/gateway/apps/${enc(name)}`),
        ...options,
    });

// Mutation: delete (soft) app by id.
// The name is a URL path segment, so an app whose name carries a `/` cannot be
// reached by ANY verb — its detail page 404s and never renders a Delete button.
// The list row is the only place such a row is visible, and it always has the
// id, so this is the handle the list deletes through.
export const useDeleteGatewayAppById = (
    options?: Omit<UseMutationOptions<IGatewayDeleteResponse, Err, string>, "mutationFn">
) =>
    useMutation({
        mutationFn: (id: string): Promise<IGatewayDeleteResponse> => api.delete(`/gateway/apps/by-id/${enc(id)}`),
        ...options,
    });

// Mutation: test reachability of an app's base_url
export const useTestGatewayApp = (
    options?: Omit<UseMutationOptions<IGatewayTestResponse, Err, { name: string; timeout_s?: number }>, "mutationFn">
) =>
    useMutation({
        mutationFn: ({ name, timeout_s = 5 }): Promise<IGatewayTestResponse> =>
            api.post(`/gateway/apps/${enc(name)}/test`, { timeout_s }, { timeout: 120_000 }),
        ...options,
    });

// Mutation: import + reconcile functions from the app's OpenAPI spec_url
export const useSyncGatewayApp = (
    options?: Omit<UseMutationOptions<IGatewaySyncResponse, Err, { name: string; force?: boolean }>, "mutationFn">
) =>
    useMutation({
        mutationFn: ({ name, force = false }): Promise<IGatewaySyncResponse> =>
            api.post(`/gateway/apps/${enc(name)}/sync`, undefined, { params: { force }, timeout: 120_000 }),
        ...options,
    });
