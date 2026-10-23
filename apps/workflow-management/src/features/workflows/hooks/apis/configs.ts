import api from "@/lib/api";
import type { IConfigParameter, IConfigParametersResponse } from "../../types/configs";
import type { UseQueryOptions, UseMutationOptions } from "@ldc/tanstack-query";
import { queryKeyFactory, useQuery, useMutation } from "@ldc/tanstack-query";

const _configKeys = queryKeyFactory("workflow-config");

export const configKeys = {
    ..._configKeys,
    getAllConfigParameters: () => [..._configKeys.lists(), "get-all-config-parameters"],
};

const getAllConfigParameters = async (workflowId: string): Promise<IConfigParametersResponse> => {
    return api.get(`/workflows/${workflowId}/config/parameters`)
};

export const useQueryConfigParameters = (
    workflowId: string,
    options?: Omit<UseQueryOptions<Awaited<ReturnType<typeof getAllConfigParameters>>>, "queryKey" | "queryFun">
) => {
    return useQuery({
        queryKey: configKeys.getAllConfigParameters(),
        queryFn: () => getAllConfigParameters(workflowId),
        ...options,
    })
}

const updateConfigParameters = async (workflowId: string, payload: { parameters: IConfigParameter[] }) => {
    return api.put(`/workflows/${workflowId}/config/parameters`, payload)
}

export const useMutateConfigParameters = (
    options?: Omit<
        UseMutationOptions<
            Awaited<ReturnType<typeof updateConfigParameters>>,
            Error,
            { workflowId: string; parameters: IConfigParameter[] }
        >,
        "mutationFn"
    >
) => {
    return useMutation({
        mutationFn: (payload: { workflowId: string, parameters: IConfigParameter[] }) => updateConfigParameters(payload.workflowId, { parameters: payload.parameters }),
        ...options,
    })
}