import { initializeI18n } from "@ldc/i18n";
import { addReteEditorResources } from "@ldc/workflow-editor";
import { NuqsAdapter } from "nuqs/adapters/react";
import { AppRoutes } from "./app";

import { AuthProvider } from "./components/providers/auth-provider";
import { BffAuthGate } from "./components/providers/bff-auth-gate";
import { MessageBoxProvider } from "./components/providers/messagebox-provider";
import QueryProvider from "./components/providers/query-client-provider";

import enWorkflow from "./locales/en/workflow-management.json";
import jpWorkflow from "./locales/jp/workflow-management.json";

const setupI18n = async () => {
    const i18nInstance = await initializeI18n({
        en: { "workflow-management": enWorkflow },
        jp: { "workflow-management": jpWorkflow },
    });
    addReteEditorResources(i18nInstance);
};

setupI18n().catch((error) => {
    console.error("[i18n] Failed to initialize, falling back to default language", error);
});

export default function AppWithProviders() {
    return (
        <QueryProvider>
            <AuthProvider>
                <BffAuthGate>
                    <NuqsAdapter>
                        <MessageBoxProvider>
                            <AppRoutes />
                        </MessageBoxProvider>
                    </NuqsAdapter>
                </BffAuthGate>
            </AuthProvider>
        </QueryProvider>
    );
}