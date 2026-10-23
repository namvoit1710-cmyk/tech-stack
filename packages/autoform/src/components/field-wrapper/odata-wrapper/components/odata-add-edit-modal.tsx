import { useTranslation } from "@ldc/i18n";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "@ldc/ui/components/dialog";
import type { ComponentProps } from "react";
import { useCallback, useMemo, useRef } from "react";
import type { FieldValues } from "react-hook-form";
import { useBuilderContext } from "../../../../contexts/builder.context";
import { useActionEngine } from "../../../../libs/action-engine";
import type { ActionConfig } from "../../../../libs/action-engine/types";
import type { IField } from "../../../../types/schema";
import type { BuilderRef } from "../../../builder/builder";
import Builder from "../../../builder/builder";

interface IOdataAddEditModalProps extends ComponentProps<typeof Dialog> {
    field: IField;
    isEdit: boolean;
    readOnly?: boolean;
    defaultValues?: any;

    onSubmit?: (values: any) => void;
}

const DOT_ESCAPE = "__dot__";

const escapeKey = (key: string) => key.replace(/\./g, DOT_ESCAPE);
const unescapeKey = (key: string) => key.replace(new RegExp(DOT_ESCAPE, "g"), ".");

const OdataAddEditModal = (props: IOdataAddEditModalProps) => {
    const { field, isEdit, defaultValues, onSubmit, readOnly, ...dialogProps } = props;
    const { t } = useTranslation("autoform");
    const { t: tCommon } = useTranslation("common");

    const subFields = field.fields?.[0] ?? { fields: [] };

    const builderRef = useRef<BuilderRef>(null);

    const { services } = useBuilderContext();

    const actions = useMemo<ActionConfig[]>(
        () => (field.fieldConfig?.wrapperProps?.actions as ActionConfig[] | undefined) ?? [],
        [field.fieldConfig?.wrapperProps?.actions]
    );

    // update_row inside the edit form writes directly onto the RHF form (no refresh).
    const handleUpdateRow = useCallback((partial: Record<string, unknown>) => {
        const methods = builderRef.current?.getMethods();
        if (!methods) return;
        for (const [key, value] of Object.entries(partial)) {
            methods.setValue(escapeKey(key), value, { shouldDirty: true, shouldValidate: true });
        }
    }, []);

    const { handleFormActions } = useActionEngine({
        actions,
        builderRef,
        services,
    });

    // Inject the row-update callback so field controls only need to pass rowData.
    const onFormActions = useCallback(
        (action: string, payload?: Record<string, unknown>) =>
            handleFormActions(action, { ...payload, updateRow: handleUpdateRow }),
        [handleFormActions, handleUpdateRow]
    );

    const handleFormSubmit = (values: FieldValues) => {
        if (onSubmit) {
            const restored = Object.fromEntries(
                Object.entries(values).map(([k, v]) => [unescapeKey(k), v])
            );
            onSubmit(restored);
        }
    }

    const schemaFields = useMemo(() => {
        if (subFields.fields) {
            return (subFields.fields || []).map((f: IField) => {
                const hasDot = f.key.includes(".");
                return {
                    ...f,
                    key: hasDot ? escapeKey(f.key) : f.key,
                    fieldConfig: {
                        ...f.fieldConfig,
                        controlProps: {
                            ...f.fieldConfig?.controlProps,
                            readOnly: readOnly,
                        },
                        fieldWrapper: f.fieldConfig?.fieldWrapper === "FormItemWrapper" ? f.fieldConfig?.fieldWrapper : "FormItemWrapper",
                    }
                }
            });
        }
        return [];
    }, [subFields, readOnly]);

    const safeDefaultValues = useMemo(() => {
        if (!defaultValues) return undefined;
        return Object.fromEntries(
            Object.entries(defaultValues).map(([k, v]) => [escapeKey(k), v])
        );
    }, [defaultValues]);

    return (
        <Dialog {...dialogProps}>
            <DialogContent
                className="min-w-150 max-h-[85vh] overflow-hidden flex flex-col p-0"
                onInteractOutside={(e) => e.preventDefault()}
                onEscapeKeyDown={(e) => e.preventDefault()}
            >
                <DialogHeader className="p-4 border-b font-semibold">
                    {isEdit ? readOnly ? t("modal.view_row") : t("modal.edit_row") : t("modal.add_item")}
                </DialogHeader>

                <div className="flex-2 overflow-y-auto relative grid grid-cols-1 px-4">
                    <Builder
                        ref={builderRef}
                        schema={{ fields: schemaFields }}
                        defaultValues={safeDefaultValues}
                        services={services}

                        onSubmit={handleFormSubmit}
                        onFormActions={onFormActions}
                    />
                </div>

                <DialogFooter className="p-1 flex items-center justify-end px-4 pb-4">
                    <Button variant="outline" onClick={() => dialogProps.onOpenChange?.(false)}>
                        {tCommon("button.cancel")}
                    </Button>

                    {!readOnly && (
                        <Button onClick={() => builderRef.current?.onSubmit()}>
                            {isEdit ? t("modal.save_changes") : t("modal.add_item")}
                        </Button>
                    )}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}

export default OdataAddEditModal;