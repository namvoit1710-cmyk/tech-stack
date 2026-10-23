import { MutationCache, QueryCache, QueryClientProvider, getQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { isAxiosError } from "@ldc/api-sdk";
import { useState } from "react";
import { useLanguage } from "@/hooks/use-language";

const QueryProvider = ({ children }: { children: React.ReactNode }) => {
    const { t } = useLanguage();

    const [queryClient] = useState(() => getQueryClient({
        defaultOptions: {
            queries: {
                staleTime: 60 * 60 * 1000,
                refetchOnWindowFocus: false,
                retry: false,
            },
            mutations: {
                retry: false,
            },
        },

        queryCache: new QueryCache({
            onError: (error, query) => {
                if (query.meta?.silent) return;

                if (isAxiosError(error)) {
                    toast.error(t("notification.error"), error.response?.data?.message ?? error.response?.data?.code ?? error.message, {
                        code: error.response?.data?.code,
                        errors: error.response?.data?.errors
                    });
                }
            }
        }),

        mutationCache: new MutationCache({
            onError: (error, _variables, _context, mutation) => {
                if (mutation.meta?.silent) return;

                if (isAxiosError(error)) {
                    toast.error(t("notification.error"), error.response?.data?.message ?? error.response?.data?.code ?? error.message, {
                        code: error.response?.data?.code,
                        errors: error.response?.data?.errors
                    });
                }
            }
        })
    }))

    return (
        <QueryClientProvider client={queryClient}>
            {children}
        </QueryClientProvider>
    );
};

export default QueryProvider;