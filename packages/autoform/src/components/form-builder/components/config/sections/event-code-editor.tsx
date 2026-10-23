import type { BeforeMount } from "@monaco-editor/react";
import { useCallback } from "react";
import LdcCodeEditor from "../../../../field-control/code-control";
import { ACTION_SCRIPT_CTX_DTS } from "../../../../../libs/action-engine/script/action-script-ctx.dts";

export interface EventCodeEditorProps {
    value: string;
    onChange: (value: string) => void;
    dark?: boolean;
    height?: number;
}

let extraLibRegistered = false;

/**
 * Event script editor. Wraps the shared LdcCodeEditor (package/ui code editor) so
 * the Events section gets the same toolbar (undo/redo/format/copy) + an "Expand"
 * button for a full-size editor — instead of a bare, cramped Monaco instance.
 * `beforeMount` registers the ActionScriptCtx `.d.ts` for `ctx.` IntelliSense.
 */
export function EventCodeEditor({ value, onChange, dark, height }: EventCodeEditorProps) {
    const beforeMount: BeforeMount = useCallback((monaco) => {
        const js = monaco.languages.typescript.javascriptDefaults;
        // Register the ctx contract once per Monaco instance (module-level guard).
        if (!extraLibRegistered) {
            js.addExtraLib(ACTION_SCRIPT_CTX_DTS, "ts:action-script-ctx.d.ts");
            extraLibRegistered = true;
        }
        // Keep IntelliSense, drop red squiggles on author-trusted code.
        js.setDiagnosticsOptions({ noSemanticValidation: true, noSyntaxValidation: false });
    }, []);

    return (
        <LdcCodeEditor
            language="javascript"
            value={value}
            onChange={onChange}
            beforeMount={beforeMount}
            dark={dark}
            minHeight={height ?? 220}
            maxHeight={520}
            expandTitle="Event script"
        />
    );
}
