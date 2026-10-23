import { useLanguage } from "@/hooks/use-language";
import { SimpleSelect } from "@/components/simple-select/simple-select";
import { SERVICE_CONFIGS } from "@/constants/config";
import {
    FIELD_CONTROL_OPTIONS,
    GATEWAY_APPS_SERVER_OPTIONS,
    OUTPUT_TYPE_OPTIONS,
    isSchemaFieldValid,
    type IFieldServerOptions,
    type ISchemaFieldItem,
} from "@/features/node-definitions/lib/converters";
import { GATEWAY_OPTIONS_DEFAULT_DATAPATH } from "@/features/node-definitions/lib/gateway-binding";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { ChevronDownIcon, ChevronUpIcon, GripVerticalIcon, TrashIcon } from "lucide-react";
import { useId, useState } from "react";
import GatewayOptionsPicker from "./gateway-options-picker";

const OPTION_CONTROLS = new Set(["SelectControl", "ComboBoxControl", "MultipleComboboxControl"]);
const SERVICE_KEYS = Object.keys(SERVICE_CONFIGS);
const BLANK_SERVER_OPTIONS: IFieldServerOptions = { service: "", endpoint: "", valueField: "id", labelField: "value", dependencies: "" };

interface FieldRowProps {
    field: ISchemaFieldItem;
    index: number;
    total: number;
    onChange: (index: number, field: ISchemaFieldItem) => void;
    onRemove: (index: number) => void;
    onMoveUp: (index: number) => void;
    onMoveDown: (index: number) => void;
}

/** One input-schema field. Replaces the former schema-field-editor with proper
 *  label associations and humanized type/control vocabulary. */
