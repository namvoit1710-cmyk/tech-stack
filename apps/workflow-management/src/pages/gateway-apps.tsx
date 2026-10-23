import Page from "@/components/containers/page";
import GatewayAppList from "@/features/gateway-admin/components/apps/gateway-app-list";
import GatewayAppModal from "@/features/gateway-admin/components/apps/gateway-app-modal";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { PlusIcon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

const GatewayAppsPage = () => {
    const { t } = useLanguage();
    const navigate = useNavigate();
    const [createOpen, setCreateOpen] = useState(false);

    return (
        <Page>
            <Page.Header
                title={t("gateway.tab_title")}
                description={t("gateway.apps_subtitle")}
                actions={
                    <Button onClick={() => setCreateOpen(true)}>
                        <PlusIcon className="size-4" /> {t("gateway.create_app")}
                    </Button>
                }
            />
            <GatewayAppList />
            <GatewayAppModal open={createOpen} onOpenChange={setCreateOpen} onSaved={(name) => navigate(`/gateway/apps/${encodeURIComponent(name)}`)} />
        </Page>
    );
};

export default GatewayAppsPage;
