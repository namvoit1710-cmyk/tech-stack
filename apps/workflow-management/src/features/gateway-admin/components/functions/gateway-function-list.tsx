import { useMessageBox } from "@/components/providers/messagebox-provider";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { gatewayFunctionKey, useDeleteGatewayFunction, useGatewayFunctionList } from "../../hooks/apis/gateway-functions";
import { IGatewayFunction } from "../../types/gateway";
import GatewayFunctionModal from "./gateway-function-modal";

const GatewayFunctionList = ({ app }: { app: string }) => {
    const { t } = useLanguage();
    const queryClient = useQueryClient();
    const showMessageBox = useMessageBox();

    const [modalOpen, setModalOpen] = useState(false);
    const [editing, setEditing] = useState<IGatewayFunction | undefined>();

    const { data, isLoading } = useGatewayFunctionList(app);
    const functions = data?.data?.items ?? [];

    const { mutate: remove, isPending: removing } = useDeleteGatewayFunction({
        onSuccess: () => {
            toast.success(t("notification.success"), "Function deleted.");
            queryClient.invalidateQueries({ queryKey: gatewayFunctionKey.list(app) });
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Delete failed."),
    });

    const handleDelete = async (fn: IGatewayFunction) => {
        const ok = await showMessageBox(`Delete function "${fn.name}"?`, "Delete function");
        if (ok) remove({ app, name: fn.name });
    };

    const openCreate = () => { setEditing(undefined); setModalOpen(true); };
    const openEdit = (fn: IGatewayFunction) => { setEditing(fn); setModalOpen(true); };

    return (
        <section className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
                <h5 className="text-sm font-medium">Functions {functions.length > 0 && <span className="text-muted-foreground">({functions.length})</span>}</h5>
                <Button size="sm" onClick={openCreate}><Plus className="size-4 mr-1" /> Add function</Button>
            </div>

            {(isLoading || removing) && <div className="loader-bar w-full" />}

            {!isLoading && functions.length === 0 && (
                <p className="text-sm text-muted-foreground py-6 text-center">
                    No functions yet. Add one manually, or set an OpenAPI spec URL on the app and sync.
                </p>
            )}

            {functions.length > 0 && (
                <div className="rounded-md border border-border divide-y divide-border">
                    {functions.map((fn) => (
                        <div key={fn.id} className="flex items-center gap-3 px-3 py-2">
                            <Badge variant="outline" className="font-mono">{fn.method}</Badge>
                            <div className="flex flex-col min-w-0 flex-1">
                                <span className="text-sm font-medium truncate">{fn.name}</span>
                                <span className="text-xs text-muted-foreground font-mono truncate">{fn.path_template}</span>
                            </div>
                            <span className="text-xs text-muted-foreground shrink-0">{fn.input_params.length} in / {fn.output_fields.length} out</span>
                            <Button variant="ghost" size="icon" onClick={() => openEdit(fn)}><Pencil className="size-4" /></Button>
                            <Button variant="ghost" size="icon" onClick={() => handleDelete(fn)}><Trash2 className="size-4" /></Button>
                        </div>
                    ))}
                </div>
            )}

            <GatewayFunctionModal open={modalOpen} onOpenChange={setModalOpen} app={app} fn={editing} />
        </section>
    );
};

export default GatewayFunctionList;
