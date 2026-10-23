import { initializeI18n } from "@ldc/i18n";
import { addReteEditorResources } from "@ldc/workflow-editor";
import React from "react";
import ReactDOM from "react-dom/client";

import "./main.css";

import { NuqsAdapter } from "nuqs/adapters/react";

import App from "./app";
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

const renderApp = () => {
    const rootElement = document.getElementById("root");
    if (!rootElement) return;

    ReactDOM.createRoot(rootElement).render(
        <React.StrictMode>
            <QueryProvider>
                <NuqsAdapter>
                    <AuthProvider>
                        <BffAuthGate>
                            <MessageBoxProvider>
                                <App />
                            </MessageBoxProvider>
                        </BffAuthGate>
                    </AuthProvider>
                </NuqsAdapter>
            </QueryProvider>
        </React.StrictMode>
    );
};

setupI18n().catch((error) => {
    console.error("[i18n] Failed to initialize, falling back to default language", error);
});

renderApp();
