import Page from "@/components/containers/page";
import { useLanguage } from "@/hooks/use-language";
import { cn, useTheme } from "@ldc/ui";
import { Monitor, Moon, Sun } from "lucide-react";

// system | light | dark — same engine as the sidebar toggle (ThemeProvider →
// localStorage key "mode-system"). This page is just a roomier picker.
const MODES = [
    { value: "system", icon: Monitor, label: "theme_system", desc: "theme_system_desc" },
    { value: "light", icon: Sun, label: "theme_light", desc: "theme_light_desc" },
    { value: "dark", icon: Moon, label: "theme_dark", desc: "theme_dark_desc" },
] as const;

const AppearanceSettings = () => {
    const { t } = useLanguage();
    const { mode, setMode } = useTheme();
    return (
        <section className="max-w-2xl flex flex-col gap-4">
            <div className="flex flex-col gap-0.5">
                <h5 className="text-sm font-medium">{t("appearance")}</h5>
                <p className="text-xs text-muted-foreground">{t("appearance_description")}</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3" role="radiogroup" aria-label={t("theme_mode")}>
                {MODES.map(({ value, icon: Icon, label, desc }) => {
                    const active = mode === value;
                    return (
                        <button
                            key={value}
                            type="button"
                            role="radio"
                            aria-checked={active}
                            onClick={() => setMode(value)}
                            className={cn(
                                "flex flex-col items-start gap-2 rounded-lg border p-4 text-left transition-colors",
                                active
                                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                                    : "border-border hover:bg-muted/50"
                            )}
                        >
                            <Icon className="size-5" />
                            <span className="text-sm font-medium">{t(label)}</span>
                            <span className="text-xs text-muted-foreground">{t(desc)}</span>
                        </button>
                    );
                })}
            </div>
        </section>
    );
};

const SettingsPage = () => {
    const { t } = useLanguage();

    // Credentials moved to their own /credentials page; Settings is Appearance-only,
    // so the tab strip is gone and Appearance renders directly.
    return (
        <Page>
            <Page.Header title={t("settings")} description={t("settings_description")} />
            <div className="flex-1 overflow-y-auto px-4 py-6">
                <AppearanceSettings />
            </div>
        </Page>
    );
};

export default SettingsPage;
