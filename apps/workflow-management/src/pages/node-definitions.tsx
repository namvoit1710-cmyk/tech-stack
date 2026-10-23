import Page from "@/components/containers/page";
import NodeDefinitionCreateModal from "@/features/node-definitions/components/list/node-definition-create-modal";
import NodeDefinitionList from "@/features/node-definitions/components/list/node-definition-list";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { PlusIcon } from "lucide-react";
import { useState } from "react";

const NodeDefinitionsPage = () => {
    const { t } = useLanguage();
    const [createOpen, setCreateOpen] = useState(false);

    return (
        <Page>
            <Page.Header
                title={t("node_definitions.tab_title")}
                actions={
                    <Button onClick={() => setCreateOpen(true)}>
                        <PlusIcon className="size-4" />
                        {t("node_definitions.create_button")}
                    </Button>
                }
            />
            <NodeDefinitionList onCreate={() => setCreateOpen(true)} />
            <NodeDefinitionCreateModal open={createOpen} onOpenChange={setCreateOpen} />
        </Page>
    );
};

export default NodeDefinitionsPage;
