import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Spinner } from "@ldc/ui/components/spinner";
import { Switch } from "@ldc/ui/components/switch";
import { useEffect, useId, useState } from "react";
import { gatewayAppKey, useCreateGatewayApp, useUpdateGatewayApp } from "../../hooks/apis/gateway-apps";
import {
    AUTH_TYPES,
    GatewayAppKind,
    GatewayAuthType,
    ICreateGatewayAppPayload,
    IGatewayApp,
    IUpdateGatewayAppPayload,
} from "../../types/gateway";

interface Props {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** Present = edit mode. */
    app?: IGatewayApp;
    onSaved?: (name: string) => void;
}

const splitHosts = (raw: string) => raw.split(/[\n,]/).map((h) => h.trim()).filter(Boolean);

const GatewayAppModal = ({ open, onOpenChange, app, onSaved }: Props) => {
    const { t } = useLanguage();
    const uid = useId();
    const queryClient = useQueryClient();
    const isEdit = !!app;

    const [name, setName] = useState("");
    const [kind, setKind] = useState<GatewayAppKind>("internal");
    const [baseUrl, setBaseUrl] = useState("");
    const [hosts, setHosts] = useState("");
    const [authType, setAuthType] = useState<GatewayAuthType>("none");
    const [description, setDescription] = useState("");
    const [specUrl, setSpecUrl] = useState("");
    // credentials are write-only; on edit, off until the user opts to set/rotate
    const [setCredential, setSetCredential] = useState(false);
    const [token, setToken] = useState("");
    const [header, setHeader] = useState("");
    const [headerValue, setHeaderValue] = useState("");

    useEffect(() => {
        if (!open) return;
        setName(app?.name ?? "");
        setKind(app?.kind ?? "internal");
        setBaseUrl(app?.base_url ?? "");
        setHosts((app?.allowed_hosts ?? []).join("\n"));
        setAuthType(app?.auth_type ?? "none");
        setDescription(app?.description ?? "");
        setSpecUrl(""); // spec_url is not in the projection; blank = leave unchanged on edit
        setSetCredential(!isEdit && false);
        setToken(""); setHeader(""); setHeaderValue("");
    }, [open, app, isEdit]);

    const invalidate = () => {
        queryClient.invalidateQueries({ queryKey: gatewayAppKey.lists() });
        if (app) queryClient.invalidateQueries({ queryKey: gatewayAppKey.detail(app.name) });
    };

    const { mutate: create, isPending: creating } = useCreateGatewayApp({
        onSuccess: () => {
            toast.success(t("notification.success"), "Gateway app created.");
            invalidate(); onOpenChange(false); onSaved?.(name.trim());
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Create failed."),
    });
    const { mutate: update, isPending: updating } = useUpdateGatewayApp({
        onSuccess: () => {
            toast.success(t("notification.success"), "Gateway app updated.");
            invalidate(); onOpenChange(false); onSaved?.(name.trim());
        },
        onError: (e) => toast.error(t("notification.error"), e.response?.data?.error?.message || "Update failed."),
    });

    const needsCred = authType === "bearer" || authType === "api_key_header";
    const credProvided =
        authType === "bearer" ? !!token.trim() : authType === "api_key_header" ? !!header.trim() && !!headerValue.trim() : false;
    // On create with an auth type, a credential is required. On edit, only when rotating.
    const credOk = !needsCred || (isEdit ? (!setCredential || credProvided) : credProvided);
    const canSave = !!name.trim() && credOk && (kind === "internal" ? !!baseUrl.trim() : splitHosts(hosts).length > 0);
    const busy = creating || updating;

    const buildAuthConfig = (): Record<string, unknown> | undefined => {
        if (!needsCred) return undefined;
        if (authType === "bearer") return { token: token.trim() };
        return { header: header.trim(), value: headerValue };
    };

    const submit = () => {
        if (!canSave) return;
        if (isEdit && app) {
            const payload: IUpdateGatewayAppPayload = {
                description,
                auth_type: authType,
                ...(name.trim() !== app.name ? { name: name.trim() } : {}),
                ...(kind === "internal" ? { base_url: baseUrl.trim() } : { allowed_hosts: splitHosts(hosts) }),
                ...(specUrl.trim() ? { spec_url: specUrl.trim() } : {}),
                ...(setCredential && credProvided ? { auth_config: buildAuthConfig() } : {}),
            };
            update({ name: app.name, version: app.version, payload });
        } else {
            const payload: ICreateGatewayAppPayload = {
                name: name.trim(),
                kind,
                auth_type: authType,
                description,
                ...(kind === "internal" ? { base_url: baseUrl.trim() } : { allowed_hosts: splitHosts(hosts) }),
                ...(specUrl.trim() ? { spec_url: specUrl.trim() } : {}),
                ...(buildAuthConfig() ? { auth_config: buildAuthConfig() } : {}),
            };
            create(payload);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>{isEdit ? "Edit gateway app" : "Create gateway app"}</DialogTitle>
                    <DialogDescription>
                        Register an internal/external target. Add functions manually or import them from an OpenAPI spec.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4 py-1">
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-name`}>Name <span className="text-destructive">*</span></Label>
                        <Input id={`${uid}-name`} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. orders-api" />
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label>Kind</Label>
                        <Select value={kind} onValueChange={(v) => setKind(v as GatewayAppKind)} disabled={isEdit}>
                            <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="internal">internal</SelectItem>
                                <SelectItem value="external">external</SelectItem>
                            </SelectContent>
                        </Select>
                        {isEdit && <span className="text-xs text-muted-foreground">Kind cannot be changed after creation.</span>}
                    </div>

                    {kind === "internal" ? (
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor={`${uid}-base`}>Base URL <span className="text-destructive">*</span></Label>
                            <Input id={`${uid}-base`} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://service.internal/api" />
                        </div>
                    ) : (
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor={`${uid}-hosts`}>Allowed hosts <span className="text-destructive">*</span></Label>
                            <textarea
                                id={`${uid}-hosts`} value={hosts} onChange={(e) => setHosts(e.target.value)} rows={3}
                                placeholder={"api.partner.com\n*.partner.com"}
                                className="w-full text-sm p-2 border border-border rounded-md resize-y bg-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                            />
                            <span className="text-xs text-muted-foreground">One host per line. One-level wildcard allowed (e.g. *.partner.com).</span>
                        </div>
                    )}

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-spec`}>OpenAPI spec URL</Label>
                        <Input id={`${uid}-spec`} value={specUrl} onChange={(e) => setSpecUrl(e.target.value)} placeholder="https://service/openapi.json" />
                        <span className="text-xs text-muted-foreground">
                            {isEdit ? "Leave blank to keep the current spec URL. " : ""}After saving, use “Sync from OpenAPI” to import functions.
                        </span>
                    </div>

                    <div className="flex flex-col gap-1.5">
                        <Label>Auth type</Label>
                        <Select value={authType} onValueChange={(v) => setAuthType(v as GatewayAuthType)}>
                            <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                {AUTH_TYPES.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>

                    {needsCred && (
                        <div className="flex flex-col gap-2 rounded-md border border-border p-3">
                            {isEdit && (
                                <label className="flex items-center gap-2 text-sm">
                                    <Switch checked={setCredential} onCheckedChange={setSetCredential} />
                                    {app?.auth_summary?.credential_present ? "Rotate credential" : "Set credential"}
                                    {app?.auth_summary?.credential_present && (
                                        <span className="text-xs text-muted-foreground">(a credential is currently stored)</span>
                                    )}
                                </label>
                            )}
                            {(!isEdit || setCredential) && authType === "bearer" && (
                                <div className="flex flex-col gap-1.5">
                                    <Label htmlFor={`${uid}-token`}>Bearer token <span className="text-destructive">*</span></Label>
                                    <Input id={`${uid}-token`} type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
                                </div>
                            )}
                            {(!isEdit || setCredential) && authType === "api_key_header" && (
                                <>
                                    <div className="flex flex-col gap-1.5">
                                        <Label htmlFor={`${uid}-hname`}>Header name <span className="text-destructive">*</span></Label>
                                        <Input id={`${uid}-hname`} value={header} onChange={(e) => setHeader(e.target.value)} placeholder="X-API-Key" autoComplete="off" />
                                    </div>
                                    <div className="flex flex-col gap-1.5">
                                        <Label htmlFor={`${uid}-hval`}>Header value <span className="text-destructive">*</span></Label>
                                        <Input id={`${uid}-hval`} type="password" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)} autoComplete="off" />
                                    </div>
                                </>
                            )}
                        </div>
                    )}

                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor={`${uid}-desc`}>Description</Label>
                        <textarea
                            id={`${uid}-desc`} value={description} onChange={(e) => setDescription(e.target.value)} rows={2}
                            className="w-full text-sm p-2 border border-border rounded-md resize-y bg-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                        />
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>{t("cancel")}</Button>
                    <Button onClick={submit} disabled={!canSave || busy}>
                        {busy && <Spinner className="size-4 mr-2" />}{t("save")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default GatewayAppModal;
