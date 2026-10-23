import { useLanguage } from "@/hooks/use-language";
import {
    autoFormToSchemaFields,
    createEmptyField,
    schemaFieldsToAutoForm,
    schemaFieldsValid,
    schemaFieldsToPreviewFields,
    type ISchemaFieldItem,
} from "@/features/node-definitions/lib/converters";
import { Builder, type IField } from "@ldc/autoform";
import { Button } from "@ldc/ui/components/button";
import { EyeIcon, PlusIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import FieldRow from "../controls/field-row";
import SectionShell, { type SectionMode } from "../section-shell";
import AdvancedJsonEditor from "../advanced-json-editor";

interface SchemaSectionProps {
    title: string;
    help?: string;
    icon?: ReactNode;
    fields: ISchemaFieldItem[];
    onChange: (fields: ISchemaFieldItem[]) => void;
    onValidityChange?: (valid: boolean) => void;
    /** Show the live form preview panel (inputs yes, outputs no). */
    preview?: boolean;
}

/** Input/Output schema editor: guided field list (+ live preview) with an
 *  Advanced JSON (AutoFormField) escape hatch. */
const SchemaSection = ({ title, help, icon, fields, onChange, onValidityChange, preview = true }: SchemaSectionProps) => {
    const { t } = useLanguage();
    const [mode, setMode] = useState<SectionMode>("guided");

    const setField = useCallback(
        (index: number, updated: ISchemaFieldItem) => {
            const next = [...fields];
            next[index] = updated;
            onChange(next);
        },
        [fields, onChange]
    );

    const move = useCallback(
        (from: number, to: number) => {
            if (to < 0 || to >= fields.length) return;
            const next = [...fields];
            const [moved] = next.splice(from, 1);
            next.splice(to, 0, moved!);
            onChange(next);
        },
        [fields, onChange]
    );

    // Guided validity is real now (a half-configured dynamic-options field is invalid).
    // Advanced mode owns its own validity via AdvancedJsonEditor, so only report here in guided.
    useEffect(() => {
        if (mode === "guided") onValidityChange?.(schemaFieldsValid(fields));
    }, [mode, fields, onValidityChange]);

    const handleModeChange = (next: SectionMode) => setMode(next);

    const previewFields = useMemo<IField[]>(
        () => schemaFieldsToPreviewFields(fields) as unknown as IField[],
        [fields]
    );

    const guided = (
        <div className={preview ? "grid grid-cols-1 lg:grid-cols-2 gap-5" : ""}>
            <div className="flex flex-col gap-2.5">
                {fields.length === 0 && (
                    <div className="text-center py-10 text-sm text-muted-foreground border border-dashed border-border rounded-xl">
                        {t("node_definition_builder.no_fields")}
                    </div>
                )}
                {fields.map((field, index) => (
                    <FieldRow
                        key={index}
                        field={field}
                        index={index}
                        total={fields.length}
                        onChange={setField}
                        onRemove={(i) => onChange(fields.filter((_, idx) => idx !== i))}
                        onMoveUp={(i) => move(i, i - 1)}
                        onMoveDown={(i) => move(i, i + 1)}
                    />
                ))}
                <Button variant="outline" size="sm" className="self-start" onClick={() => onChange([...fields, createEmptyField()])}>
                    <PlusIcon className="size-3.5 mr-1" />
                    {t("node_definition_builder.add_field")}
                </Button>
            </div>

            {preview && (
                <div className="flex flex-col gap-2.5">
                    <div className="flex items-center gap-2 text-muted-foreground">
                        <EyeIcon className="size-4" />
                        <span className="text-xs font-semibold uppercase tracking-wide">{t("node_definition_builder.live_preview")}</span>
                    </div>
                    <div className="rounded-xl border border-border bg-muted/30 p-4 min-h-40">
                        {previewFields.length === 0 ? (
                            <div className="flex items-center justify-center h-full text-sm text-muted-foreground py-8">
                                {t("node_definition_builder.preview_empty")}
                            </div>
                        ) : (
                            <Builder schema={{ fields: previewFields }} />
                        )}
                    </div>
                </div>
            )}
        </div>
    );

    const advanced = (
        <AdvancedJsonEditor
            key={mode}
            defaultValue={schemaFieldsToAutoForm(fields)}
            onChange={(parsed) => onChange(autoFormToSchemaFields(Array.isArray(parsed) ? parsed : []))}
            onValidityChange={onValidityChange}
            placeholder='[ { "key": "query", "outputType": "string", "fieldConfig": { … } } ]'
        />
    );

    return (
        <SectionShell title={title} help={help} icon={icon} mode={mode} onModeChange={handleModeChange} guided={guided} advanced={advanced} />
    );
};

export default SchemaSection;
