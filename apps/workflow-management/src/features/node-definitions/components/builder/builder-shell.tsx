import Page from "@/components/containers/page";
import LoadingOverlay from "@/features/workflows/components/loading-overlay/loading-overlay";
import { nodepalleteKey } from "@/features/workflows/hooks/apis/node-pallete";
import {
    nodeDefinitionKey,
    useNodeDefinitionById,
    useUpdateNodeDefinition,
} from "@/features/node-definitions/hooks/apis/node-definitions";
import { useWorkerCatalog } from "@/features/node-definitions/hooks/use-worker-catalog";
import { resolveWorkerFields } from "@/features/node-definitions/lib/worker-fields";
import {
    autoFormToSchemaFields,
    entriesToLockedDefaults,
    keyValuePairsToObject,
    lockedDefaultsToEntries,
    mappingEntriesToObject,
    objectToKeyValuePairs,
    objectToMappingEntries,
    schemaFieldsToAutoForm,
    type IKeyValuePair,
    type ILockedDefaultEntry,
    type IMappingEntry,
    type ISchemaFieldItem,
} from "@/features/node-definitions/lib/converters";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { LoadingSpin, DynamicNodeIcon } from "@ldc/workflow-editor";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@ldc/ui/components/tabs";
import { FileOutputIcon, FormInputIcon, SaveIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import BasicsSection, { type IBasicsValue } from "./sections/basics-section";
import DefaultsSection from "./sections/defaults-section";
import GatewayActionSection from "./sections/gateway-action-section";
import MappingAfterSection from "./sections/mapping-after-section";
import MappingBeforeSection from "./sections/mapping-before-section";
import SchemaSection from "./sections/schema-section";
import { extractFunctionCallMarkers, isGatewayWorker, parseGatewayMarker } from "@/features/node-definitions/lib/gateway-binding";
import { useNodeDefinitionTest } from "@/features/node-definitions/hooks/use-node-definition-test";

type SectionId = "basics" | "gateway_action" | "inputs" | "outputs" | "defaults" | "mapping_before" | "mapping_after";

const EDIT_TABS: SectionId[] = ["basics", "inputs", "outputs", "defaults", "mapping_before", "mapping_after"];

const emptyBasics: IBasicsValue = { name: "", description: "", baseWorkerId: "", icon: "", color: "", tags: [], kind: "", collection: "" };

const BuilderShell = () => {
    const { t } = useLanguage();
    const { definitionId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [searchParams] = useSearchParams();

    const isNew = definitionId === "new"; // legacy route — creation now happens via the list popup
    const { data: definitionResponse, isLoading } = useNodeDefinitionById(isNew ? "" : definitionId!, { enabled: !isNew && !!definitionId });
    const definition = definitionResponse?.data;

    // ── Builder state ────────────────────────────────────────────────
    const [basics, setBasics] = useState<IBasicsValue>(emptyBasics);
    const [inputFields, setInputFields] = useState<ISchemaFieldItem[]>([]);
    const [outputFields, setOutputFields] = useState<ISchemaFieldItem[]>([]);
    const [lockedDefaults, setLockedDefaults] = useState<ILockedDefaultEntry[]>([]);
    const [inputMapping, setInputMapping] = useState<IMappingEntry[]>([]);
    const [outputMapping, setOutputMapping] = useState<IKeyValuePair[]>([]);
    // _function_call markers (gateway invoke) live raw in input_mapping; held
    // separately because the IMappingEntry round-trip would otherwise drop them.
    const [gatewayMarker, setGatewayMarker] = useState<Record<string, unknown> | null>(null);
    const gatewayInitial = useMemo(() => parseGatewayMarker(gatewayMarker), [gatewayMarker]);
    const [valid, setValid] = useState({ inputs: true, outputs: true, defaults: true, mapping_before: true, mapping_after: true });
    // Idempotent: return the SAME object when unchanged so React bails out — a
    // section that reports validity from an effect (SchemaSection) would otherwise
    // loop forever, since the inline onValidityChange arrow changes every render.
    const setValidFor = useCallback((key: keyof typeof valid, ok: boolean) => setValid((v) => v[key] === ok ? v : { ...v, [key]: ok }), []);

    const updateBasics = useCallback((partial: Partial<IBasicsValue>) => setBasics((b) => ({ ...b, ...partial })), []);

    const test = useNodeDefinitionTest(definitionId ?? "");

    // ── Worker catalog (v1 palette; carries worker_id + schemas) ──────
    const { workerNodes, workerOptions } = useWorkerCatalog();
    const selectedWorker = useMemo(
        () => workerNodes.find((w: any) => w.worker_id === basics.baseWorkerId || w.worker_type === basics.baseWorkerId || w.id === basics.baseWorkerId),
        [basics.baseWorkerId, workerNodes]
    );
    const workerSchema = useMemo(() => (basics.baseWorkerId ? selectedWorker?.input_schema : undefined), [basics.baseWorkerId, selectedWorker]);
    const workerOutputSchema = useMemo(() => (basics.baseWorkerId ? selectedWorker?.output_schema : undefined), [basics.baseWorkerId, selectedWorker]);

    // The base worker's mappable input fields — derived dynamically from its
    // catalog input_schema (curated registry wins for HTTP). Drives the Input
    // Mapping "Add Mapping" menu and keeps the load/save round-trip honest for
    // workers other than HTTP (e.g. the code worker).
    const workerFields = useMemo(() => resolveWorkerFields(basics.baseWorkerId, workerSchema), [basics.baseWorkerId, workerSchema]);

    // Input Mapping only handles UNLOCKED worker fields; Defaults owns the locked ones.
    const lockedKeys = useMemo(() => new Set(lockedDefaults.filter((e) => e.locked).map((e) => e.key)), [lockedDefaults]);
    useEffect(() => {
        const locked = new Set(lockedDefaults.filter((e) => e.locked).map((e) => e.key));
        setInputMapping((prev) => {
            const next = prev.filter((e) => !locked.has(e.workerField));
            return next.length === prev.length ? prev : next; // same ref → no loop
        });
    }, [lockedDefaults]);

    // ── Hydrate from loaded definition (render-time, guarded) ─────────
    const [prevDefinition, setPrevDefinition] = useState(definition);
    if (definition && definition !== prevDefinition) {
        setPrevDefinition(definition);
        setBasics({
            name: definition.name ?? "",
            description: definition.description ?? "",
            baseWorkerId: definition.base_worker_id ?? "",
            icon: definition.icon ?? "",
            color: definition.color ?? "",
            tags: definition.tags ?? [],
            kind: definition.kind ?? "",
            collection: definition.collection ?? "",
        });
        setInputFields(autoFormToSchemaFields(definition.input_schema ?? []));
        setOutputFields(autoFormToSchemaFields(definition.output_schema ?? []));
        setInputMapping(objectToMappingEntries(definition.input_mapping, definition.base_worker_id));
        setOutputMapping(objectToKeyValuePairs(definition.output_mapping));
        setGatewayMarker(extractFunctionCallMarkers(definition.input_mapping));
    }

    // ── Rebuild locked-default rows when the worker schema changes ────
    const [prevWorkerSchema, setPrevWorkerSchema] = useState(workerSchema);
    if (workerSchema !== prevWorkerSchema) {
        setPrevWorkerSchema(workerSchema);
        if (workerSchema?.length) {
            setLockedDefaults(lockedDefaultsToEntries(definition?.locked_defaults, workerSchema));
        } else if (definition?.locked_defaults) {
            setLockedDefaults(lockedDefaultsToEntries(definition.locked_defaults));
        }
    }

    // ── Re-hydrate input mapping once the worker's real fields are known ──
    // The initial hydration above runs before the catalog resolves, so it falls
    // back to the HTTP field set and would DROP worker-specific keys (e.g. a code
    // worker's). Re-parse from the saved mapping once `selectedWorker` resolves —
    // guarded to once per definition so a palette refetch can't clobber edits.
    const inputMappingHydratedFor = useRef<string>("");
    useEffect(() => {
        const id = definitionId ?? "";
        if (!definition || !selectedWorker || inputMappingHydratedFor.current === id) return;
        inputMappingHydratedFor.current = id;
        setInputMapping(objectToMappingEntries(definition.input_mapping, basics.baseWorkerId, workerFields));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [definition, selectedWorker, workerFields]);

    // ── Save ─────────────────────────────────────────────────────────
    const invalidateListAndPalette = useCallback(() => {
        // Lists + palette only — NOT the active detail, so autosave doesn't refetch-and-clobber edits.
        queryClient.invalidateQueries({ queryKey: nodeDefinitionKey.lists() });
        queryClient.invalidateQueries({ queryKey: nodepalleteKey.getAllNodePalletes() });
    }, [queryClient]);

    const { mutate: update, isPending: isSaving } = useUpdateNodeDefinition({
        onSuccess: () => { toast.success(t("notification.success"), t("node_definitions.updated_successfully")); invalidateListAndPalette(); },
        onError: () => toast.error(t("notification.error"), t("node_definitions.update_failed")),
    });
    const { mutateAsync: autosave } = useUpdateNodeDefinition({
        onError: () => toast.error(t("notification.error"), t("node_definitions.update_failed")),
    });

    const buildPayload = useCallback(() => ({
        name: basics.name,
        description: basics.description,
        icon: basics.icon,
        color: basics.color,
        tags: basics.tags,
        kind: basics.kind || "",
        collection: basics.collection || "",
        locked_defaults: entriesToLockedDefaults(lockedDefaults),
        input_schema: schemaFieldsToAutoForm(inputFields),
        output_schema: schemaFieldsToAutoForm(outputFields),
        input_mapping: { ...mappingEntriesToObject(inputMapping, basics.baseWorkerId, workerFields), ...(gatewayMarker ?? {}) },
        output_mapping: keyValuePairsToObject(outputMapping),
    }), [basics, lockedDefaults, inputFields, outputFields, inputMapping, outputMapping, gatewayMarker, workerFields]);

    const basicsValid = !!basics.name.trim() && !!basics.baseWorkerId;
    const allValid = basicsValid && Object.values(valid).every(Boolean);

    // Baseline the saved signature once the definition loads, so save-as-you-go only patches real changes.
    const lastSaved = useRef<string>("");
    useEffect(() => {
        if (definition && !isNew) lastSaved.current = JSON.stringify(buildPayload());
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [definition]);

    // Silent, dirty-guarded patch — used on tab change and right before a test run.
    const saveDraft = useCallback(async () => {
        if (isNew || !definitionId || !allValid) return;
        const json = JSON.stringify(buildPayload());
        if (json === lastSaved.current) return;
        try {
            await autosave({ id: definitionId, payload: buildPayload() });
            lastSaved.current = json;
            invalidateListAndPalette();
        } catch { /* error already toasted */ }
    }, [isNew, definitionId, allValid, buildPayload, autosave, invalidateListAndPalette]);

    const handleSave = useCallback(() => {
        if (!allValid || !definitionId) return;
        update({ id: definitionId, payload: buildPayload() }, { onSuccess: () => { lastSaved.current = JSON.stringify(buildPayload()); } });
    }, [allValid, definitionId, update, buildPayload]);

    // Run a test with the current data: patch first, then test (the backend tests the saved node).
    const wrappedTest = useMemo(() => ({
        ...test,
        run: (values: Record<string, any>) => { void saveDraft().then(() => test.run(values)); },
        runLast: () => { void saveDraft().then(() => test.runLast()); },
    }), [test, saveDraft]);

    // ── Section renderer ──────────────────────────────────────────────
    const renderSection = (id: SectionId) => {
        switch (id) {
            case "basics":
                return <BasicsSection value={basics} onChange={updateBasics} workerOptions={workerOptions} isEdit />;
            case "gateway_action":
                return <GatewayActionSection initial={gatewayInitial} onApply={(b) => { setInputFields(b.inputFields); setOutputFields(b.outputFields); setOutputMapping(b.outputMapping); setGatewayMarker(b.marker); }} />;
            case "inputs":
                return <SchemaSection title={t("node_definition_builder.input_fields_title")} help={t("node_definition_builder.input_fields_help")} icon={<FormInputIcon className="size-5" />} fields={inputFields} onChange={setInputFields} onValidityChange={(ok) => setValidFor("inputs", ok)} />;
            case "outputs":
                return <SchemaSection title={t("node_definition_builder.output_fields_title")} help={t("node_definition_builder.output_fields_help")} icon={<FileOutputIcon className="size-5" />} fields={outputFields} onChange={setOutputFields} onValidityChange={(ok) => setValidFor("outputs", ok)} preview={false} />;
            case "defaults":
                return <DefaultsSection entries={lockedDefaults} onChange={setLockedDefaults} workerSchema={workerSchema} customFields={inputFields} onValidityChange={(ok) => setValidFor("defaults", ok)} />;
            case "mapping_before":
                return <MappingBeforeSection inputMapping={inputMapping} onInputMappingChange={setInputMapping} customFields={inputFields} workerFields={workerFields} lockedKeys={lockedKeys} inputSchemaFields={inputFields} test={wrappedTest} onValidityChange={(ok) => setValidFor("mapping_before", ok)} />;
            case "mapping_after":
                return <MappingAfterSection outputMapping={outputMapping} onOutputMappingChange={setOutputMapping} customFields={inputFields} workerType={basics.baseWorkerId} workerOutputSchema={workerOutputSchema} test={wrappedTest} onValidityChange={(ok) => setValidFor("mapping_after", ok)} />;
        }
    };

    // Gateway-worker nodes get a "Gateway Action" tab (right after Basics) to bind a function.
    const tabs: SectionId[] = isGatewayWorker(basics.baseWorkerId)
        ? ["basics", "gateway_action", "inputs", "outputs", "defaults", "mapping_before", "mapping_after"]
        : EDIT_TABS;

    if (isNew) return <Navigate to="/node-definitions" replace />;

    return (
        <Page>
            <Page.Header
                title={
                    <div className="flex items-center gap-2">
                        <div className="p-2 rounded-lg" style={{ color: basics.color || "#3b82f6", backgroundColor: `color-mix(in srgb, ${basics.color || "#3b82f6"} 10%, transparent)` }}>
                            <DynamicNodeIcon name={basics.icon || "box"} fallbackIconName="box" className="size-3" />
                        </div>
                        <h3 className="text-md font-medium">{basics.name || t("node_definition_builder.title")}</h3>
                    </div>
                }
                description={basics.description}
                actions={
                    <div className="flex items-center gap-2">
                        <Button variant="link" onClick={() => navigate("/node-definitions")}>{t("node_definition_builder.back")}</Button>
                        <Button disabled={isSaving || !allValid} onClick={handleSave}>
                            {isSaving ? <LoadingSpin /> : <SaveIcon className="size-4" strokeWidth={1.5} />}
                            <span className="ml-2">{t("save")}</span>
                        </Button>
                    </div>
                }
            />

            <div className="flex flex-col h-full w-full overflow-hidden relative">
                <EditTabs
                    tabs={tabs}
                    renderSection={renderSection}
                    initialTab={(searchParams.get("tab") as SectionId) || "basics"}
                    onTabChange={saveDraft}
                    t={t}
                />
                <LoadingOverlay isLoading={isLoading} />
            </div>
        </Page>
    );
};

/* ── Edit tabs ─────────────────────────────────────────────────────── */
const EditTabs = ({
    tabs, renderSection, initialTab, onTabChange, t,
}: {
    tabs: SectionId[];
    renderSection: (id: SectionId) => React.ReactNode;
    initialTab: SectionId;
    onTabChange?: () => void;
    t: (k: string) => string;
}) => {
    const [active, setActive] = useState<SectionId>(tabs.includes(initialTab) ? initialTab : "basics");
    return (
        <Tabs
            value={active}
            onValueChange={(v) => { onTabChange?.(); setActive(v as SectionId); }}
            className="flex-1 flex flex-col overflow-hidden"
        >
            <TabsList variant="line" className="px-4 border-b border-b-border justify-start w-full">
                {tabs.map((id) => (
                    <TabsTrigger key={id} value={id} className="flex-0 cursor-pointer data-active:after:bg-primary">
                        {t(`node_definition_builder.tab_${id}`)}
                    </TabsTrigger>
                ))}
            </TabsList>
            {tabs.map((id) => (
                <TabsContent key={id} value={id} className="flex-1 overflow-y-auto m-0">
                    <div className="mx-auto w-full max-w-5xl p-6 md:p-8">{renderSection(id)}</div>
                </TabsContent>
            ))}
        </Tabs>
    );
};

export default BuilderShell;
