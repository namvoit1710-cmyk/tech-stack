// Environments · Config tab (SA-2471). The variable KEY schema is a property of the
// SECTION: every environment in a section shares the SAME keys+types, only the value
// differs. The page lists SECTIONS as collapsible cards; each expands to two sub-tabs
// (Variables = the shared key schema, Values = per-environment values). All create /
// delete actions use modal dialogs; the backend rejects duplicate/empty keys on Save
// and the offending key fields are highlighted (see section-card.tsx). Values are
// non-secret — a credential-type key stores only a credential_ref id per environment.
import AddSectionDialog from "@/features/environments/components/add-section-dialog";
import SectionCard from "@/features/environments/components/section-card";
import { useEnvSectionList } from "@/features/environments/hooks/apis/environments";
import { useLanguage } from "@/hooks/use-language";
import { useEffect, useState } from "react";

interface IEnvironmentsManagementProps {
    // The page header's "Add" button opens the Add-section dialog (kept as a prop
    // so the shared page tabs wrapper stays untouched — SA-2450).
    addOpen: boolean;
    onAddOpenChange: (open: boolean) => void;
}

const EnvironmentsManagement = ({ addOpen, onAddOpenChange }: IEnvironmentsManagementProps) => {
    const { t } = useLanguage();
    const { data, isLoading } = useEnvSectionList();
    const sections = data?.data?.items ?? [];

    // Per-section collapse state (UI only; does not change data). New sections
    // open expanded.
    const [openIds, setOpenIds] = useState<Set<string>>(new Set());

    // First load: expand the first section so the page isn't a wall of headers.
    useEffect(() => {
        const first = sections[0];
        if (first) {
            setOpenIds(prev => (prev.size === 0 ? new Set([first.id]) : prev));
        }
    }, [sections]);

    const setOpen = (id: string, open: boolean) =>
        setOpenIds(prev => {
            const next = new Set(prev);
            if (open) next.add(id);
            else next.delete(id);
            return next;
        });

    return (
        <section className="flex flex-col gap-4 px-4 pb-6" data-testid="environments-config">
            <p className="text-sm text-muted-foreground">{t("environments.config_intro")}</p>

            {isLoading && <div className="loader-bar w-full" />}

            {!isLoading && sections.length === 0 && (
                <p className="py-10 text-center text-sm text-muted-foreground" data-testid="environments-empty">
                    {t("environments.no_sections")}
                </p>
            )}

            <div className="flex flex-col gap-3.5">
                {sections.map(sec => (
                    <SectionCard
                        key={sec.id}
                        section={sec}
                        open={openIds.has(sec.id)}
                        onOpenChange={o => setOpen(sec.id, o)}
                    />
                ))}
            </div>

            <AddSectionDialog
                open={addOpen}
                existingNames={sections.map(s => s.section)}
                onOpenChange={onAddOpenChange}
                onCreated={id => setOpen(id, true)}
            />
        </section>
    );
};

export default EnvironmentsManagement;
