import type { ISchema } from "../../../../types/schema";
import { cn } from "@ldc/ui";
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import { Editor } from "@monaco-editor/react";
import { Check, ClipboardCopy, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useDesignerCtx } from "../../hooks/use-designer-context";

export function JsonPreview() {
    const { schema, importSchema } = useDesignerCtx();
    const [copied, setCopied] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const json = JSON.stringify(schema, null, 2);

    const copy = async () => {
        await navigator.clipboard.writeText(json).catch(() => undefined);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const importFromClipboard = () => {
        void navigator.clipboard.readText().then((text) => {
            try {
                const parsed: ISchema = JSON.parse(text);
                if (!Array.isArray(parsed?.fields)) { setError("Invalid ISchema"); return; }
                importSchema(parsed);
                setError(null);
            } catch { setError("Invalid JSON in clipboard"); }
        });
    };

    const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
            try {
                const parsed: ISchema = JSON.parse(ev.target?.result as string);
                if (!Array.isArray(parsed?.fields)) { setError("Invalid ISchema"); return; }
                importSchema(parsed);
                setError(null);
            } catch { setError("Failed to parse JSON"); }
        };
        reader.readAsText(file);
        e.target.value = "";
    };

    const handleJsonChange = useDebounceCallback((value: string | undefined) => {
        if (value === undefined) return;
        try {
            const parsed: ISchema = JSON.parse(value);
            if (!Array.isArray(parsed?.fields)) { setError("Invalid ISchema"); return; }
            importSchema(parsed);
            setError(null);
        } catch { setError("Invalid JSON in clipboard"); }
    }, 500);

    return (
        <div className="border-t border-border shrink-0 bg-background h-full flex flex-col">
            <div
                className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-muted/40"
            >
                <div className="flex items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                        ISchema JSON
                    </span>
                    <span className="text-[9px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full">
                        {schema.fields.length} fields
                    </span>
                </div>
                <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <button onClick={importFromClipboard} className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground" title="Import from clipboard">
                        <Upload className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => fileRef.current?.click()} className="p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground" title="Import from file">
                        <Upload className="w-3.5 h-3.5 rotate-180" />
                    </button>
                    <button onClick={copy} className={cn("p-1 rounded hover:bg-accent transition-colors", copied ? "text-green-600" : "text-muted-foreground hover:text-foreground")} title="Copy JSON">
                        {copied ? <Check className="w-3.5 h-3.5" /> : <ClipboardCopy className="w-3.5 h-3.5" />}
                    </button>
                </div>
            </div>

            {error && <p className="text-xs text-destructive px-3 pb-1">{error}</p>}

            <div className="flex-2 overflow-hidden">
                <Editor
                    height={"100%"}
                    language={"json"}
                    value={json}
                    onChange={handleJsonChange}
                    options={{
                        minimap: { enabled: false },
                        lineNumbers: "off",
                        lineNumbersMinChars: 0,
                        scrollBeyondLastLine: false,
                        wordWrap: "on",
                        fontSize: 11,
                        tabSize: 2,
                        insertSpaces: true,
                        renderLineHighlight: "line",
                        cursorBlinking: "smooth",
                        cursorSmoothCaretAnimation: "on",
                        smoothScrolling: true,
                        padding: { top: 8, bottom: 8 },
                        overviewRulerLanes: 0,
                        hideCursorInOverviewRuler: true,
                        scrollbar: {
                            vertical: "auto",
                            horizontal: "hidden",
                            verticalScrollbarSize: 6,
                            useShadows: false,
                        },
                        lineDecorationsWidth: 0,
                        automaticLayout: true,
                        domReadOnly: true,

                        folding: true,
                        foldingHighlight: true,
                        glyphMargin: false,
                        showFoldingControls: "mouseover",
                    }}
                />
            </div>

            <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={onFile} />
        </div>
    );
}