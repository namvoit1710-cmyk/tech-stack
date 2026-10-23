import { useGatewayAppList } from "@/features/gateway-admin/hooks/apis/gateway-apps";
import { useGatewayFunctionList } from "@/features/gateway-admin/hooks/apis/gateway-functions";
import { buildGatewayBinding, type IGatewayBinding, type IParamBinding, type IParsedGatewayBinding } from "@/features/node-definitions/lib/gateway-binding";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { NetworkIcon, ZapIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import SectionShell from "../section-shell";

interface Props {
    /** Apply overwrites the node's Input/Output schema + writes the invoke marker. */
    onApply: (binding: IGatewayBinding) => void;
    /** Saved selection (from the loaded node's marker) to reload into the form. */
    initial?: IParsedGatewayBinding | null;
}

const GatewayActionSection = ({ onApply, initial }: Props) => {
    const { t } = useLanguage();
    const [app, setApp] = useState("");
    const [fnName, setFnName] = useState("");
    const [bindings, setBindings] = useState<Record<string, IParamBinding>>({});
    const [applied, setApplied] = useState(false);

    // Seed the form once from the loaded node's saved binding (don't clobber edits).
    const seededRef = useRef(false);
    useEffect(() => {
        if (seededRef.current || !initial) return;
        seededRef.current = true;
        setApp(initial.app);
        setFnName(initial.function);
        setBindings(Object.fromEntries(initial.bindings.map((b) => [b.key, b])));
    }, [initial]);

    const { data: appsResp, isLoading: appsLoading } = useGatewayAppList();
    const apps = appsResp?.data?.items ?? [];
    const { data: fnsResp, isLoading: fnsLoading } = useGatewayFunctionList(app);
    const fns = fnsResp?.data?.items ?? [];
    const fn = useMemo(() => fns.find((f) => f.name === fnName), [fns, fnName]);

    const onAppChange = (v: string) => { setApp(v); setFnName(""); setBindings({}); setApplied(false); };
    const onFnChange = (v: string) => { setFnName(v); setBindings({}); setApplied(false); };
    const setBinding = (key: string, patch: Partial<IParamBinding>) => {
        setApplied(false);
        setBindings((b) => ({ ...b, [key]: { key, mode: "expose", ...b[key], ...patch } }));
    };
    const modeOf = (key: string) => bindings[key]?.mode ?? "expose";

    const apply = () => {
        if (!fn) return;
        onApply(buildGatewayBinding(fn, Object.values(bindings), app));
        setApplied(true);
    };

    return (
        <SectionShell
            title={t("node_definition_builder.gateway_action_title")}
            help={t("node_definition_builder.gateway_action_help")}
            icon={<NetworkIcon className="size-5" />}
            hideToggle
            guided={
                <div className="flex flex-col gap-5 max-w-2xl">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div className="flex flex-col gap-1.5">
                            <Label>{t("node_definition_builder.gateway_app")} *</Label>
                            <Select value={app} onValueChange={onAppChange} disabled={appsLoading}>
                                <SelectTrigger className="w-full"><SelectValue placeholder={t("node_definition_builder.gateway_app_placeholder")} /></SelectTrigger>
                                <SelectContent>
                                    {apps.map((a) => <SelectItem key={a.id} value={a.name}>{a.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <Label>{t("node_definition_builder.gateway_function")} *</Label>
                            <Select value={fnName} onValueChange={onFnChange} disabled={!app || fnsLoading}>
                                <SelectTrigger className="w-full"><SelectValue placeholder={t("node_definition_builder.gateway_function_placeholder")} /></SelectTrigger>
                                <SelectContent>
                                    {fns.map((f) => <SelectItem key={f.id} value={f.name}>{f.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    {fn && (
                        <div className="flex flex-col gap-2">
                            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                {t("node_definition_builder.gateway_params")}
                            </span>
                            {fn.input_params.length === 0 && (
                                <p className="text-sm text-muted-foreground">{t("node_definition_builder.gateway_no_params")}</p>
                            )}
                            {fn.input_params.map((p) => {
                                const locked = modeOf(p.key) === "lock";
                                return (
                                    <div key={p.key} className="flex items-center gap-3 rounded-md border border-border p-3">
                                        <div className="flex flex-col min-w-0 flex-1">
                                            <span className="text-sm font-medium truncate">{p.label || p.key}</span>
                                            <span className="text-xs text-muted-foreground truncate">{p.key} · {p.type} · {p.location}{p.required ? " · required" : ""}</span>
                                        </div>
                                        {locked && (
                                            <Input
                                                value={bindings[p.key]?.constant ?? ""}
                                                onChange={(e) => setBinding(p.key, { constant: e.target.value })}
                                                placeholder={t("node_definition_builder.gateway_constant_placeholder")}
                                                className="h-8 text-sm max-w-[200px]"
                                            />
                                        )}
                                        <Select value={modeOf(p.key)} onValueChange={(v) => setBinding(p.key, { mode: v as IParamBinding["mode"] })}>
                                            <SelectTrigger className="h-8 w-[130px] shrink-0"><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="expose">{t("node_definition_builder.gateway_expose")}</SelectItem>
                                                <SelectItem value="lock">{t("node_definition_builder.gateway_lock")}</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    <div className="flex items-center gap-3">
                        <Button onClick={apply} disabled={!fn}>
                            <ZapIcon className="size-4" />
                            <span className="ml-1">{t("node_definition_builder.gateway_apply")}</span>
                        </Button>
                        {applied && <span className="text-xs text-muted-foreground">{t("node_definition_builder.gateway_applied")}</span>}
                    </div>
                    <p className="text-xs text-muted-foreground">{t("node_definition_builder.gateway_apply_hint")}</p>
                </div>
            }
        />
    );
};

export default GatewayActionSection;
