import { SERVICE_CONFIGS, WORKER_TYPE_UPDATE_PORT } from "@/constants/config";
import useFormDropZone from "@/features/workflows/hooks/use-form-dropzone";
import { useEditorStore } from "@/features/workflows/stores/editor-stores";
import { convertKeys } from "@/features/workflows/utils/parse-string";
import { useLanguage } from "@/hooks/use-language";
import type { FieldValues, IField } from "@ldc/autoform";
import { Builder, useBuilderServices } from "@ldc/autoform";
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import { LoadingSpin } from "@ldc/workflow-editor";
import { useEffect, useMemo } from "react";
import { ExpressionInputControl, ExpressionTextareaControl } from "../../node-config-controls/expression-input-control";
import WorkflowPickerControl from "../../node-config-controls/workflow-picker-control";
import EnvironmentPickerControl from "../../node-config-controls/environment-picker-control";
import { CredentialPickerControl } from "../../node-config-controls/credential-picker-control";
import { buildCredentialField } from "@/features/workflows/utils/credential-field";
import { useNodeDetailContext } from "./node-detail-provider";

// The Builder merges its built-in controls OVER any custom map, so we register the expression
// controls under fresh keys and remap the catalog schema's text controls onto them.
const EXPRESSION_CONTROL_KEYS: Record<string, string> = {
    InputControl: "ExpressionControl",
    TextareaControl: "ExpressionTextareaControl",
};

const remapExpressionControls = (fields: IField[]): IField[] =>
    fields.map((field) => {
        const fc = field.fieldConfig?.fieldControl;
        const mapped = fc && EXPRESSION_CONTROL_KEYS[fc] ? EXPRESSION_CONTROL_KEYS[fc] : fc;
        return {
            ...field,
            fieldConfig: { ...field.fieldConfig, fieldControl: mapped },
            fields: field.fields ? remapExpressionControls(field.fields) : field.fields,
        };
    });

