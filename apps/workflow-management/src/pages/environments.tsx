// Standalone Environments / Config Variables manager route (/environments).
// SA-2605 restructures the page to THREE tabs for the section<->environment
// many-to-many model:
//   Environments    — a flat, section-independent environment list + per-env values
//                     editor. Keys are read-only (declared on the section); values
//                     editable. Membership chips are derived from the section
//                     memberships.
//   Sections        — each section's shared key schema + per-env values (the SA-2471
//                     card) PLUS a many-to-many environment membership picker PLUS
//                     the workflows bound to the section.
//   Workflow binding — a single Section <select> per workflow (no per-env subset);
//                     shows a read-only preview of the bound section's environments.
// The SA-2450 Assign UI/hooks are kept (coexistence) but no longer wired into this
// page. The header "Add" button opens Add-environment on the Environments tab and
// Add-section on the Sections tab.
import Page from "@/components/containers/page";
import EnvironmentsFlat from "@/features/environments/components/environments-flat";
import SectionsManagement from "@/features/environments/components/sections-management";
import { UserGuideDrawer } from "@/features/environments/components/user-guide-drawer";
import WorkflowSectionBind from "@/features/environments/components/workflow-section-bind";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@ldc/ui/components/tabs";
import { CircleHelp } from "lucide-react";
import { parseAsString, useQueryState } from "nuqs";
import { useState } from "react";

const ENV_TAB = { ENVIRONMENTS: "environments", SECTIONS: "sections", BINDING: "binding" } as const;

const EnvironmentsPage = () => {
    const { t } = useLanguage();
    const [addSectionOpen, setAddSectionOpen] = useState(false);
    const [guideOpen, setGuideOpen] = useState(false);
    const [activeTab, setActiveTab] = useQueryState("tab", parseAsString.withDefault(ENV_TAB.ENVIRONMENTS));

    return (
        <Page>
            {/* SA-2605: the Add-section primary lives in the Sections tab's own
                header row (mockup .cfghead), not in the page top bar. */}
            <Page.Header
                title={t("environments.title")}
                description={t("environments.description")}
                actions={
                    <div className="pr-4">
                        {/* SA-2617: plain-English user guide for admins. */}
                        <Button variant="outline" size="sm" onClick={() => setGuideOpen(true)}>
                            <CircleHelp className="size-4" />
                            {t("environments.user_guide")}
                        </Button>
                    </div>
                }
            />
            <UserGuideDrawer open={guideOpen} onOpenChange={setGuideOpen} />
            {/* flex-1 + min-h-0 so the tab body claims the remaining page height;
                each TabsContent then scrolls its own overflow. */}
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col gap-0 flex-1 min-h-0">
                <TabsList variant="line" className="px-4 border-b border-b-border justify-start w-full shrink-0">
                    <TabsTrigger value={ENV_TAB.ENVIRONMENTS} className="flex-0 cursor-pointer data-active:after:bg-primary" data-testid="environments-tab-environments">
                        {t("environments.tab_environments")}
                    </TabsTrigger>
                    <TabsTrigger value={ENV_TAB.SECTIONS} className="flex-0 cursor-pointer data-active:after:bg-primary" data-testid="environments-tab-sections">
                        {t("environments.tab_sections")}
                    </TabsTrigger>
                    <TabsTrigger value={ENV_TAB.BINDING} className="flex-0 cursor-pointer data-active:after:bg-primary" data-testid="environments-tab-binding">
                        {t("environments.tab_binding")}
                    </TabsTrigger>
                </TabsList>

                <TabsContent value={ENV_TAB.ENVIRONMENTS} className="flex-1 min-h-0 overflow-y-auto">
                    <EnvironmentsFlat />
                </TabsContent>

                <TabsContent value={ENV_TAB.SECTIONS} className="flex-1 min-h-0 overflow-y-auto">
                    <SectionsManagement addOpen={addSectionOpen} onAddOpenChange={setAddSectionOpen} />
                </TabsContent>

                <TabsContent value={ENV_TAB.BINDING} className="flex-1 min-h-0 overflow-y-auto">
                    <WorkflowSectionBind />
                </TabsContent>
            </Tabs>
        </Page>
    );
};

export default EnvironmentsPage;
