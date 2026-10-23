import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@ldc/ui/components/tooltip";
import type { BeforeMount } from "@monaco-editor/react";
import Editor from "@monaco-editor/react";
import { Check, Copy, Maximize2, Redo2, Undo2, WandSparkles } from "lucide-react";
import React, { forwardRef, useEffect, useRef, useState } from "react";
import { useWatch } from "react-hook-form";
import type { FieldComponentProps } from "../../types/schema";

// Track the app theme via the `.dark` class the design-system ThemeProvider
// toggles on <html>, so Monaco follows light/dark without the caller wiring it.
function useDocumentDark(): boolean {
    const [dark, setDark] = useState(
        () => typeof document !== "undefined" && document.documentElement.classList.contains("dark")
    );
    useEffect(() => {
        if (typeof document === "undefined") return;
        const el = document.documentElement;
        const sync = () => setDark(el.classList.contains("dark"));
        sync();
        const obs = new MutationObserver(sync);
        obs.observe(el, { attributes: true, attributeFilter: ["class"] });
        return () => obs.disconnect();
    }, []);
    return dark;
}

export interface LdcCodeEditorProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "onChange"> {
    value?: string;
    onChange?: (value: string) => void;
    language: string;
    placeholder?: string;
    disabled?: boolean;
    readOnly?: boolean;
    minHeight?: number;
    maxHeight?: number;
    className?: string;
    dark?: boolean;
    /** Register Monaco extra libs / diagnostics before the editor mounts (e.g. the ctx .d.ts). */
    beforeMount?: BeforeMount;
    /** Show the "expand" toolbar button that opens the editor full-size in a dialog. Default true. */
    expandable?: boolean;
    /** Dialog title when expanded. */
    expandTitle?: string;
    /** Fill the parent's height (Monaco height 100%) instead of growing with content. */
    fill?: boolean;
}

/**
 * Coerce a field value into the string Monaco requires. Callers can hand us a
 * non-string — e.g. an auto-form JSON field whose stored/default value is a real
 * array or object (the node-definition "Try it" form restores such values from a
 * prior run). Render those as pretty JSON instead of letting Monaco throw.
 */
export function toEditorValue(value: unknown): string {
    if (typeof value === "string") return value;
    if (value == null) return "";
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

interface IToolbarButtonProps {
    label: string;
    onClick: () => void;
    children: React.ReactNode;
}

function ToolbarButton({ label, onClick, children }: IToolbarButtonProps) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7 text-muted-foreground hover:text-foreground"
                    onClick={onClick}
                    aria-label={label}
                >
                    {children}
                </Button>
            </TooltipTrigger>
            <TooltipContent>{label}</TooltipContent>
        </Tooltip>
    );
}

