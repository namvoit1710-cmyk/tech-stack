import { useTranslation } from "@ldc/i18n";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Combobox, ComboboxChip, ComboboxChips, ComboboxChipsInput, ComboboxContent, ComboboxEmpty, ComboboxItem, ComboboxList, useComboboxAnchor } from "@ldc/ui/components/combobox";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { ScrollArea } from "@ldc/ui/components/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { useEffect, useMemo, useState } from "react";

export type FilterOperator = "is" | "is_not" | "contains" | "not_contains";

export interface FilterCondition {
    operator: FilterOperator;
    values: string[];
}

interface ColumnFilterDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    columns: { key: string; label: string }[];
    tableData: any[];
    value: Record<string, FilterCondition>;
    onApply: (filters: Record<string, FilterCondition>) => void;
}

const ColumnFilterDialog = ({
    open,
    onOpenChange,
    columns,
    tableData,
    value,
    onApply,
}: ColumnFilterDialogProps) => {
    const { t } = useTranslation("autoform");
    const { t: tCommon } = useTranslation("common");

    const operators = useMemo<{ value: FilterOperator; label: string }[]>(() => [
        { value: "is", label: t("filter.operator.is") },
        { value: "is_not", label: t("filter.operator.is_not") },
        { value: "contains", label: t("filter.operator.contains") },
        { value: "not_contains", label: t("filter.operator.not_contains") },
    ], [t]);
    const [draft, setDraft] = useState<Record<string, FilterCondition>>(value);
    const [selectedCol, setSelectedCol] = useState<string>(columns[0]?.key ?? "");
    const [portalContainer, setPortalContainer] = useState<HTMLElement | null>(null);
    const anchor = useComboboxAnchor();

    // sync draft when dialog opens (not on every value change to preserve in-progress edits)
    useEffect(() => {
        if (open) setDraft(value);
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    // guard stale selectedCol if columns shrinks
    useEffect(() => {
        if (selectedCol && !columns.some(c => c.key === selectedCol))
            setSelectedCol(columns[0]?.key ?? "");
    }, [columns, selectedCol]);

    const uniqueValues = useMemo(() => {
        if (!selectedCol) return [];
        return [...new Set(
            tableData.map(r => String(r[selectedCol] ?? "")).filter(Boolean)
        )].sort();
    }, [tableData, selectedCol]);

    const currentCondition: FilterCondition = draft[selectedCol] ?? { operator: "is", values: [] };

    const updateDraft = (col: string, patch: Partial<FilterCondition>) => {
        setDraft(prev => ({
            ...prev,
            [col]: { ...(prev[col] ?? { operator: "is", values: [] }), ...patch },
        }));
    };

    const handleApply = () => {
        const cleaned = Object.fromEntries(
            Object.entries(draft).filter(([, f]) => f.values.length > 0)
        );
        onApply(cleaned);
        onOpenChange(false);
    };

    const handleCancel = () => {
        setDraft(value);
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : handleCancel())}>
            <DialogContent
                className="max-w-4xl min-w-[700px] p-0 gap-0"
                onEscapeKeyDown={(e) => e.preventDefault()}
                onPointerDownOutside={(e) => e.preventDefault()}
            >
                <DialogHeader className="px-5 py-4 border-b">
                    <DialogTitle>{t("filter.title")}</DialogTitle>
                </DialogHeader>

                <div ref={(el) => setPortalContainer(el)} className="flex min-h-[340px]">
                    {/* Left: column list */}
                    <ScrollArea className="w-48 border-r flex-shrink-0">
                        <div className="py-1">
                            {columns.map(col => (
                                <button
                                    key={col.key}
                                    onClick={() => setSelectedCol(col.key)}
                                    className={cn(
                                        "w-full text-left px-4 py-2 text-sm transition-colors",
                                        selectedCol === col.key
                                            ? "bg-muted font-medium border-r-2 border-primary"
                                            : "hover:bg-muted/50"
                                    )}
                                >
                                    {col.label}
                                    {(draft[col.key]?.values.length ?? 0) > 0 && (
                                        <span className="ml-1 text-xs text-primary font-semibold">
                                            ({draft[col.key]?.values.length})
                                        </span>
                                    )}
                                </button>
                            ))}
                        </div>
                    </ScrollArea>

                    {/* Right: condition editor */}
                    <div className="flex-1 p-5 flex flex-col gap-4">
                        {selectedCol ? (
                            <>
                                <p className="text-sm font-medium">
                                    {t("filter.where")} <strong>{columns.find(c => c.key === selectedCol)?.label}</strong>
                                </p>

                                <div className="flex items-start gap-2">
                                    <Select
                                        value={currentCondition.operator}
                                        onValueChange={(op) =>
                                            updateDraft(selectedCol, { operator: op as FilterOperator })
                                        }
                                    >
                                        <SelectTrigger className="w-40 flex-shrink-0">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {operators.map(op => (
                                                <SelectItem key={op.value} value={op.value}>
                                                    {op.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>

                                    <Combobox
                                        key={selectedCol}
                                        multiple
                                        items={uniqueValues}
                                        itemToStringValue={(v: string) => v}
                                        value={currentCondition.values}
                                        onValueChange={(vals) =>
                                            updateDraft(selectedCol, { values: vals as string[] })
                                        }
                                    >
                                        <ComboboxChips ref={anchor} className="flex-1 min-h-9">
                                            {currentCondition.values.map(v => (
                                                <ComboboxChip key={v}>{v}</ComboboxChip>
                                            ))}
                                            <ComboboxChipsInput placeholder={t("filter.add_value_placeholder")} />
                                        </ComboboxChips>
                                        <ComboboxContent anchor={anchor} container={portalContainer}>
                                            <ComboboxList>
                                                {(v: string) => (
                                                    <ComboboxItem key={v} value={v}>{v}</ComboboxItem>
                                                )}
                                            </ComboboxList>
                                            <ComboboxEmpty>{t("filter.no_results")}</ComboboxEmpty>
                                        </ComboboxContent>
                                    </Combobox>
                                </div>
                            </>
                        ) : (
                            <p className="text-sm text-muted-foreground">{t("filter.select_column")}</p>
                        )}
                    </div>
                </div>

                <DialogFooter className="px-5 py-3 border-t">
                    <Button variant="outline" size="sm" onClick={handleCancel}>
                        {tCommon("button.cancel")}
                    </Button>
                    <Button size="sm" onClick={handleApply}>
                        {t("filter.apply")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default ColumnFilterDialog;
