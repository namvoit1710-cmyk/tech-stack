import { useLanguage } from "@/hooks/use-language";
import {
    mappingEntriesToObject,
    objectToMappingEntries,
    schemaFieldsToPreviewFields,
    type IMappingEntry,
    type ISchemaFieldItem,
} from "@/features/node-definitions/lib/converters";
import type { NodeDefinitionTest } from "@/features/node-definitions/hooks/use-node-definition-test";
import type { IWorkerFieldMeta } from "@/features/node-definitions/lib/worker-fields";
import { Builder, BuilderRef, IField } from "@ldc/autoform";
import { Button } from "@ldc/ui/components/button";
import { Spinner } from "@ldc/ui/components/spinner";
import { cn } from "@ldc/ui";
import { LogInIcon, PlayIcon } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import AdvancedJsonEditor from "../advanced-json-editor";
import InputMappingEditor from "../controls/input-mapping-editor";
import SectionShell, { type SectionMode } from "../section-shell";
import {
    IdleHint,
    NotTestablePanel,
    RequestErrorPanel,
    ResolvedInputPanel,
    SaveFirstHint,
    TestStatusBadge,
} from "../test/test-result-views";

interface MappingBeforeSectionProps {
    inputMapping: IMappingEntry[];
    onInputMappingChange: (e: IMappingEntry[]) => void;
    customFields: ISchemaFieldItem[];
    /** Base worker's mappable fields, resolved from its input_schema (dynamic). */
    workerFields: IWorkerFieldMeta[];
    /** Worker fields locked in Defaults — excluded from Input Mapping. */
    lockedKeys?: Set<string>;
    /** The node's input form fields — used to build the sample-input test form. */
    inputSchemaFields: ISchemaFieldItem[];
    test: NodeDefinitionTest;
    isUnsaved?: boolean;
    onValidityChange?: (valid: boolean) => void;
}

/** "Input Mapping": the input mapping (user input → worker request) + a Run that shows the
 *  resolved input the worker will receive. */
const MappingBeforeSection = ({
    inputMapping,
    onInputMappingChange,
    customFields,
    workerFields,
    lockedKeys,
    inputSchemaFields,
    test,
    isUnsaved,
    onValidityChange,
}: MappingBeforeSectionProps) => {
    const { t } = useLanguage();
    const [mode, setMode] = useState<SectionMode>("guided");
    const formRef = useRef<BuilderRef>(null);

    const testFields = useMemo<IField[]>(
        () => schemaFieldsToPreviewFields(inputSchemaFields) as unknown as IField[],
        [inputSchemaFields]
    );

    // This is a "Try it" sample-input form — run with whatever's entered and let
    // the backend judge it. Going through the Builder's onSubmit gated the run on
    // the builder's own zod validity, so an empty/invalid preview field made the
    // button a silent no-op. Read current values directly instead.
    const handleRun = useCallback(() => {
        test.run(formRef.current?.getValues?.() ?? {});
    }, [test]);

    const resultArea = test.notTestable
        ? <NotTestablePanel />
        : test.error
            ? <RequestErrorPanel message={test.error} />
            : test.result
                ? (
                    <div className={cn("flex flex-col gap-4", test.isPending && "opacity-50")}>
                        <TestStatusBadge status={test.result.status} executionMs={test.result.execution_time_ms} />
                        <ResolvedInputPanel value={test.result.resolved_input} />
                    </div>
                )
                : <IdleHint />;

    return (
        <div className="flex flex-col gap-8">
            <SectionShell
                title={t("node_definition_builder.input_mapping_title")}
                help={t("node_definition_builder.mapping_before_help")}
                icon={<LogInIcon className="size-5" />}
                mode={mode}
                onModeChange={(m) => { if (m === "guided") onValidityChange?.(true); setMode(m); }}
                guided={<InputMappingEditor entries={inputMapping} onChange={onInputMappingChange} customFields={customFields} workerFields={workerFields} lockedKeys={lockedKeys} />}
                advanced={
                    <AdvancedJsonEditor
                        key={mode}
                        defaultValue={mappingEntriesToObject(inputMapping, undefined, workerFields)}
                        onChange={(parsed) => onInputMappingChange(objectToMappingEntries(parsed, undefined, workerFields))}
                        onValidityChange={onValidityChange}
                        placeholder='{ "query_params": [ { "param_name": "q", "param_value": "{{$custom.query}}" } ] }'
                    />
                }
            />

            {/* Try it */}
            <div className="rounded-xl border border-border bg-muted/20 p-5 flex flex-col gap-4">
                <div className="flex flex-col">
                    <h4 className="text-sm font-semibold text-foreground">{t("node_definition_builder.try_it")}</h4>
                    <p className="text-sm text-muted-foreground mt-0.5">{t("node_definition_builder.resolved_input_hint")}</p>
                </div>

                {isUnsaved ? (
                    <SaveFirstHint />
                ) : (
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                        <div className="flex flex-col gap-3">
                            {testFields.length > 0 ? (
                                <div className="rounded-lg border border-border bg-card p-4">
                                    <Builder key={`${inputSchemaFields.length}`} ref={formRef} schema={{ fields: testFields }} defaultValues={test.lastInput} onSubmit={test.run} />
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground py-4">{t("node_definition_builder.test_no_input_fields")}</div>
                            )}
                            <Button onClick={handleRun} disabled={test.isPending} className="self-start">
                                {test.isPending ? <><Spinner className="size-4 mr-2" />{t("node_definition_builder.test_running")}</> : <><PlayIcon className="size-4 mr-2" />{t("node_definition_builder.test_run")}</>}
                            </Button>
                        </div>
                        <div>{resultArea}</div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default MappingBeforeSection;
