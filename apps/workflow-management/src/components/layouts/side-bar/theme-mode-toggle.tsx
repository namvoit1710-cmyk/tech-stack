import { useLanguage } from "@/hooks/use-language";
import { useTheme } from "@ldc/ui";
import { ToggleGroup, ToggleGroupItem } from "@ldc/ui/components/toggle-group";
import { Monitor, Moon, Sun } from "lucide-react";

// system | light | dark — persisted to localStorage + applied via the design-system
// ThemeProvider (key "mode-system"). "system" follows prefers-color-scheme live.
const MODES = [
    { value: "system", icon: Monitor, label: "theme_system" },
    { value: "light", icon: Sun, label: "theme_light" },
    { value: "dark", icon: Moon, label: "theme_dark" },
] as const;

export function ThemeModeToggle() {
    const { mode, setMode } = useTheme();
    const { t } = useLanguage();

    return (
        <ToggleGroup
            type="single"
            value={mode}
            // Radix emits "" when the active item is clicked again — ignore it so a mode is always set.
            onValueChange={(v) => { if (v === "system" || v === "light" || v === "dark") setMode(v); }}
            variant="outline"
            size="sm"
            className="w-full"
            aria-label={t("theme_mode")}
        >
            {MODES.map(({ value, icon: Icon, label }) => (
                <ToggleGroupItem key={value} value={value} aria-label={t(label)} title={t(label)} className="flex-1">
                    <Icon className="size-4" />
                </ToggleGroupItem>
            ))}
        </ToggleGroup>
    );
}

export default ThemeModeToggle;
