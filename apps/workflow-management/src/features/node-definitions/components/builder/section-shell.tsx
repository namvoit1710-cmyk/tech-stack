import { cn } from "@ldc/ui";
import { useLanguage } from "@/hooks/use-language";
import { Code2Icon, SlidersHorizontalIcon } from "lucide-react";
import type { ReactNode } from "react";

export type SectionMode = "guided" | "advanced";

interface SectionShellProps {
    title: string;
    help?: string;
    icon?: ReactNode;
    /** Hide the Guided|Advanced toggle (e.g. the Overview or Review section). */
    hideToggle?: boolean;
    mode?: SectionMode;
    onModeChange?: (mode: SectionMode) => void;
    /** Guided (visual) content. */
    guided: ReactNode;
    /** Advanced (raw JSON) content — required unless hideToggle. */
    advanced?: ReactNode;
    actions?: ReactNode;
}

/**
 * The single, consistent frame for every builder section: an aligned header
 * (icon + title + one-line help + optional actions / Guided|Advanced toggle)
 * over the section body. Presentational only — the parent owns `mode` and the
 * guided↔advanced conversion.
 */
const SectionShell = ({
    title,
    help,
    icon,
    hideToggle,
    mode = "guided",
    onModeChange,
    guided,
    advanced,
    actions,
}: SectionShellProps) => {
    const { t } = useLanguage();

    return (
        <section className="flex flex-col gap-5">
            <header className="flex items-start justify-between gap-4 border-b border-border pb-4">
                <div className="flex items-start gap-3 min-w-0">
                    {icon && (
                        <span className="flex items-center justify-center size-9 shrink-0 rounded-lg bg-primary/10 text-primary">
                            {icon}
                        </span>
                    )}
                    <div className="space-y-1 min-w-0">
                        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
                        {help && <p className="text-sm text-muted-foreground max-w-prose">{help}</p>}
                    </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    {actions}
                    {!hideToggle && (
                        <div
                            role="tablist"
                            aria-label={t("node_definition_builder.edit_mode")}
                            className="inline-flex items-center rounded-lg border border-border bg-muted/50 p-0.5"
                        >
                            <ToggleButton
                                active={mode === "guided"}
                                onClick={() => onModeChange?.("guided")}
                                icon={<SlidersHorizontalIcon className="size-3.5" />}
                                label={t("node_definition_builder.guided")}
                            />
                            <ToggleButton
                                active={mode === "advanced"}
                                onClick={() => onModeChange?.("advanced")}
                                icon={<Code2Icon className="size-3.5" />}
                                label={t("node_definition_builder.advanced_json")}
                            />
                        </div>
                    )}
                </div>
            </header>

            {hideToggle || mode === "guided" ? guided : advanced}
        </section>
    );
};

const ToggleButton = ({
    active,
    onClick,
    icon,
    label,
}: {
    active: boolean;
    onClick: () => void;
    icon: ReactNode;
    label: string;
}) => (
    <button
        type="button"
        role="tab"
        aria-selected={active}
        onClick={onClick}
        className={cn(
            "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
            active ? "bg-card shadow-sm text-foreground font-medium" : "text-muted-foreground hover:text-foreground"
        )}
    >
        {icon}
        {label}
    </button>
);

export default SectionShell;
