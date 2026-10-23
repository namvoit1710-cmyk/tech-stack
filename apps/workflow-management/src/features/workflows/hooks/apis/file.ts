import { fileApi } from "@/lib/api";
import type { AxiosError } from "@ldc/api-sdk";
import type { UseQueryOptions } from "@ldc/tanstack-query";
import { queryKeyFactory, useQuery } from "@ldc/tanstack-query";

const _fileQueryKey = queryKeyFactory("file");
export const fileQueryKeys = {
    ..._fileQueryKey,
    odataById: (file_id: string, params?: Record<string, unknown>) => [..._fileQueryKey.detail("odataById", file_id), params ?? null]
}

export interface IOdataFileResponse<T = unknown> {
    file_id: string;
    data: T[];
}

export const getFileById = (file_id: string): Promise<Blob> => {
    return fileApi.get(`/download/${file_id}`, {
        responseType: "blob",
    });
}

export const getOdataFileById = <T = unknown>(file_id: string, params?: Record<string, unknown>): Promise<IOdataFileResponse<T>> => {
    return fileApi.get(`/odata/${file_id}/data`, params ? { params } : undefined);
}

export const useQueryFileById = (
    { file_id, params }: { file_id: string, params?: Record<string, unknown> },
    options?: Omit<
        UseQueryOptions<
            IOdataFileResponse,
            AxiosError<{ code?: string, errors?: string }>,
            IOdataFileResponse
        >,
        "queryKey" | "queryFn"
    >
) => {
    return useQuery({
        queryKey: fileQueryKeys.odataById(file_id, params),
        queryFn: (): Promise<IOdataFileResponse<unknown>> => getOdataFileById(file_id, params),
        enabled: !!file_id,
        ...options
    });
}