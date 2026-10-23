import { useTranslation } from "@ldc/i18n";
import { SearchInput } from "@ldc/ui/blocks/search-input/search-input";
import { Button } from "@ldc/ui/components/button";
import { Checkbox } from "@ldc/ui/components/checkbox";
import { Label } from "@ldc/ui/components/label";
import { Popover, PopoverContent, PopoverTrigger } from "@ldc/ui/components/popover";
import { RadioGroup, RadioGroupItem } from "@ldc/ui/components/radio-group";
import { ScrollArea } from "@ldc/ui/components/scroll-area";
import { useDebounceValue } from "@ldc/ui/hooks/use-debounce-value";
import { ChevronDown, ListIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

interface ColumnVisibleDropdownProps {
    children: ReactNode;
    value: Record<string, boolean>;
    columns: { label: string; key: string; hidden?: boolean }[];
    onChange: (updatedColumns: Record<string, boolean>) => void;
}

const ColumnVisibleDropdown = ({ children, columns, value, onChange }: ColumnVisibleDropdownProps) => {
    const { t } = useTranslation("autoform");
    const { t: tCommon } = useTranslation("common");

    const [open, setOpen] = useState(false);
    const [selectedColumns, setSelectedColumns] = useState(value);

    useEffect(() => {
        setSelectedColumns(value);
    }, [value]);

    const handleCheckedChange = (key: string, checked: boolean) => {
        const newSelectedColumns = { ...selectedColumns, [key]: checked };
        setSelectedColumns(newSelectedColumns);
    }

    const handleApplyColumnVisibility = () => {
        onChange(selectedColumns);
        setOpen(false);
    }

    const handleSelectAll = () => {
        setSelectedColumns(prev => {
            const updated = { ...prev };
            filteredColumns.forEach(col => {
                if (!col.hidden) updated[col.key] = true;
            });
            return updated;
        });
    }

    const handleDeselectAll = () => {
        setSelectedColumns(prev => {
            const updated = { ...prev };
            filteredColumns.forEach(col => {
                if (!col.hidden) updated[col.key] = false;
            });
            return updated;
        });
    }

    const [searchColumnString, setSearchColumnString] = useState("");
    const [debouncedSearchColumnString] = useDebounceValue(searchColumnString, 300);

    const filteredColumns = columns.filter((col) => col.label.toLowerCase().includes(debouncedSearchColumnString.toLowerCase()));

    const visibleCount = Object.values(selectedColumns).filter(Boolean).length;

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button variant="outline">
                    <ListIcon />
                    <span>{t("table.field_list", { count: visibleCount, total: columns.length })}</span>

                    <ChevronDown className="ml-1 size-4" />
                </Button>
            </PopoverTrigger>

            <PopoverContent align="end" className="min-w-64 w-80 flex flex-col gap-3 p-4">
                <div className="flex flex-col gap-1 w-full">
                    <RadioGroup
                        defaultValue="select_all"
                        className="grid grid-cols-2 gap-2 w-full"
                        onValueChange={(value) => {
                            if (value === "select_all") {
                                handleSelectAll();
                            } else {
                                handleDeselectAll();
                            }
                        }}
                    >
                        <div className="flex items-center gap-3">
                            <RadioGroupItem value="select_all" id="select_all" />
                            <Label className="font-normal" htmlFor="select_all">{t("table.select_all")}</Label>
                        </div>

                        <div className="flex items-center gap-3">
                            <RadioGroupItem value="deselect_all" id="deselect_all" />
                            <Label className="font-normal" htmlFor="deselect_all">{t("table.deselect_all")}</Label>
                        </div>
                    </RadioGroup>
                </div>

                <SearchInput
                    placeholder={t("table.search_column_placeholder")}
                    value={searchColumnString}
                    onChange={(e) => setSearchColumnString(e.target.value)}
                />

                <ScrollArea className="h-[240px] w-full">
                    <div className="flex flex-col gap-2 pr-4">
                        {!filteredColumns.length && (
                            <div className="text-sm text-muted-foreground text-center py-2">{t("table.no_columns")}</div>
                        )}

                        {!!filteredColumns?.length && filteredColumns.map((column) => (
                            <div key={column.key} className="flex items-center space-x-2">
                                <Checkbox
                                    id={`column-${column.key}`}
                                    checked={selectedColumns[column.key] ?? true}
                                    disabled={column.hidden === true}
                                    onCheckedChange={(checked: boolean) => handleCheckedChange(column.key, checked)}
                                />
                                <Label
                                    htmlFor={`column-${column.key}`}
                                    className="text-sm font-normal cursor-pointer flex-1 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed"
                                    aria-disabled={column.hidden === true}
                                >
                                    {column.label}
                                </Label>
                            </div>
                        ))}
                    </div>
                </ScrollArea>

                <div className="flex items-center justify-end gap-2 pt-2 border-t">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setOpen(false)}
                    >
                        {tCommon("button.cancel")}
                    </Button>
                    <Button
                        size="sm"
                        onClick={handleApplyColumnVisibility}
                    >
                        {tCommon("button.apply")}
                    </Button>
                </div>
            </PopoverContent>
        </Popover>
    )
}

export default ColumnVisibleDropdown;