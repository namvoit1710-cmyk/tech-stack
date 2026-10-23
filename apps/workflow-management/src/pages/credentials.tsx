// Standalone Credentials manager route (/credentials) — SA-1346 / FE-5.
// Matches the Gateway / Node Definitions page shell: title + description + a header
// action button; the list body is a dedicated component. Only ids/metadata are shown —
// secrets stay in the vault.
import Page from "@/components/containers/page";
import CredentialsSettings from "@/features/settings/credentials-settings";
import { useLanguage } from "@/hooks/use-language";
import { Button } from "@ldc/ui/components/button";
import { PlusIcon } from "lucide-react";
import { useState } from "react";

const CredentialsPage = () => {
  const { t } = useLanguage();
  const [addOpen, setAddOpen] = useState(false);
  return (
    <Page>
      <Page.Header
        title={t("credentials_settings.title")}
        description={t("credentials_settings.description")}
        actions={
          <Button data-testid="credentials-add-open" onClick={() => setAddOpen(true)}>
            <PlusIcon className="size-4" /> {t("credentials_settings.add")}
          </Button>
        }
      />
      <CredentialsSettings addOpen={addOpen} onAddOpenChange={setAddOpen} />
    </Page>
  );
};

export default CredentialsPage;
