import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@ldc/ui/components/command";
import { Popover, PopoverContent, PopoverTrigger } from "@ldc/ui/components/popover";
import { DynamicNodeIcon } from "@ldc/workflow-editor";
import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";

interface IconPickerProps {
    value?: string;
    onChange: (name: string) => void;
    color?: string;
}

/** Curated set of common lucide icons (PascalCase — DynamicNodeIcon kebab-cases). */
const COMMON_ICONS = [
    "Box", "Search", "Globe", "Link", "Zap", "Bot", "Cpu", "Database", "Server", "Cloud",
    "Mail", "MessageSquare", "Send", "Bell", "Phone", "Calendar", "Clock", "MapPin",
    "CreditCard", "DollarSign", "ShoppingCart", "Package", "Truck", "Tag", "Gift",
    "FileText", "File", "Folder", "Image", "Video", "Music", "Download", "Upload",
    "User", "Users", "Lock", "Key", "Shield", "Eye", "Settings", "Wrench", "Cog",
    "Code", "Terminal", "GitBranch", "Webhook", "Filter", "Sparkles", "Star", "Heart",
    "CheckCircle", "AlertTriangle", "Info", "HelpCircle", "Flag", "Bookmark",
    "BarChart", "PieChart", "TrendingUp", "Activity", "Table", "List", "LayoutGrid",
];

const IconPicker = ({ value, onChange, color }: IconPickerProps) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button variant="outline" className="w-full justify-between font-normal" aria-label={t("node_definition_builder.info_icon")}>
                    <span className="flex items-center gap-2">
                        <span
                            className="flex items-center justify-center size-6 rounded"
                            style={{
                                color: color || "#3b82f6",
                                backgroundColor: `color-mix(in srgb, ${color || "#3b82f6"} 12%, transparent)`,
                            }}
                        >
                            <DynamicNodeIcon name={value || "box"} fallbackIconName="box" className="size-4" />
                        </span>
                        <span className="text-sm">{value || t("node_definition_builder.icon_pick")}</span>
                    </span>
                    <ChevronDownIcon className="size-4 text-muted-foreground" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-72 p-0" align="start">
                <Command>
                    <CommandInput placeholder={t("node_definition_builder.icon_search")} />
                    <CommandList>
                        <CommandEmpty>{t("node_definition_builder.icon_none")}</CommandEmpty>
                        <CommandGroup>
                            <div className="grid grid-cols-6 gap-1 p-1">
                                {COMMON_ICONS.map((name) => (
                                    <CommandItem
                                        key={name}
                                        value={name}
                                        onSelect={() => {
                                            onChange(name);
                                            setOpen(false);
                                        }}
                                        className={cn(
                                            "flex items-center justify-center aspect-square rounded cursor-pointer",
                                            value === name && "bg-primary/10 ring-1 ring-primary/40"
                                        )}
                                        title={name}
                                    >
                                        <DynamicNodeIcon name={name} fallbackIconName="box" className="size-4" />
                                    </CommandItem>
                                ))}
                            </div>
                        </CommandGroup>
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
    );
};

export default IconPicker;
