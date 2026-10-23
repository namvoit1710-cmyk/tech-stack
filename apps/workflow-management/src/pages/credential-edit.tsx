// Rebuild marker: deploy edit-prefill (merge-commit first-parent rebuild gap).
// Edit-credential route (/credentials/:id/edit) — SA-1346 / FE-5, SA-1342 / FE-1.
// Renders the credential type's form_schema PREFILLED with the stored non-secret values
// (host/port/username/…) fetched from GET /credentials/:id, so a user sees and can change
// what they entered. Secrets are never returned by the vault (per-adapter allowlist) — the
// field stays blank and must be re-entered to save (PATCH re-runs the adapter's
// validate+acquire). Rename / rotate / delete stay available. OAuth-redirect types
// (formSchema == null) or types with no editable fields fall back to a name-only edit.
import Page from "@/components/containers/page";
import {
  credentialKeys,
  useCredentialFormValues,
  useCredentialTypes,
  useDeleteCredential,
  useRotateCredentialKey,
  useUpdateCredential,
} from "@/features/workflows/hooks/apis/credentials";
import type { ICredentialTypeDefinition } from "@/features/workflows/types/credentials";
import {
  buildDefaultValues,
  buildSubmitMapping,
  flattenForSubmit,
  stripActionButtons,
} from "@/features/workflows/utils/credential-form-mapping";
import { encryptFormData, isTransportEncryptionAvailable } from "@/lib/credential-encryption";
import { useLanguage } from "@/hooks/use-language";
import { Builder } from "@ldc/autoform";
import { Button } from "@ldc/ui/components/button";
import { useQueryClient } from "@ldc/tanstack-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

const CredentialEditPage = () => {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id = "" } = useParams();

  const { data: formValues, isLoading } = useCredentialFormValues(id);
  const { data: typesData } = useCredentialTypes();
  const update = useUpdateCredential();
  const rotate = useRotateCredentialKey();
  const del = useDeleteCredential();

  const [builderValues, setBuilderValues] = useState<Record<string, unknown>>({});
  const [formValid, setFormValid] = useState(false);

  const credType: ICredentialTypeDefinition | undefined = (typesData?.types ?? []).find(
    tp => tp.typeId === formValues?.typeId,
  );
  const schema = credType?.formSchema ?? null;
  const mapping = useMemo(() => buildSubmitMapping(schema), [schema]);
  // Render without the schema's dead Test/Save buttons — this page has its own Save below.
  const formSchema = useMemo(() => stripActionButtons(schema), [schema]);

  // Prefill the Builder: nest the stored non-secret values (+ displayName) into the
  // schema's value shape. Secret fields are absent from the vault response → stay blank.
  const defaultValues = useMemo(() => {
    if (!mapping || !formValues) return undefined;
    return buildDefaultValues(mapping, formValues.values ?? {}, formValues.displayName);
  }, [mapping, formValues]);

  const back = () => navigate("/credentials");
  const refresh = () => queryClient.invalidateQueries({ queryKey: credentialKeys.list() });

  const onSave = async () => {
    if (!mapping) return;
    const { displayName, formData } = flattenForSubmit(builderValues, mapping);
    // Encrypt the secret-bearing formData to the vault's transport key when available;
    // fall back to plaintext over TLS (the vault still encrypts at rest).
    let body: Record<string, unknown> = { displayName, formData };
    if (isTransportEncryptionAvailable()) {
      try {
        body = { displayName, encryptedFormData: await encryptFormData(formData) };
      } catch {
        body = { displayName, formData };
      }
    }
    update.mutate(
      { credentialId: id, body, version: formValues?.version },
      { onSuccess: () => { refresh(); back(); } },
    );
  };

  // Three different things can go wrong on save and they need three different
  // answers: reload (someone else moved the row), rename (a sibling of this type
  // already owns the name — possible only since DISPLAY_NAME joined the natural
  // key), or plain failure. Showing "reload and try again" for all of them sends
  // the user to re-do the edit that was never going to save.
  const errorCode = (err: unknown): string | undefined =>
    (err as { response?: { data?: { error?: { code?: string } } } } | null)?.response?.data?.error
      ?.code;

  const saveErrorKey = () => {
    const code = errorCode(update.error);
    if (code === "DUPLICATE_CREDENTIAL_NAME") return "credentials_settings.edit_duplicate_name";
    if (code === "VERSION_CONFLICT") return "credentials_settings.edit_conflict";
    return "credentials_settings.edit_failed";
  };

  const onRotate = () => rotate.mutate(id, { onSuccess: refresh });
  const onDelete = () => del.mutate(id, { onSuccess: () => { refresh(); back(); } });

  return (
    <Page>
      <Page.Header title={t("credentials_settings.edit_title")} />
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <section className="max-w-lg flex flex-col gap-4" data-testid="credential-edit-page">
          {isLoading && <p className="text-sm text-muted-foreground">{t("credentials_settings.loading")}</p>}

          {!isLoading && !formValues && (
            <p className="text-sm text-muted-foreground" data-testid="credential-edit-missing">
              {t("credentials_settings.edit_missing")}
            </p>
          )}

          {formValues && (
            <>
              <p className="text-xs text-muted-foreground">
                {formValues.provider} · {formValues.credentialKind} · v{formValues.version ?? 1}
              </p>

              {schema && mapping ? (
                <>
                  <h3 className="text-sm font-medium">{t("credentials_settings.edit_fields_title")}</h3>
                  <p className="text-xs text-muted-foreground" data-testid="credential-edit-secret-hint">
                    {t("credentials_settings.edit_secret_hint")}
                  </p>
                  <div data-testid="credential-edit-form">
                    <Builder
                      schema={formSchema ?? schema}
                      defaultValues={defaultValues}
                      onValuesChange={(v: unknown) =>
                        setBuilderValues((v as Record<string, unknown>) ?? {})
                      }
                      onValidChange={setFormValid}
                    />
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground" data-testid="credential-edit-no-fields">
                  {t("credentials_settings.edit_no_fields")}
                </p>
              )}

              {update.isError && (
                <p className="text-xs font-medium text-red-600" data-testid="credential-edit-error">
                  {t(saveErrorKey())}
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {schema && mapping && (
                  <Button
                    type="button"
                    size="sm"
                    data-testid="credential-edit-save"
                    disabled={!formValid || update.isPending}
                    onClick={onSave}
                  >
                    {update.isPending ? t("credentials_settings.saving") : t("credentials_settings.save")}
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="credential-edit-rotate"
                  disabled={rotate.isPending}
                  onClick={onRotate}
                >
                  {rotate.isPending ? t("credentials_settings.rotating") : t("credentials_settings.rotate")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  data-testid="credential-edit-delete"
                  disabled={del.isPending}
                  onClick={onDelete}
                >
                  {t("credentials_settings.delete")}
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={back}>
                  {t("credentials_settings.delete_cancel")}
                </Button>
              </div>

              {rotate.isSuccess && (
                <p className="text-xs font-medium text-green-600" data-testid="credential-edit-rotated">
                  {t("credentials_settings.rotated_ok")}
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </Page>
  );
};

export default CredentialEditPage;
