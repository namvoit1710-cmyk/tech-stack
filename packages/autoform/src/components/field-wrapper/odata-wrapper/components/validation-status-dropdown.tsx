import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Checkbox } from "@ldc/ui/components/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@ldc/ui/components/popover";
import { ScrollArea } from "@ldc/ui/components/scroll-area";
import { AlertCircle, AlertTriangle } from "lucide-react";

interface ValidationStatusDropdownProps {
    status: "error" | "warning";
    rows: any[];
    selectedIds: Set<string>;
    onToggle: (id: string, checked: boolean) => void;
    onClear: () => void;
    tableData: any[];
}

const ValidationStatusDropdown = ({
    status,
    rows,
    selectedIds,
    onToggle,
    onClear,
    tableData,
}: ValidationStatusDropdownProps) => {
    const { t } = useTranslation("autoform");
    const { t: tCommon } = useTranslation("common");

    const count = rows.length;
    const hasSelected = selectedIds.size > 0;

    const Icon = status === "error" ? AlertCircle : AlertTriangle;
    const colorClass = status === "error"
        ? "text-destructive border-destructive/30 bg-destructive/5 hover:bg-destructive/10"
        : "text-yellow-600 border-yellow-300 bg-yellow-50 hover:bg-yellow-100";
    const badgeClass = status === "error"
        ? "bg-destructive text-white"
        : "bg-yellow-500 text-white";
    const label = status === "error" ? t("validation.errors") : t("validation.warnings");

    const getRowIndex = (rowId: string) =>
        tableData.findIndex(r => r._id === rowId) + 1 || "?";

    if (count === 0) return null;

    return (
        <Popover>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    className={cn("gap-1.5", colorClass, hasSelected && "ring-1 ring-offset-1")}
                >
                    <Icon className="size-3.5" />
                    {label}
                    <span className={cn("rounded-full px-1.5 text-xs font-semibold", badgeClass)}>
                        {count}
                    </span>
                </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-72 p-0">
                <div className="flex items-center justify-between px-3 py-2 border-b">
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                        {label} ({count})
                    </span>
                    {hasSelected && (
                        <button
                            onClick={onClear}
                            className="text-xs text-muted-foreground hover:text-foreground underline"
                        >
                            {tCommon("button.clear")}
                        </button>
                    )}
                </div>
                <ScrollArea className="max-h-60">
                    <div className="flex flex-col py-1">
                        {rows.map((row) => (
                            <label
                                key={row._id}
                                className="flex items-start gap-2.5 px-3 py-2 cursor-pointer hover:bg-muted/50 transition-colors"
                            >
                                <Checkbox
                                    checked={selectedIds.has(row._id)}
                                    onCheckedChange={(checked) => onToggle(row._id, !!checked)}
                                    className="mt-0.5 flex-shrink-0"
                                />
                                <span className="text-sm leading-snug">
                                    <span className="font-medium">{t("validation.row_label", { index: getRowIndex(row._id) })}</span>
                                    {row.__validate_message && (
                                        <span className="text-muted-foreground"> — {row.__validate_message}</span>
                                    )}
                                </span>
                            </label>
                        ))}
                    </div>
                </ScrollArea>
            </PopoverContent>
        </Popover>
    );
};

export default ValidationStatusDropdown;
