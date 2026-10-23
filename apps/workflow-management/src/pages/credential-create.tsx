// Create-credential route (/credentials/create) — SA-1346 / FE-5.
// Wraps the shared CredentialAddForm (all types, no node affinity filter) in a
// page; on success it returns to the list.
import Page from "@/components/containers/page";
import { CredentialAddForm } from "@/features/workflows/components/node-config-controls/credential-add-form";
import { credentialKeys } from "@/features/workflows/hooks/apis/credentials";
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { useNavigate } from "react-router-dom";

const CredentialCreatePage = () => {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const back = () => navigate("/credentials");

  return (
    <Page>
      <Page.Header title={t("credentials_settings.create_title")} />
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-lg" data-testid="credential-create-page">
          <CredentialAddForm
            onCreated={() => {
              queryClient.invalidateQueries({ queryKey: credentialKeys.list() });
              back();
            }}
            onCancel={back}
          />
        </div>
      </div>
    </Page>
  );
};

export default CredentialCreatePage;
