import { useTranslation } from "@ldc/i18n";
import { toast } from "@ldc/ui/blocks/toast/toast";
import type { RefObject } from "react";
import { useCallback, useState } from "react";
import type { IEditorInstance, IEditorValue } from "../components/rete-editor/types";
import { RETE_EDITOR_I18N_NAMESPACE } from "../i18n";
import { validateEditorValue } from "../utils/validate-editor-value";

export interface handlerLoadingType {
    isCopyLoading: boolean;
    isPasteLoading: boolean;
}

export function useEditorClipboard({
    editorInstance,
    isLoadingRef,
    readOnly,
    onChange,
}: {
    editorInstance: IEditorInstance | undefined;
    isLoadingRef: RefObject<boolean>;
    readOnly: boolean;
    onChange?: (value: IEditorValue) => void;
}) {
    const { t } = useTranslation(RETE_EDITOR_I18N_NAMESPACE);

    const [isHandlerLoading, setHandlerLoading] = useState<handlerLoadingType>({
        isCopyLoading: false,
        isPasteLoading: false,
    });

    const handleCopy = useCallback(async () => {
        if (!editorInstance) return;

        setHandlerLoading(prev => ({ ...prev, isCopyLoading: true }))

        const data = editorInstance.serializeNodes();
        await navigator.clipboard.writeText(JSON.stringify(data, null, 2));

        setTimeout(() => {
            setHandlerLoading(prev => ({ ...prev, isCopyLoading: false }))
            toast.success(t("notification.success"), t("notification.copied_successfully"));
        }, 400)
    }, [editorInstance]);

    const handlePaste = useCallback(async (e?: React.ClipboardEvent<HTMLDivElement>) => {
        if (!editorInstance || readOnly) return;

        e?.preventDefault();

        setHandlerLoading(prev => ({ ...prev, isPasteLoading: true }))

        let text = await navigator.clipboard.readText();
        if (!text && e) {
            text = e.clipboardData.getData("text/plain")
        }

        try {
            const parsed: unknown = JSON.parse(text);

            if (!validateEditorValue(parsed)) {
                toast.warning(t("notification.warning"), t("notification.pasted_json_is_not_valid"));
                return;
            }

            isLoadingRef.current = true;

            try {
                await editorInstance.initialLoadNodes(parsed);
                onChange?.(parsed)
            } finally {
                toast.success(t("notification.success"), t("notification.pasted_json_successfully"));
                isLoadingRef.current = false;
            }
        } catch {
            toast.warning(t("notification.warning"), t("notification.failed_to_parse_pasted_json"));
        } finally {
            setHandlerLoading(prev => ({ ...prev, isPasteLoading: false }))
        }
    }, [editorInstance, isLoadingRef, readOnly]);

    return {
        isHandlerLoading,
        clipboardHandlers: {
            onCopy: handleCopy,
            onPaste: handlePaste,
        },
    };
}
