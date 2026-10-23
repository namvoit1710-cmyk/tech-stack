import Page from "@/components/containers/page";
import { useMessageBox } from "@/components/providers/messagebox-provider";
import GatewayAppModal from "@/features/gateway-admin/components/apps/gateway-app-modal";
import GatewayFunctionList from "@/features/gateway-admin/components/functions/gateway-function-list";
import { gatewayAppKey, useDeleteGatewayApp, useGatewayApp, useSyncGatewayApp, useTestGatewayApp } from "@/features/gateway-admin/hooks/apis/gateway-apps";
import { gatewayFunctionKey } from "@/features/gateway-admin/hooks/apis/gateway-functions";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Spinner } from "@ldc/ui/components/spinner";
import { ArrowLeft, Pencil, RefreshCw, Trash2, Wifi } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

const GatewayAppDetailPage = () => {
    const { t } = useLanguage();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const showMessageBox = useMessageBox();
    const { appName = "" } = useParams<{ appName: string }>();

    const [editOpen, setEditOpen] = useState(false);

    const { data, isLoading, isError } = useGatewayApp(appName);
    const app = data?.data?.app;

    const { mutate: test, isPending: testing } = useTestGatewayApp({
        onSuccess: (res) => {
            const r = res.data;
            if (r.reachable) toast.success("Reachable", `HTTP ${r.status_code} · ${Math.round(r.latency_ms)} ms`);
            else toast.error("Unreachable", r.upstream_error || `HTTP ${r.status_code}`);
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Test failed."),
    });

    const { mutate: sync, isPending: syncing } = useSyncGatewayApp({
        onSuccess: (res) => {
            const r = res.data;
            if (r.skipped) toast.success("Up to date", "Spec unchanged — nothing to import.");
            else toast.success("Synced", `+${r.created} created · ${r.updated} updated · ${r.deleted} removed · ${r.failed} failed`);
            queryClient.invalidateQueries({ queryKey: gatewayFunctionKey.list(appName) });
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Sync failed."),
    });

    const { mutate: remove, isPending: removing } = useDeleteGatewayApp({
        onSuccess: () => {
            toast.success(t("notification.success"), "App deleted.");
            queryClient.invalidateQueries({ queryKey: gatewayAppKey.lists() });
            navigate("/gateway/apps");
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Delete failed."),
    });

    const handleDelete = async () => {
        const ok = await showMessageBox(`Delete app "${appName}" and stop serving its functions?`, "Delete app");
        if (ok) remove(appName);
    };

    return (
        <Page>
            <Page.Header
                title={
                    <span className="flex items-center gap-2">
                        <Button variant="ghost" size="icon" onClick={() => navigate("/gateway/apps")}><ArrowLeft className="size-4" /></Button>
                        {appName}
                    </span>
                }
                actions={
                    app && (
                        <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => test({ name: appName })} disabled={testing}>
                                {testing ? <Spinner className="size-4 mr-1" /> : <Wifi className="size-4 mr-1" />} Test
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => sync({ name: appName })} disabled={syncing}>
                                {syncing ? <Spinner className="size-4 mr-1" /> : <RefreshCw className="size-4 mr-1" />} Sync from OpenAPI
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => sync({ name: appName, force: true })} disabled={syncing}>
                                Force re-sync
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}><Pencil className="size-4 mr-1" /> Edit</Button>
                            <Button variant="destructive" size="sm" onClick={handleDelete} disabled={removing}><Trash2 className="size-4 mr-1" /> Delete</Button>
                        </div>
                    )
                }
            />

            <div className="flex flex-col gap-6 px-4 pb-6 overflow-y-auto">
                {isLoading && <div className="loader-bar w-full" />}
                {isError && <p className="text-sm text-destructive">App not found.</p>}

                {app && (
                    <>
                        <div className="rounded-md border border-border p-4 flex flex-col gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="secondary">{app.kind}</Badge>
                                <Badge variant="outline">{app.auth_type}</Badge>
                                {app.auth_summary?.credential_present
                                    ? <Badge variant="outline">credential set (v{app.auth_summary.key_version})</Badge>
                                    : <Badge variant="outline">no credential</Badge>}
                                {!app.is_active && <Badge variant="destructive">inactive</Badge>}
                                <span className="ml-auto text-xs text-muted-foreground">version {app.version}</span>
                            </div>
                            {app.description && <p className="text-sm text-muted-foreground">{app.description}</p>}
                            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                                {app.kind === "internal" ? (
                                    <><dt className="text-muted-foreground">Base URL</dt><dd className="font-mono truncate">{app.base_url || "—"}</dd></>
                                ) : (
                                    <><dt className="text-muted-foreground">Allowed hosts</dt><dd className="font-mono truncate">{app.allowed_hosts.join(", ") || "—"}</dd></>
                                )}
                            </dl>
                        </div>

                        <GatewayFunctionList app={appName} />
                    </>
                )}
            </div>

            {app && <GatewayAppModal open={editOpen} onOpenChange={setEditOpen} app={app} />}
        </Page>
    );
};

export default GatewayAppDetailPage;
