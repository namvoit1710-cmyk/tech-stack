import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { Input } from "@ldc/ui/components/input";
import { Popover, PopoverContent, PopoverTrigger } from "@ldc/ui/components/popover";
import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";

interface ColorPickerProps {
    value?: string;
    onChange: (hex: string) => void;
}

const PRESET_COLORS = [
    "#3B82F6", "#4285F4", "#6366F1", "#8B5CF6", "#A855F7", "#EC4899",
    "#EF4444", "#F97316", "#F59E0B", "#EAB308", "#10B981", "#14B8A6",
    "#06B6D4", "#0EA5E9", "#64748B", "#1F2937",
];

const ColorPicker = ({ value, onChange }: ColorPickerProps) => {
    const { t } = useLanguage();
    const [open, setOpen] = useState(false);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button variant="outline" className="w-full justify-between font-normal" aria-label={t("node_definition_builder.info_color")}>
                    <span className="flex items-center gap-2">
                        <span className="size-5 rounded border border-border" style={{ backgroundColor: value || "#3B82F6" }} />
                        <span className="text-sm font-mono">{value || "#3B82F6"}</span>
                    </span>
                    <ChevronDownIcon className="size-4 text-muted-foreground" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-60 p-3" align="start">
                <div className="grid grid-cols-8 gap-1.5 mb-3">
                    {PRESET_COLORS.map((c) => (
                        <button
                            key={c}
                            type="button"
                            aria-label={c}
                            onClick={() => { onChange(c); setOpen(false); }}
                            className={cn(
                                "size-6 rounded border transition-transform hover:scale-110",
                                value?.toUpperCase() === c ? "ring-2 ring-offset-1 ring-ring border-transparent" : "border-border"
                            )}
                            style={{ backgroundColor: c }}
                        />
                    ))}
                </div>
                <div className="flex items-center gap-2">
                    <input
                        type="color"
                        value={value || "#3B82F6"}
                        onChange={(e) => onChange(e.target.value.toUpperCase())}
                        className="size-8 rounded cursor-pointer border border-border bg-transparent p-0"
                        aria-label={t("node_definition_builder.color_custom")}
                    />
                    <Input
                        value={value || ""}
                        onChange={(e) => onChange(e.target.value)}
                        placeholder="#4285F4"
                        className="h-8 text-sm font-mono"
                    />
                </div>
            </PopoverContent>
        </Popover>
    );
};

export default ColorPicker;