const LdcCodeEditor = forwardRef<HTMLDivElement, LdcCodeEditorProps>(({
    value = "",
    onChange,
    language = "javascript",
    placeholder = "Write your code here…",
    disabled = false,
    readOnly = false,
    minHeight = 160,
    maxHeight = 480,
    className = "",
    dark,
    beforeMount,
    expandable = true,
    expandTitle,
    fill = false,
}, ref) => {
    const { t } = useTranslation("autoform");
    const [isFocused, setIsFocused] = useState<boolean>(false);
    const [editorHeight, setEditorHeight] = useState<number>(minHeight);
    const [copied, setCopied] = useState<boolean>(false);
    const [expanded, setExpanded] = useState<boolean>(false);
    const editorRef = useRef<any>(null);
    // Explicit `dark` prop wins; otherwise follow the app theme.
    const docDark = useDocumentDark();
    const isDark = dark ?? docDark;

    // Monaco's model value must be a string — coerce non-strings (see toEditorValue).
    const text = toEditorValue(value);

    const showPlaceholder = !text && !isFocused;

    function handleUndo() {
        editorRef.current?.focus();
        editorRef.current?.trigger("toolbar", "undo", null);
    }

    function handleRedo() {
        editorRef.current?.focus();
        editorRef.current?.trigger("toolbar", "redo", null);
    }

    function handleFormat() {
        editorRef.current?.focus();
        editorRef.current?.getAction("editor.action.formatDocument")?.run();
    }

    async function handleCopy() {
        const copyText = editorRef.current?.getValue() ?? text;
        if (!copyText) return;
        try {
            await navigator.clipboard.writeText(copyText);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            // Clipboard unavailable (insecure context / denied permission) —
            // silently ignore; copy is a convenience, not a critical path.
        }
    }

    function handleMount(editor: any, monaco: any) {
        editorRef.current = editor;

        editor.getDomNode()?.style.setProperty("outline", "none");

        editor.onDidFocusEditorText(() => setIsFocused(true));
        editor.onDidBlurEditorText(() => setIsFocused(false));

        editor.onDidContentSizeChange(() => {
            const contentHeight = Math.min(
                maxHeight,
                Math.max(minHeight, editor.getContentHeight())
            );
            setEditorHeight(contentHeight);
        });

        monaco.editor.defineTheme("shadcn-light", {
            base: "vs",
            inherit: true,
            rules: [],
            colors: {
                "editor.background": "#ffffff",
                "editor.foreground": "#09090b",
                "editorLineNumber.foreground": "#a1a1aa",
                "editorLineNumber.activeForeground": "#71717a",
                "editor.selectionBackground": "#f4f4f5",
                "editor.inactiveSelectionBackground": "#f4f4f580",
                "editorCursor.foreground": "#09090b",
                "editorIndentGuide.background": "#f4f4f5",
                "editorIndentGuide.activeBackground": "#e4e4e7",
                "editor.lineHighlightBackground": "#fafafa",
                "editorGutter.background": "#ffffff",
                "scrollbarSlider.background": "#e4e4e780",
                "scrollbarSlider.hoverBackground": "#d4d4d8",
            },
        });

        monaco.editor.defineTheme("shadcn-dark", {
            base: "vs-dark",
            inherit: true,
            rules: [],
            colors: {
                "editor.background": "#09090b",
                "editor.foreground": "#fafafa",
                "editorLineNumber.foreground": "#52525b",
                "editorLineNumber.activeForeground": "#a1a1aa",
                "editor.selectionBackground": "#27272a",
                "editor.inactiveSelectionBackground": "#27272a80",
                "editorCursor.foreground": "#fafafa",
                "editorIndentGuide.background": "#27272a",
                "editorIndentGuide.activeBackground": "#3f3f46",
                "editor.lineHighlightBackground": "#18181b",
                "editorGutter.background": "#09090b",
                "scrollbarSlider.background": "#27272a80",
                "scrollbarSlider.hoverBackground": "#3f3f46",
            },
        });

        monaco.editor.setTheme(isDark ? "shadcn-dark" : "shadcn-light");

        editor.updateOptions({
            formatOnPaste: false,
            formatOnType: false,
            automaticLayout: true,
        });

        const editorContainer = editor.getContainerDomNode();

        editorContainer.addEventListener("dragover", (e: any) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
        });

        editorContainer.addEventListener("drop", (e: any) => {
            e.preventDefault();

            const textToInsert = e.dataTransfer.getData("text/plain");

            if (textToInsert) {
                const target = editor.getTargetAtClientPoint(e.clientX, e.clientY);

                if (target?.position) {
                    const position = target.position;

                    editor.setPosition(position);
                    editor.focus();

                    editor.executeEdits("dnd-drop", [
                        {
                            range: new monaco.Range(
                                position.lineNumber,
                                position.column,
                                position.lineNumber,
                                position.column
                            ),
                            text: `"${textToInsert}"`,
                            forceMoveMarkers: true
                        },
                    ]);

                    // Do NOT format after a drop: dropped text is typically a
                    // {{$node-id...}} variable reference whose hyphens Monaco
                    // would mangle into " - " operator spacing.
                }
            }
        });
    }

    return (
        <div ref={ref} className={cn("flex flex-col gap-1.5 w-full", fill && "h-full min-h-0", className)}>
            <div
                className={cn(
                    "relative rounded-lg border overflow-hidden",
                    fill && "flex flex-col h-full min-h-0",
                    "transition-[border-color,box-shadow] duration-150",
                    "bg-background dark:bg-input/30",
                    isFocused
                        ? "border-ring ring-3 ring-ring/50"
                        : "border-input",
                    "aria-invalid:ring-destructive/20 aria-invalid:border-destructive aria-invalid:ring-3",
                    "dark:aria-invalid:ring-destructive/40 dark:aria-invalid:border-destructive/50",
                    "aria-disabled:pointer-events-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:bg-input/50",
                    "dark:aria-disabled:bg-input/80",
                )}
                aria-disabled={disabled}
            >
                {showPlaceholder && (
                    <div
                        className={cn(
                            "absolute top-2 left-2 z-10 pointer-events-none",
                            "text-xs leading-[21px] whitespace-pre font-mono",
                            "text-muted-foreground",
                        )}
                    >
                        {placeholder}
                    </div>
                )}

                <div
                    className={cn(
                        "absolute top-1.5 right-2 z-20",
                        "px-1.5 py-px rounded text-[11px] font-medium",
                        "tracking-wide uppercase select-none",
                        "bg-muted text-muted-foreground"
                    )}
                >
                    {language}
                </div>

                <div className={fill ? "relative flex-1 min-h-0" : "contents"}>
                <Editor
                    height={fill ? "100%" : editorHeight}
                    language={language}
                    value={text}
                    onChange={(val) => onChange?.(val ?? "")}
                    beforeMount={beforeMount}
                    onMount={handleMount}
                    theme={isDark ? "shadcn-dark" : "shadcn-light"}
                    options={{
                        minimap: { enabled: false },
                        lineNumbers: "off",
                        lineNumbersMinChars: 0,
                        scrollBeyondLastLine: false,
                        wordWrap: "on",
                        fontSize: 13,
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
                        readOnly,
                        domReadOnly: readOnly,
                        contextmenu: !readOnly,

                        folding: true,
                        foldingHighlight: true,
                        glyphMargin: false,
                        showFoldingControls: "mouseover",
                    }}
                />
                </div>

                {!disabled && (
                    <div
                        className={cn(
                            "flex items-center gap-0.5 border-t border-input",
                            "bg-background dark:bg-input/30 px-1.5 py-1",
                        )}
                    >
                        <TooltipProvider delayDuration={300}>
                            {!readOnly && (
                                <>
                                    <ToolbarButton label={t("code.undo")} onClick={handleUndo}>
                                        <Undo2 className="size-3.5" />
                                    </ToolbarButton>
                                    <ToolbarButton label={t("code.redo")} onClick={handleRedo}>
                                        <Redo2 className="size-3.5" />
                                    </ToolbarButton>
                                    <ToolbarButton label={t("code.format")} onClick={handleFormat}>
                                        <WandSparkles className="size-3.5" />
                                    </ToolbarButton>
                                </>
                            )}
                            <ToolbarButton
                                label={copied ? t("code.copied") : t("code.copy")}
                                onClick={handleCopy}
                            >
                                {copied ? (
                                    <Check className="size-3.5 text-green-600" />
                                ) : (
                                    <Copy className="size-3.5" />
                                )}
                            </ToolbarButton>
                            {expandable && (
                                <ToolbarButton label="Expand" onClick={() => setExpanded(true)}>
                                    <Maximize2 className="size-3.5" />
                                </ToolbarButton>
                            )}
                        </TooltipProvider>
                    </div>
                )}
            </div>

            {expandable && (
                <Dialog open={expanded} onOpenChange={setExpanded}>
                    <DialogContent className="flex h-[90vh] w-[95vw] max-w-[95vw] flex-col gap-3 p-4 sm:max-w-[95vw]">
                        <DialogHeader className="shrink-0">
                            <DialogTitle>{expandTitle ?? "Code editor"}</DialogTitle>
                        </DialogHeader>
                        <div className="min-h-0 flex-1 overflow-hidden">
                            <LdcCodeEditor
                                language={language}
                                value={value}
                                onChange={onChange}
                                beforeMount={beforeMount}
                                dark={dark}
                                readOnly={readOnly}
                                disabled={disabled}
                                placeholder={placeholder}
                                expandable={false}
                                fill
                                className="h-full"
                            />
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
});

LdcCodeEditor.displayName = "LdcCodeEditor";

export interface ICodeControlProps extends FieldComponentProps {
    language?: string;
    languageField?: string;
}

function resolveSiblingPath(ownName: string | undefined, key: string): string {
    if (key.includes(".") || !ownName?.includes(".")) return key;
    const parts = ownName.split(".");
    parts[parts.length - 1] = key;
    return parts.join(".");
}

interface IWatchedCodeControlProps {
    /** Sibling field key (or absolute path) whose value drives the language. */
    fieldName: string;
    /** Language used until the watched field has a value. */
    fallbackLanguage: string;
    /** This field's own form path, used to resolve the sibling. */
    name?: string;
    value?: string;
    onChange?: (value: string) => void;
    error?: unknown;
    [key: string]: any;
}

/**
 * Variant rendered when `languageField` is provided: subscribes to the named
 * form field so the editor language updates whenever the user switches it.
 * Kept as a separate component so `useWatch` is only mounted inside a form
 * context (the read-only output preview has no `languageField` and skips it).
 */
const WatchedCodeControl = forwardRef<HTMLDivElement, IWatchedCodeControlProps>(
    (props, ref) => {
        const { fieldName, fallbackLanguage, name, value, onChange, error, ...rest } = props;
        const watchPath = resolveSiblingPath(name, fieldName);
        const watched = useWatch({ name: watchPath });
        const language = typeof watched === "string" && watched ? watched : fallbackLanguage;

        return (
            <LdcCodeEditor ref={ref} language={language} value={value} onChange={onChange} {...rest} aria-invalid={!!error} />
        );
    },
);

WatchedCodeControl.displayName = "WatchedCodeControl";

const CodeControl = forwardRef<HTMLDivElement, ICodeControlProps>(
    (props, ref) => {
        const { language = "json", languageField, value, onChange, error, ...rest } = props;

        if (languageField) {
            return (
                <WatchedCodeControl
                    ref={ref}
                    fieldName={languageField}
                    fallbackLanguage={language}
                    value={value}
                    onChange={onChange}
                    error={error}
                    {...rest}
                />
            );
        }

        return (
            <LdcCodeEditor ref={ref} language={language} value={value} onChange={onChange} {...rest} aria-invalid={!!error} />
        )
    });

CodeControl.displayName = "CodeControl";

export { CodeControl };

export default LdcCodeEditor;
