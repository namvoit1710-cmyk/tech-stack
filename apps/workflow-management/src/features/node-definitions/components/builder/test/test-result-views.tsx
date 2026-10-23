// Presentational pieces for rendering a node-definition test outcome. Extracted from the old
// test-section so the "Before test" (resolved input) and "After test" (outputs) tabs can each
// show their slice of the same run. Pure/leaf components — unit-testable.
import JsonView from "@/components/json-view/json-view";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { CheckIcon, CopyIcon, InfoIcon, SaveIcon } from "lucide-react";
import { useState } from "react";

export const isSuccessStatus = (s: string) => ["success", "completed"].includes(s.toLowerCase());
export const isFailedStatus = (s: string) => ["failed", "error"].includes(s.toLowerCase());

export const CopyButton = ({ value }: { value: unknown }) => {
    const { t } = useLanguage();
    const [copied, setCopied] = useState(false);
    return (
        <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            aria-label={t("node_definition_builder.copy")}
            onClick={() => {
                navigator.clipboard?.writeText(JSON.stringify(value ?? {}, null, 2));
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
            }}
        >
            {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
        </Button>
    );
};

export const TestStatusBadge = ({ status, executionMs }: { status: string; executionMs?: number }) => (
    <div className="flex items-center gap-4">
        <Badge
            className={isSuccessStatus(status) ? "bg-green-100 text-green-800 border-green-200" : ""}
            variant={isFailedStatus(status) ? "destructive" : "outline"}
        >
            {status}
        </Badge>
        {!!executionMs && executionMs > 0 && <span className="text-sm text-muted-foreground">{executionMs}ms</span>}
    </div>
);

const JsonResultPanel = ({ label, value }: { label: string; value: unknown }) => (
    <div>
        <div className="flex items-center justify-between mb-2">
            <h5 className="text-xs font-semibold uppercase text-muted-foreground">{label}</h5>
            <CopyButton value={value} />
        </div>
        <div className="border rounded-lg bg-muted/40 p-3">
            <JsonView value={(value as Record<string, unknown>) || {}} collapsed={2} />
        </div>
    </div>
);

export const ResolvedInputPanel = ({ value }: { value: unknown }) => {
    const { t } = useLanguage();
    return <JsonResultPanel label={t("node_definition_builder.test_resolved_input")} value={value} />;
};

export const OutputsPanel = ({ value }: { value: unknown }) => {
    const { t } = useLanguage();
    return <JsonResultPanel label={t("node_definition_builder.test_outputs")} value={value} />;
};

export const WorkerErrorBlock = ({ message }: { message: string }) => {
    const { t } = useLanguage();
    return (
        <div className="border border-destructive/50 bg-destructive/5 rounded-lg p-3 text-sm text-destructive">
            <strong>{t("node_definition_builder.test_error")}:</strong> {message}
        </div>
    );
};

export const RequestErrorPanel = ({ message }: { message: string }) => (
    <div className="border border-destructive/50 bg-destructive/5 rounded-lg p-4 text-sm text-destructive">{message}</div>
);

export const NotTestablePanel = () => {
    const { t } = useLanguage();
    return (
        <div className="flex flex-col gap-2 border border-amber-200 bg-amber-50 rounded-lg p-4 text-sm text-amber-800">
            <div className="flex items-center gap-2 font-medium">
                <InfoIcon className="size-4" />
                {t("node_definition_builder.test_on_canvas_title")}
            </div>
            <p>{t("node_definition_builder.test_on_canvas_desc")}</p>
        </div>
    );
};

export const OutputReferenceLine = ({ reference }: { reference: string }) => {
    const { t } = useLanguage();
    return (
        <div>
            <h5 className="text-xs font-semibold uppercase text-muted-foreground mb-2">{t("node_definition_builder.test_output_reference")}</h5>
            <code className="text-xs bg-muted px-2 py-1 rounded">{reference}</code>
        </div>
    );
};

export const Hint = ({ icon, text, className }: { icon?: React.ReactNode; text: string; className?: string }) => (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-10 text-center text-sm text-muted-foreground", className)}>
        {icon}
        <p>{text}</p>
    </div>
);

export const SaveFirstHint = () => {
    const { t } = useLanguage();
    return <Hint icon={<SaveIcon className="size-6" />} text={t("node_definition_builder.test_save_first")} />;
};

export const IdleHint = () => {
    const { t } = useLanguage();
    return <Hint text={t("node_definition_builder.test_idle_message")} />;
};
