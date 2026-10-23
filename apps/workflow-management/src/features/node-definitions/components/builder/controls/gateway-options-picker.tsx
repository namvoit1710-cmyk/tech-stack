import { useGatewayAppList } from "@/features/gateway-admin/hooks/apis/gateway-apps";
import { useGatewayFunctionList } from "@/features/gateway-admin/hooks/apis/gateway-functions";
import type { IFieldServerOptions } from "@/features/node-definitions/lib/converters";
import { GATEWAY_OPTIONS_DEFAULT_DATAPATH } from "@/features/node-definitions/lib/gateway-binding";
import { useLanguage } from "@/hooks/use-language";
import { SimpleSelect } from "@/components/simple-select/simple-select";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";

interface Props {
    so: IFieldServerOptions;
    onChange: (patch: Partial<IFieldServerOptions>) => void;
    uid: string;
}

/** Field-options source = invoke a gateway function. Picks app + function from
 *  gateway-admin; the converter turns this into a POST invoke serverOptions. */
const GatewayOptionsPicker = ({ so, onChange, uid }: Props) => {
    const { t } = useLanguage();
    const { data: appsResp } = useGatewayAppList();
    const apps = appsResp?.data?.items ?? [];
    const { data: fnsResp } = useGatewayFunctionList(so.appName ?? "");
    const fns = fnsResp?.data?.items ?? [];

    const onApp = (name: string) => onChange({ appName: name, functionName: "", functionId: "" });
    const onFn = (name: string) => {
        const fn = fns.find((f) => f.name === name);
        onChange({ functionName: name, functionId: fn?.id ?? "" });
    };

    return (
        <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
                <Label htmlFor={`${uid}-gw-app`} className="text-xs text-muted-foreground">{t("node_definition_builder.gateway_app")} *</Label>
                <SimpleSelect id={`${uid}-gw-app`} size="sm" value={so.appName ?? ""} onValueChange={onApp} className="text-sm bg-card"
                    emptyLabel={t("node_definition_builder.select_value")}
                    options={apps.map((a) => ({ value: a.name, label: a.name }))} />
            </div>
            <div className="flex flex-col gap-1">
                <Label htmlFor={`${uid}-gw-fn`} className="text-xs text-muted-foreground">{t("node_definition_builder.gateway_function")} *</Label>
                <SimpleSelect id={`${uid}-gw-fn`} size="sm" value={so.functionName ?? ""} onValueChange={onFn} disabled={!so.appName} className="text-sm bg-card"
                    emptyLabel={t("node_definition_builder.select_value")}
                    options={fns.map((f) => ({ value: f.name, label: f.name }))} />
            </div>
            <div className="flex flex-col gap-1">
                <Label htmlFor={`${uid}-gw-val`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_value")} *</Label>
                <Input id={`${uid}-gw-val`} value={so.valueField} onChange={(e) => onChange({ valueField: e.target.value })} placeholder="id" className="h-8 text-sm" />
            </div>
            <div className="flex flex-col gap-1">
                <Label htmlFor={`${uid}-gw-lbl`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_label")} *</Label>
                <Input id={`${uid}-gw-lbl`} value={so.labelField} onChange={(e) => onChange({ labelField: e.target.value })} placeholder="name" className="h-8 text-sm" />
            </div>
            <div className="col-span-2 flex flex-col gap-1">
                <Label htmlFor={`${uid}-gw-path`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_items_path")}</Label>
                <Input id={`${uid}-gw-path`} value={so.dataPath ?? ""} onChange={(e) => onChange({ dataPath: e.target.value })} placeholder={GATEWAY_OPTIONS_DEFAULT_DATAPATH} className="h-8 text-sm" />
                <span className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_items_path_hint")}</span>
            </div>
        </div>
    );
};

export default GatewayOptionsPicker;
