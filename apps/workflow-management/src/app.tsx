// Deploy trigger: workflow-management consumes @ldc/autoform (SA-2177 code actions +
// gateway.invoke). This marker forces change-detection to redeploy the app after an
// autoform-only change lands, so the built shell ships the new package. [SA-2177]
import { ThemeProvider } from "@ldc/ui";
import { ToasterRoot } from "@ldc/ui/blocks/toast/components/toast-root";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import MainLayout from "./components/layouts/main-layout";
import CredentialCreatePage from "./pages/credential-create";
import DashboardOverviewPage from "./pages/dashboard-overview";
import DashboardWorkerPage from "./pages/dashboard-worker";
import DashboardWorkflowPage from "./pages/dashboard-workflow";
import CredentialEditPage from "./pages/credential-edit";
import CredentialsPage from "./pages/credentials";
import EnvironmentsPage from "./pages/environments";
import GatewayAppDetailPage from "./pages/gateway-app-detail";
import GatewayAppsPage from "./pages/gateway-apps";
import NodeDefinitionBuilderPage from "./pages/node-definition-builder";
import NodeDefinitionsPage from "./pages/node-definitions";
import ReplayDebugPage from "./pages/replay-debug";
import SettingsPage from "./pages/settings";
import TriggersPage from "./pages/triggers";
import WorkerHealthPage from "./pages/worker-health";
import WorkflowDetailPage from "./pages/workflow-detail";
import WorkflowsPage from "./pages/workflows";

export const AppRoutes = () => {
    return (
        <ThemeProvider defaultMode="system">
            <Routes>
                <Route element={<MainLayout />}>
                    <Route index element={<WorkflowsPage />} />
                    <Route path="workflow/:workflowId" element={<WorkflowDetailPage />} />
                    <Route path="workflow/:workflowId/replay/:runId" element={<ReplayDebugPage />} />
                    <Route path="node-definitions" element={<NodeDefinitionsPage />} />
                    <Route path="workers" element={<WorkerHealthPage />} />
                    <Route path="node-definitions/:definitionId" element={<NodeDefinitionBuilderPage />} />
                    <Route path="gateway/apps" element={<GatewayAppsPage />} />
                    <Route path="gateway/apps/:appName" element={<GatewayAppDetailPage />} />
                    <Route path="credentials" element={<CredentialsPage />} />
                    <Route path="credentials/create" element={<CredentialCreatePage />} />
                    <Route path="credentials/:id/edit" element={<CredentialEditPage />} />
                    <Route path="environments" element={<EnvironmentsPage />} />
                    <Route path="triggers" element={<TriggersPage />} />
                    <Route path="dashboard" element={<Navigate to="/dashboard/overview" replace />} />
                    <Route path="dashboard/overview" element={<DashboardOverviewPage />} />
                    <Route path="dashboard/workflow" element={<DashboardWorkflowPage />} />
                    <Route path="dashboard/worker" element={<DashboardWorkerPage />} />
                    <Route path="settings" element={<SettingsPage />} />
                </Route>
            </Routes>
            <ToasterRoot />
        </ThemeProvider>
    );
};

const App = () => {
    return (
        <BrowserRouter>
            <AppRoutes />
        </BrowserRouter>
    );
};

export default App;
