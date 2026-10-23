import { useMessageBox } from "@/components/providers/messagebox-provider";
import { useLanguage } from "@/hooks/use-language";
import type { IEditorValue } from "@ldc/workflow-editor";
import { useCallback, useEffect, useRef } from "react";
import { useEditorStore } from "../stores/editor-stores";
import { formatVersionLabel } from "../utils/version-utils";
import { UNSAVED_VERSION_SENTINEL } from "./use-workflow-detail-page";

export default function useVersionSwitchGuard() {
    const showMessageBox = useMessageBox();
    const { t } = useLanguage();

    const selectedVersionId = useEditorStore((s) => s.selectedVersionId);
    const setBaseChangeVersion = useEditorStore((s) => s.setBaseChangeVersion);
    const setUnsavedDraftBaseVersion = useEditorStore((s) => s.setUnsavedDraftBaseVersion);

    const warnedRef = useRef(false);
    const lastSelectedVersionRef = useRef(selectedVersionId);

    useEffect(() => {
        if (selectedVersionId !== lastSelectedVersionRef.current) {
            warnedRef.current = false;
            lastSelectedVersionRef.current = selectedVersionId;
        }
    }, [selectedVersionId]);

    const checkVersionSwitch = useCallback(
        async (_editorValue: IEditorValue): Promise<boolean> => {
            const unsavedDraft = useEditorStore.getState().unsavedDraft;
            const selectedVersionId = useEditorStore.getState().selectedVersionId;
            const baseChangeVersion = useEditorStore.getState().baseChangeVersion;

            // No existing draft — nothing to warn about
            if (!unsavedDraft) {
                return true;
            }

            // Editing on the draft view itself — expected
            if (selectedVersionId === UNSAVED_VERSION_SENTINEL) {
                return true;
            }

            // Continuing edits on the same version that produced the draft — no overwrite risk
            if (baseChangeVersion === selectedVersionId) {
                return true;
            }

            // Already confirmed once this session
            if (warnedRef.current) {
                return true;
            }

            const confirmed = await showMessageBox(
                t("version_edit_warning_message", { version: formatVersionLabel(baseChangeVersion ?? "") }),
                t("version_edit_warning_title"),
            );

            if (confirmed) {
                setBaseChangeVersion(selectedVersionId);
                setUnsavedDraftBaseVersion(selectedVersionId);
                warnedRef.current = true;
                return true;
            }

            return false;
        },
        [setBaseChangeVersion, setUnsavedDraftBaseVersion, showMessageBox, t],
    );

    return checkVersionSwitch;
}
