import { createApiSdk } from "@ldc/api-sdk";
import { useMemo } from "react";

interface ServiceConfig {
  baseUrl: string;
  basePath?: string;
  headers?: Record<string, string>;
  withCredentials?: boolean;
  csrfUrl?: string;
  getDevToken?: () => string | null;
  onSessionExpired?: () => void;
}

type ServiceFetcher = (endpoint: string, params?: Record<string, any>, headers?: Record<string, string>) => Promise<any>;
type ServiceMutator = (endpoint: string, data?: any, headers?: Record<string, string>) => Promise<any>;
type ServiceDeleter = (endpoint: string, headers?: Record<string, string>) => Promise<any>;

export interface ServiceHandler {
  fetch: ServiceFetcher;
  post: ServiceMutator;
  put: ServiceMutator;
  patch: ServiceMutator;
  delete: ServiceDeleter;
}

export type BuilderServices = Record<string, ServiceHandler>;

const joinBaseUrl = (baseUrl: string, basePath: string): string => {
  const normalizedBaseUrl = (baseUrl ?? "").replace(/\/+$/, "");
  const normalizedBasePath = (basePath ?? "").replace(/^\/+|\/+$/g, "");
  return `${normalizedBaseUrl}/${normalizedBasePath}`;
};

const createServiceHandler = (config: ServiceConfig): ServiceHandler => {
  const { baseUrl, basePath = "/api/v1", headers, withCredentials, csrfUrl, getDevToken, onSessionExpired } = config;

  const sdk = createApiSdk({
    baseURL: joinBaseUrl(baseUrl, basePath),
    withCredentials: withCredentials ?? false,
    timeout: 5 * 60 * 1000,
    headers,
    ...(csrfUrl !== undefined ? { csrfUrl } : {}),
    ...(getDevToken !== undefined ? { getDevToken } : {}),
    ...(onSessionExpired !== undefined ? { onSessionExpired } : {}),
  });

  return {
    fetch: async (endpoint, params, reqHeaders) => {
      return sdk.get(endpoint, {
        params,
        headers: { ...headers, ...reqHeaders },
      });
    },
    post: async (endpoint, data, reqHeaders) => {
      return sdk.post(endpoint, data, { headers: reqHeaders });
    },
    put: async (endpoint, data, reqHeaders) => {
      return sdk.put(endpoint, data, { headers: reqHeaders });
    },
    patch: async (endpoint, data, reqHeaders) => {
      return sdk.patch(endpoint, data, { headers: reqHeaders });
    },
    delete: async (endpoint, reqHeaders) => {
      return sdk.delete(endpoint, { headers: reqHeaders });
    },
  };
};

export const useBuilderServices = (
  configs: Record<string, ServiceConfig>
): BuilderServices => {
  return useMemo(() => {
    return Object.entries(configs).reduce((acc, [name, config]) => {
      acc[name] = createServiceHandler(config);
      return acc;
    }, {} as BuilderServices);
  }, [configs]);
};