import { useLanguage } from "@/hooks/use-language";
import {
    keyValuePairsToObject,
    objectToKeyValuePairs,
    type IKeyValuePair,
    type ISchemaFieldItem,
    type ISuggestion,
} from "@/features/node-definitions/lib/converters";
import {
    workerOutputSchemaSuggestions,
    workerOutputSuggestions,
    type IOutputSchemaField,
} from "@/features/node-definitions/lib/expression-suggestions";
import type { NodeDefinitionTest } from "@/features/node-definitions/hooks/use-node-definition-test";
import type { IExprSuggestion } from "@/features/workflows/types/node-detail";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Spinner } from "@ldc/ui/components/spinner";
import { LogOutIcon, PlayIcon } from "lucide-react";
import { useMemo, useState } from "react";
import AdvancedJsonEditor from "../advanced-json-editor";
import OutputMappingEditor from "../controls/output-mapping-editor";
import SectionShell, { type SectionMode } from "../section-shell";
import {
    IdleHint,
    NotTestablePanel,
    OutputReferenceLine,
    OutputsPanel,
    RequestErrorPanel,
    SaveFirstHint,
    TestStatusBadge,
    WorkerErrorBlock,
    isFailedStatus,
} from "../test/test-result-views";

interface MappingAfterSectionProps {
    outputMapping: IKeyValuePair[];
    onOutputMappingChange: (p: IKeyValuePair[]) => void;
    customFields: ISchemaFieldItem[];
    workerType?: string;
    /** The selected base worker's declared response schema (catalog output_schema). */
    workerOutputSchema?: IOutputSchemaField[];
    test: NodeDefinitionTest;
    isUnsaved?: boolean;
    onValidityChange?: (valid: boolean) => void;
}

/** "Output Mapping": the output mapping (worker response → outputs) + the mapped outputs of the run. */
const MappingAfterSection = ({
    outputMapping,
    onOutputMappingChange,
    customFields,
    workerType,
    workerOutputSchema,
    test,
    isUnsaved,
    onValidityChange,
}: MappingAfterSectionProps) => {
    const { t } = useLanguage();
    const [mode, setMode] = useState<SectionMode>("guided");

    const keySuggestions = useMemo<ISuggestion[]>(
        () => customFields.filter((f) => f.key).map((f) => ({ value: f.key, label: f.label || f.key, description: t("node_definition_builder.matches_input_field") })),
        [customFields, t]
    );

    const valueSuggestions = useMemo<IExprSuggestion[]>(() => {
        const groupLabel = t("node_definition_builder.expr_group_output");
        const fromWorker = workerOutputSchemaSuggestions(workerOutputSchema, groupLabel);
        return fromWorker.length ? fromWorker : workerOutputSuggestions(workerType, groupLabel);
    }, [workerOutputSchema, workerType, t]);

    const r = test.result;
    const resultArea = test.notTestable
        ? <NotTestablePanel />
        : test.error
            ? <RequestErrorPanel message={test.error} />
            : r
                ? (
                    <div className={cn("flex flex-col gap-4", test.isPending && "opacity-50")}>
                        <TestStatusBadge status={r.status} executionMs={r.execution_time_ms} />
                        {r.error && isFailedStatus(r.status) && <WorkerErrorBlock message={r.error} />}
                        <OutputsPanel value={r.outputs} />
                        {r.output_reference && <OutputReferenceLine reference={r.output_reference} />}
                    </div>
                )
                : <IdleHint />;

    return (
        <div className="flex flex-col gap-8">
            <SectionShell
                title={t("node_definition_builder.output_mapping_title")}
                help={t("node_definition_builder.mapping_after_help")}
                icon={<LogOutIcon className="size-5" />}
                mode={mode}
                onModeChange={(m) => { if (m === "guided") onValidityChange?.(true); setMode(m); }}
                guided={<OutputMappingEditor pairs={outputMapping} onChange={onOutputMappingChange} keySuggestions={keySuggestions} valueSuggestions={valueSuggestions} />}
                advanced={
                    <AdvancedJsonEditor
                        key={mode}
                        defaultValue={keyValuePairsToObject(outputMapping)}
                        onChange={(parsed) => onOutputMappingChange(objectToKeyValuePairs(parsed))}
                        onValidityChange={onValidityChange}
                        placeholder='{ "result": "{{$_output.response_body}}" }'
                    />
                }
            />

            {/* Result */}
            <div className="rounded-xl border border-border bg-muted/20 p-5 flex flex-col gap-4">
                <div className="flex items-center justify-between gap-4">
                    <div className="flex flex-col">
                        <h4 className="text-sm font-semibold text-foreground">{t("node_definition_builder.test_outputs")}</h4>
                        <p className="text-sm text-muted-foreground mt-0.5">{t("node_definition_builder.outputs_hint")}</p>
                    </div>
                    {!isUnsaved && (
                        <Button variant="outline" size="sm" onClick={test.runLast} disabled={test.isPending}>
                            {test.isPending ? <><Spinner className="size-4 mr-2" />{t("node_definition_builder.test_running")}</> : <><PlayIcon className="size-4 mr-2" />{t("node_definition_builder.test_run")}</>}
                        </Button>
                    )}
                </div>
                {isUnsaved ? <SaveFirstHint /> : resultArea}
            </div>
        </div>
    );
};

export default MappingAfterSection;
