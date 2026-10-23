import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { Settings2Icon } from "lucide-react";
import { useId } from "react";
import SectionShell from "../section-shell";
import ColorPicker from "../controls/color-picker";
import IconPicker from "../controls/icon-picker";
import TagsInput from "../controls/tags-input";

const KINDS = ["trigger", "action", "read", "logic", "human", "transform", "util"] as const;

export interface IBasicsValue {
    name: string;
    description: string;
    baseWorkerId: string;
    icon: string;
    color: string;
    tags: string[];
    kind: string;
    collection: string;
}

interface BasicsSectionProps {
    value: IBasicsValue;
    onChange: (partial: Partial<IBasicsValue>) => void;
    workerOptions: { id: string; value: string }[];
    /** Worker is fixed after creation. */
    isEdit?: boolean;
}

const BasicsSection = ({ value, onChange, workerOptions, isEdit }: BasicsSectionProps) => {
    const { t } = useLanguage();
    const uid = useId();
    const accent = value.color || "#3b82f6";

    return (
        <SectionShell
            title={t("node_definition_builder.section_general")}
            help={t("node_definition_builder.general_help")}
            icon={<Settings2Icon className="size-5" />}
            hideToggle
            guided={
                <div className="flex flex-col gap-6">
                    {/* Live node preview */}
                    <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-4">
                        <div
                            className="flex items-center justify-center size-11 rounded-lg shrink-0"
                            style={{ color: accent, backgroundColor: `color-mix(in srgb, ${accent} 12%, transparent)` }}
                        >
                            <DynamicNodeIcon name={value.icon || "box"} fallbackIconName="box" className="size-6" />
                        </div>
                        <div className="flex flex-col min-w-0">
                            <span className="font-medium truncate">{value.name || t("node_definition_builder.preview")}</span>
                            <span className="text-sm text-muted-foreground truncate">
                                {value.description || value.baseWorkerId || t("node_definition_builder.preview_empty")}
                            </span>
                        </div>
                    </div>

                    {/* Form */}
                    <div className="flex flex-col gap-5 max-w-2xl">
                        <Field id={`${uid}-name`} label={t("node_definition_builder.info_name")} required>
                            <Input id={`${uid}-name`} value={value.name} onChange={(e) => onChange({ name: e.target.value })} placeholder={t("node_definition_builder.info_name_placeholder")} />
                        </Field>

                        <Field id={`${uid}-desc`} label={t("node_definition_builder.info_description")}>
                            <textarea
                                id={`${uid}-desc`}
                                value={value.description}
                                onChange={(e) => onChange({ description: e.target.value })}
                                placeholder={t("node_definition_builder.info_description_placeholder")}
                                rows={2}
                                className="w-full text-sm p-2 border border-border rounded-md resize-y bg-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                            />
                        </Field>

                        <Field id={`${uid}-worker`} label={t("node_definition_builder.info_base_worker")} required hint={isEdit ? t("node_definition_builder.base_worker_locked") : undefined}>
                            <Select value={value.baseWorkerId} onValueChange={(v) => onChange({ baseWorkerId: v })} disabled={isEdit}>
                                <SelectTrigger id={`${uid}-worker`} className={cn("w-full", isEdit && "opacity-70")}>
                                    <SelectValue placeholder={t("node_definition_builder.info_base_worker_placeholder")} />
                                </SelectTrigger>
                                <SelectContent>
                                    {workerOptions.map((w) => <SelectItem key={w.id} value={w.id}>{w.value}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </Field>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <Field label={t("node_definition_builder.info_kind")}>
                                <Select value={value.kind} onValueChange={(v) => onChange({ kind: v })}>
                                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {KINDS.map((k) => <SelectItem key={k} value={k}>{t(`node_kind.${k}`)}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </Field>
                            <Field label={t("node_definition_builder.info_collection")}>
                                <Input value={value.collection}
                                       onChange={(e) => onChange({ collection: e.target.value })}
                                       placeholder={t("node_definition_builder.info_collection_placeholder")} />
                            </Field>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <Field label={t("node_definition_builder.info_icon")}>
                                <IconPicker value={value.icon} color={value.color} onChange={(icon) => onChange({ icon })} />
                            </Field>
                            <Field label={t("node_definition_builder.info_color")}>
                                <ColorPicker value={value.color} onChange={(color) => onChange({ color })} />
                            </Field>
                        </div>

                        <Field label={t("node_definition_builder.info_tags")}>
                            <TagsInput value={value.tags} onChange={(tags) => onChange({ tags })} placeholder={t("node_definition_builder.info_tags_placeholder")} />
                        </Field>
                    </div>
                </div>
            }
        />
    );
};

const Field = ({ id, label, required, hint, children }: { id?: string; label: string; required?: boolean; hint?: string; children: React.ReactNode }) => (
    <div className="flex flex-col gap-1.5">
        <Label htmlFor={id}>
            {label} {required && <span className="text-destructive">*</span>}
        </Label>
        {children}
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
);

export default BasicsSection;
