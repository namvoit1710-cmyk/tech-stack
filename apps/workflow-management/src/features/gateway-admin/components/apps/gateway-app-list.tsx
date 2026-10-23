import { useMessageBox } from "@/components/providers/messagebox-provider";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Input } from "@ldc/ui/components/input";
import { ChevronRight, KeyRound, SearchIcon, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { gatewayAppKey, useDeleteGatewayAppById, useGatewayAppList } from "../../hooks/apis/gateway-apps";
import { IGatewayApp } from "../../types/gateway";

const GatewayAppList = () => {
    const { t } = useLanguage();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const showMessageBox = useMessageBox();
    const [search, setSearch] = useState("");

    const { data, isLoading } = useGatewayAppList();
    const apps = useMemo(() => data?.data?.items ?? [], [data]);

    // Delete from the list, by id, rather than only from the detail page.
    // An app whose name cannot occupy a URL path segment 404s on its own
    // detail GET, so that page renders "App not found" and no Delete button at
    // all — the row was visible and unremovable. tenant-1 carried 42 of them.
    const { mutate: remove, isPending: removing } = useDeleteGatewayAppById({
        onSuccess: () => {
            toast.success(t("notification.success"), "App deleted.");
            queryClient.invalidateQueries({ queryKey: gatewayAppKey.lists() });
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Delete failed."),
    });

    const handleDelete = async (app: IGatewayApp) => {
        const ok = await showMessageBox(`Delete app "${app.name}" and stop serving its functions?`, "Delete app");
        if (ok) remove(app.id);
    };

    const filtered = useMemo(() => {
        const q = search.toLowerCase();
        if (!q) return apps;
        return apps.filter((a) => a.name.toLowerCase().includes(q) || a.description.toLowerCase().includes(q));
    }, [apps, search]);

    return (
        <section className="flex flex-col gap-4 px-4 pb-6">
            <div className="flex items-center justify-end">
                <div className="relative min-w-[280px] max-w-md flex-1">
                    <Input className="pl-10" placeholder="Search apps…" value={search} onChange={(e) => setSearch(e.target.value)} />
                    <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
                </div>
            </div>

            {(isLoading || removing) && <div className="loader-bar w-full" />}

            {!isLoading && apps.length === 0 && (
                <p className="text-sm text-muted-foreground py-10 text-center">No gateway apps yet. Create one to get started.</p>
            )}

            {filtered.length > 0 && (
                <div className="rounded-md border border-border divide-y divide-border">
                    {filtered.map((a) => (
                        // Row is a div, not a button: the delete control is a
                        // button and nesting one inside another is invalid.
                        <div
                            key={a.id}
                            role="button"
                            tabIndex={0}
                            onClick={() => navigate(`/gateway/apps/${encodeURIComponent(a.name)}`)}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") navigate(`/gateway/apps/${encodeURIComponent(a.name)}`); }}
                            className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/50 transition-colors cursor-pointer"
                        >
                            <div className="flex flex-col min-w-0 flex-1">
                                <span className="text-sm font-medium truncate">{a.name}</span>
                                {a.description && <span className="text-xs text-muted-foreground truncate">{a.description}</span>}
                            </div>
                            <Badge variant="secondary">{a.kind}</Badge>
                            <Badge variant="outline">{a.auth_type}</Badge>
                            {a.auth_summary?.credential_present && <KeyRound className="size-4 text-muted-foreground" aria-label="credential set" />}
                            {!a.is_active && <Badge variant="destructive">inactive</Badge>}
                            <span className="text-xs text-muted-foreground shrink-0">v{a.version}</span>
                            <button
                                type="button"
                                aria-label={`Delete ${a.name}`}
                                onClick={(e) => { e.stopPropagation(); void handleDelete(a); }}
                                disabled={removing}
                                className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors disabled:opacity-50"
                            >
                                <Trash2 className="size-4" />
                            </button>
                            <ChevronRight className="size-4 text-muted-foreground shrink-0" />
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
};

export default GatewayAppList;