const FieldRow = ({ field, index, total, onChange, onRemove, onMoveUp, onMoveDown }: FieldRowProps) => {
    const { t } = useLanguage();
    const [expanded, setExpanded] = useState(true);
    const uid = useId();

    const update = (partial: Partial<ISchemaFieldItem>) => onChange(index, { ...field, ...partial });
    const showOptions = OPTION_CONTROLS.has(field.fieldControl);
    const typeLabel = OUTPUT_TYPE_OPTIONS.find((o) => o.id === field.outputType)?.label ?? field.outputType;

    const so = field.serverOptions;
    const dynamic = !!so;
    const source = so?.source ?? "manual";
    const setSO = (patch: Partial<IFieldServerOptions>) => update({ serverOptions: { ...(so ?? BLANK_SERVER_OPTIONS), ...patch } });
    const setSource = (src: "manual" | "gateway") =>
        setSO(src === "gateway"
            ? { source: "gateway", dataPath: so?.dataPath || GATEWAY_OPTIONS_DEFAULT_DATAPATH }
            : { source: "manual" });
    const soIncomplete = dynamic && !isSchemaFieldValid(field);

    return (
        <div className="border border-border rounded-lg bg-card">
            <div className="flex items-center gap-2 px-3 py-2 rounded-t-lg hover:bg-muted/40">
                <GripVerticalIcon className="size-4 text-muted-foreground shrink-0" aria-hidden />
                <button
                    type="button"
                    className="flex-1 flex items-center gap-2 min-w-0 text-left"
                    onClick={() => setExpanded((v) => !v)}
                    aria-expanded={expanded}
                >
                    <span className="text-sm font-medium truncate">
                        {field.label || field.key || t("node_definition_builder.unnamed_field")}
                    </span>
                    <span className="text-xs text-muted-foreground shrink-0">{typeLabel}</span>
                    {field.required && <span className="text-xs text-destructive shrink-0" aria-label="required">*</span>}
                </button>
                <div className="flex items-center gap-1 shrink-0">
                    <Button variant="ghost" size="icon" className="size-7" disabled={index === 0} onClick={() => onMoveUp(index)} aria-label={t("node_definition_builder.move_up")}>
                        <ChevronUpIcon className="size-3" />
                    </Button>
                    <Button variant="ghost" size="icon" className="size-7" disabled={index === total - 1} onClick={() => onMoveDown(index)} aria-label={t("node_definition_builder.move_down")}>
                        <ChevronDownIcon className="size-3" />
                    </Button>
                    <Button variant="ghost" size="icon" className="size-7 text-destructive hover:text-destructive" onClick={() => onRemove(index)} aria-label={t("node_definition_builder.remove_field")}>
                        <TrashIcon className="size-3" />
                    </Button>
                </div>
            </div>

            {expanded && (
                <div className="px-4 pb-4 pt-2 border-t border-border/60 grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${uid}-key`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_key")} *</Label>
                        <Input id={`${uid}-key`} value={field.key} onChange={(e) => update({ key: e.target.value.replace(/\s/g, "_") })} placeholder="e.g. search_query" className="h-8 text-sm" />
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${uid}-label`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_label")}</Label>
                        <Input id={`${uid}-label`} value={field.label} onChange={(e) => update({ label: e.target.value })} placeholder="e.g. Search Query" className="h-8 text-sm" />
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${uid}-type`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_data_type")}</Label>
                        <SimpleSelect id={`${uid}-type`} size="sm" value={field.outputType} onValueChange={(v) => update({ outputType: v })} className="text-sm bg-card"
                            options={OUTPUT_TYPE_OPTIONS.map((o) => ({ value: o.id, label: o.label }))} />
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${uid}-control`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_control_type")}</Label>
                        <SimpleSelect id={`${uid}-control`} size="sm" value={field.fieldControl} onValueChange={(v) => update({ fieldControl: v, ...(OPTION_CONTROLS.has(v) ? {} : { serverOptions: undefined }) })} className="text-sm bg-card"
                            options={FIELD_CONTROL_OPTIONS.map((o) => ({ value: o.id, label: o.label }))} />
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${uid}-ph`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_placeholder")}</Label>
                        <Input id={`${uid}-ph`} value={field.placeholder} onChange={(e) => update({ placeholder: e.target.value })} placeholder="e.g. Enter a value…" className="h-8 text-sm" />
                    </div>
                    <div className="flex items-end pb-1">
                        <Label className="flex items-center gap-2 text-sm cursor-pointer">
                            <input type="checkbox" checked={field.required} onChange={(e) => update({ required: e.target.checked })} className="rounded border-input" />
                            {t("node_definition_builder.field_required")}
                        </Label>
                    </div>
                    {showOptions && (
                        <div className="col-span-2 flex flex-col gap-2">
                            <Label className="flex items-center gap-2 text-sm cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={dynamic}
                                    onChange={(e) => update({ serverOptions: e.target.checked ? (so ?? BLANK_SERVER_OPTIONS) : undefined })}
                                    className="rounded border-input"
                                />
                                {t("node_definition_builder.field_dynamic_options")}
                            </Label>

                            {!dynamic && (
                                <div className="flex flex-col gap-1">
                                    <Label htmlFor={`${uid}-opts`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_options")}</Label>
                                    <Input id={`${uid}-opts`} value={field.options} onChange={(e) => update({ options: e.target.value })} placeholder={t("node_definition_builder.field_options_placeholder")} className="h-8 text-sm" />
                                    <span className="text-xs text-muted-foreground">{t("node_definition_builder.field_options_hint")}</span>
                                </div>
                            )}

                            {dynamic && so && (
                                <div className="flex flex-col gap-3 rounded-md border border-border/60 bg-muted/20 p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-xs text-muted-foreground">{t("node_definition_builder.field_dynamic_hint")}</span>
                                        <SimpleSelect aria-label={t("node_definition_builder.field_so_source")} size="sm" value={source} onValueChange={(v) => setSource(v as "manual" | "gateway")} className="h-7! w-auto text-xs bg-card shrink-0"
                                            options={[
                                                { value: "manual", label: t("node_definition_builder.field_so_source_manual") },
                                                { value: "gateway", label: t("node_definition_builder.field_so_source_gateway") },
                                            ]} />
                                    </div>

                                    {source === "gateway" ? (
                                        <GatewayOptionsPicker so={so} onChange={setSO} uid={uid} />
                                    ) : (
                                        <>
                                            <div className="flex justify-end">
                                                <Button type="button" variant="outline" size="sm" className="h-7" onClick={() => update({ serverOptions: { ...GATEWAY_APPS_SERVER_OPTIONS, source: "manual" } })}>
                                                    {t("node_definition_builder.field_so_gateway_preset")}
                                                </Button>
                                            </div>
                                            <div className="grid grid-cols-2 gap-3">
                                                <div className="flex flex-col gap-1">
                                                    <Label htmlFor={`${uid}-so-svc`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_service")} *</Label>
                                                    <SimpleSelect id={`${uid}-so-svc`} size="sm" value={so.service} onValueChange={(v) => setSO({ service: v })} className="text-sm bg-card"
                                                        emptyLabel={t("node_definition_builder.select_value")}
                                                        options={SERVICE_KEYS.map((s) => ({ value: s, label: s }))} />
                                                </div>
                                                <div className="flex flex-col gap-1">
                                                    <Label htmlFor={`${uid}-so-ep`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_endpoint")} *</Label>
                                                    <Input id={`${uid}-so-ep`} value={so.endpoint} onChange={(e) => setSO({ endpoint: e.target.value })} placeholder="gateway/apps" className="h-8 text-sm" />
                                                </div>
                                                <div className="flex flex-col gap-1">
                                                    <Label htmlFor={`${uid}-so-val`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_value")} *</Label>
                                                    <Input id={`${uid}-so-val`} value={so.valueField} onChange={(e) => setSO({ valueField: e.target.value })} placeholder="name" className="h-8 text-sm" />
                                                </div>
                                                <div className="flex flex-col gap-1">
                                                    <Label htmlFor={`${uid}-so-lbl`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_label")} *</Label>
                                                    <Input id={`${uid}-so-lbl`} value={so.labelField} onChange={(e) => setSO({ labelField: e.target.value })} placeholder="name" className="h-8 text-sm" />
                                                </div>
                                                <div className="col-span-2 flex flex-col gap-1">
                                                    <Label htmlFor={`${uid}-so-dep`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_so_deps")}</Label>
                                                    <Input id={`${uid}-so-dep`} value={so.dependencies} onChange={(e) => setSO({ dependencies: e.target.value })} placeholder="otherFieldKey" className="h-8 text-sm" />
                                                </div>
                                            </div>
                                        </>
                                    )}
                                    {soIncomplete && <span className="text-xs text-destructive">{t("node_definition_builder.field_so_incomplete")}</span>}
                                </div>
                            )}
                        </div>
                    )}
                    <div className="col-span-2 flex flex-col gap-1">
                        <Label htmlFor={`${uid}-desc`} className="text-xs text-muted-foreground">{t("node_definition_builder.field_description")}</Label>
                        <Input id={`${uid}-desc`} value={field.description} onChange={(e) => update({ description: e.target.value })} placeholder={t("node_definition_builder.field_description_placeholder")} className="h-8 text-sm" />
                    </div>
                </div>
            )}
        </div>
    );
};

export default FieldRow;
