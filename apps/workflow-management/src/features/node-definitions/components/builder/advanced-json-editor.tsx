import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { CheckIcon, CopyIcon } from "lucide-react";
import { useCallback, useState } from "react";

interface AdvancedJsonEditorProps {
    /** Initial parsed value rendered as formatted JSON. Re-mount (via `key`)
     *  to reset from a new source — the editor is otherwise self-managed. */
    defaultValue: unknown;
    /** Called with the parsed value on every *valid* edit. */
    onChange: (parsed: any) => void;
    /** Called whenever validity changes (used to gate Save). */
    onValidityChange?: (valid: boolean) => void;
    placeholder?: string;
    rows?: number;
}

/**
 * A validated raw-JSON escape hatch. Shows inline parse errors instead of
 * silently dropping invalid JSON, and reports validity up so the builder can
 * disable Save. Salvaged from the former node-definition-schema-editor.
 */
const AdvancedJsonEditor = ({
    defaultValue,
    onChange,
    onValidityChange,
    placeholder,
    rows = 12,
}: AdvancedJsonEditorProps) => {
    const { t } = useLanguage();
    const [text, setText] = useState(() => {
        try {
            return JSON.stringify(defaultValue ?? null, null, 2);
        } catch {
            return "";
        }
    });
    const [error, setError] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);

    const handleChange = useCallback(
        (next: string) => {
            setText(next);
            if (!next.trim()) {
                setError(null);
                onValidityChange?.(true);
                onChange(Array.isArray(defaultValue) ? [] : {});
                return;
            }
            try {
                const parsed = JSON.parse(next);
                setError(null);
                onValidityChange?.(true);
                onChange(parsed);
            } catch (err) {
                setError((err as Error).message);
                onValidityChange?.(false);
            }
        },
        [defaultValue, onChange, onValidityChange]
    );

    const handleFormat = useCallback(() => {
        if (!text.trim()) return;
        try {
            setText(JSON.stringify(JSON.parse(text), null, 2));
            setError(null);
        } catch (err) {
            setError((err as Error).message);
        }
    }, [text]);

    const handleCopy = useCallback(() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    }, [text]);

    return (
        <div className="flex flex-col gap-1">
            <div className="flex items-center justify-end gap-1">
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs"
                    onClick={handleFormat}
                    disabled={!text.trim()}
                >
                    {t("node_definitions.format_json")}
                </Button>
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs"
                    onClick={handleCopy}
                    disabled={!text.trim()}
                    aria-label={t("node_definition_builder.copy")}
                >
                    {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
                </Button>
            </div>
            <textarea
                className={cn(
                    "w-full font-mono text-xs p-3 border rounded-md resize-y",
                    "focus:outline-none focus:ring-2 focus:ring-ring",
                    error ? "border-destructive/40" : "border-border"
                )}
                value={text}
                onChange={(e) => handleChange(e.target.value)}
                placeholder={placeholder}
                rows={rows}
                spellCheck={false}
                aria-invalid={!!error}
            />
            {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
    );
};

export default AdvancedJsonEditor;