const NodeDetailForm = () => {
    const { t } = useLanguage();
    const { builderRef, removeConnection, updateNodeView, nodeDetail, isNodeDetailLoading, onParametersChanged, flushParamsRef } = useNodeDetailContext()

    const formRef = useFormDropZone()

    const selectedNode = useEditorStore(s => s.selectedNode);

    // SA-2184 — the node-config form is the host BuilderProvider for everything it
    // renders, including the Form Builder behind an INPUT node's `input_schema`.
    // Without services on it, that builder's Gateway App/Function pickers have no
    // transport and render empty.
    const services = useBuilderServices(SERVICE_CONFIGS);

    const inputFormSchema: IField[] = useMemo(() => {
        if (!nodeDetail) return []

        const mappedFields = ((convertKeys(nodeDetail.input_schema) as IField[]) ?? []).map(field => {
            // An array/object field with no nested item schema can't render a per-item
            // form — the Builder would show an empty repeater (+/trash) with nowhere to
            // type a value. Such fields are edited as a raw value (e.g. a CodeControl
            // JSON editor), so coerce them to a leaf `string` like the object case below.
            const isStructuredWithoutSchema =
                !field.fields?.length && (field.outputType === "object" || field.outputType === "array")
            const outputType = isStructuredWithoutSchema ? "string" : field.outputType

            return {
                ...field,
                outputType: outputType,
                fields: field.fields,
                fieldConfig: {
                    ...field.fieldConfig,
                    fieldWrapper: field.fieldConfig?.fieldWrapper,
                    wrapperProps: {
                        ...field.fieldConfig?.wrapperProps,
                        ...((field.fieldConfig?.fieldWrapper === "FormItemWrapper") ? { labelSpan: "xl-12 lg-12 md-12 xs-12", fieldSpan: "xl-12 lg-12 md-12 xs-12" } : {})
                    },

                    fieldControl: field.fieldConfig?.fieldControl,
                    controlProps: {
                        ...field.fieldConfig?.controlProps,
                        className: "w-full",
                    }
                }
            }
        })

        const baseFields = remapExpressionControls(mappedFields)

        // T7: prepend a credential picker for credential-bearing nodes (per the catalog's
        // supported_credential_kinds, or the worker_type fallback). The picker degrades
        // gracefully when the vault is unconfigured (empty, disabled dropdown), so
        // non-credential nodes stay untouched while credential nodes always offer it.
        const credentialField = buildCredentialField(nodeDetail, t("nodes.credential.label"))

        return credentialField ? [credentialField, ...baseFields] : baseFields
    }, [nodeDetail, t])

    const defaultValues = useMemo(() => {
        return { parameters: selectedNode?.original.parameters ?? {} }
    }, [selectedNode?.id])

    const handleUpdateParameters = (data: FieldValues) => {
        const parameters = data.parameters ?? {}
        if (!selectedNode) return

        const current = selectedNode.original.parameters ?? {}
        if (JSON.stringify(current) === JSON.stringify(parameters)) return

        if (selectedNode.original.worker_type === WORKER_TYPE_UPDATE_PORT.SWITCH) {
            const { data: cases } = data.parameters;

            const newPorts = cases?.map((caseItem: { port_id?: string, field_id?: string, branch?: string }, index: number) => ({
                id: caseItem.port_id || caseItem.field_id,
                label: caseItem.branch ?? `${index}`,
            })) ?? [];

            const parameterData = parameters.data.map(({ field_id, port_id, ...rest }: { field_id?: string, port_id?: string }) => ({ ...rest, port_id: port_id || field_id }))
            parameters.data = parameterData

            selectedNode.syncOutputSockets(newPorts, (portId) => {
                removeConnection({ sourceId: portId });
            });
        }

        selectedNode?.updateParameters(parameters)
        updateNodeView(selectedNode.id)
        onParametersChanged?.()
    }

    const debouncedValueChange = useDebounceCallback(handleUpdateParameters, 200)

    // Expose a flush so "Execute Step" can commit a pending (debounced) edit
    // before the node test dispatches — otherwise a quick click tests the prior
    // value. flush() is a no-op when nothing is pending.
    useEffect(() => {
        if (!flushParamsRef) return
        flushParamsRef.current = () => debouncedValueChange.flush()
        return () => {
            flushParamsRef.current = null
        }
    }, [debouncedValueChange, flushParamsRef])

    return (
        <div className="h-full w-full overflow-hidden flex flex-col gap-4">
            <div className="flex items-center justify-between px-4">
                <h5 className="uppercase font-semibold">{t("nodes.nodes_popup_parameters")}</h5>
            </div>

            <div ref={formRef} className="relative h-full flex-2 overflow-y-auto px-4">
                {isNodeDetailLoading && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70">
                        <LoadingSpin />
                    </div>
                )}

                {!!nodeDetail && !isNodeDetailLoading && (
                    <Builder
                        key={selectedNode?.id}
                        ref={builderRef}
                        services={services}
                        defaultValues={defaultValues}
                        fieldControl={{
                            WorkflowPickerControl,
                            EnvironmentPickerControl,
                            CredentialPickerControl,
                            ExpressionControl: ExpressionInputControl,
                            ExpressionTextareaControl: ExpressionTextareaControl,
                        }}
                        schema={{
                            fields: [
                                {
                                    outputType: "object",
                                    key: "parameters",
                                    fieldConfig: {
                                        wrapperProps: {
                                            className: "h-full",
                                        },
                                    },
                                    fields: inputFormSchema
                                }
                            ]
                        }}
                        onSubmit={handleUpdateParameters}
                        onValuesChange={debouncedValueChange}
                    />
                )}

                {!nodeDetail && !isNodeDetailLoading && (
                    <div className="flex-2 flex flex-col items-center justify-center h-full">
                        <p className="text-sm text-gray-500 text-center">{t("nodes.node_not_found")}</p>
                    </div>
                )}
            </div>
        </div>
    )
}

export default NodeDetailForm
