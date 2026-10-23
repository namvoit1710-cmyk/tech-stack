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
    ICreateGatewayFunctionPayload,
    IGatewayErrorBody,
    IGatewayFunctionDetailResponse,
    IGatewayFunctionListResponse,
    IGatewayMutateResponse,
    IUpdateGatewayFunctionPayload,
} from "../../types/gateway";

export interface IGatewayFunctionListParams {
    name_contains?: string;
    include_inactive?: boolean;
    skip?: number;
    top?: number;
}

const enc = encodeURIComponent;
const base = (app: string) => `/gateway/apps/${enc(app)}/functions`;

const _gatewayFunctionKey = queryKeyFactory("gateway-functions");

export const gatewayFunctionKey = {
    ..._gatewayFunctionKey,
    list: (app: string, params?: IGatewayFunctionListParams) =>
        [..._gatewayFunctionKey.lists(), app, params] as const,
    detail: (app: string, name: string) => [..._gatewayFunctionKey.details(), app, name] as const,
};

type Err = AxiosError<IGatewayErrorBody>;

// Query: list an app's functions
export const useGatewayFunctionList = (
    app: string,
    params?: IGatewayFunctionListParams,
    options?: Omit<UseQueryOptions<IGatewayFunctionListResponse, Err, IGatewayFunctionListResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: gatewayFunctionKey.list(app, params),
        queryFn: (): Promise<IGatewayFunctionListResponse> => api.get(base(app), { params }),
        enabled: !!app,
        ...options,
    });

// Query: get one function by name within an app
export const useGatewayFunction = (
    app: string,
    name: string,
    options?: Omit<UseQueryOptions<IGatewayFunctionDetailResponse, Err, IGatewayFunctionDetailResponse, QueryKey>, "queryKey" | "queryFn">
) =>
    useQuery({
        queryKey: gatewayFunctionKey.detail(app, name),
        queryFn: (): Promise<IGatewayFunctionDetailResponse> => api.get(`${base(app)}/${enc(name)}`),
        enabled: !!app && !!name,
        ...options,
    });

// Mutation: create function
export const useCreateGatewayFunction = (
    options?: Omit<UseMutationOptions<IGatewayMutateResponse, Err, { app: string; payload: ICreateGatewayFunctionPayload }>, "mutationFn">
) =>
    useMutation({
        mutationFn: ({ app, payload }): Promise<IGatewayMutateResponse> => api.post(base(app), payload),
        ...options,
    });

// Mutation: update function (addressed by current name)
export const useUpdateGatewayFunction = (
    options?: Omit<UseMutationOptions<IGatewayMutateResponse, Err, { app: string; name: string; payload: IUpdateGatewayFunctionPayload }>, "mutationFn">
) =>
    useMutation({
        mutationFn: ({ app, name, payload }): Promise<IGatewayMutateResponse> =>
            api.patch(`${base(app)}/${enc(name)}`, payload),
        ...options,
    });

// Mutation: delete (soft) function
export const useDeleteGatewayFunction = (
    options?: Omit<UseMutationOptions<{ status: string; data: Record<string, unknown> }, Err, { app: string; name: string }>, "mutationFn">
) =>
    useMutation({
        mutationFn: ({ app, name }) => api.delete(`${base(app)}/${enc(name)}`),
        ...options,
    });
