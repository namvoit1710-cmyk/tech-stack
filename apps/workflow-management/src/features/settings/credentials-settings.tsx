// Credentials manager list body (rendered by the /credentials page). Lists every credential
// stored in the credentials-management vault and lets the user edit/delete/test per row.
// Only ids/metadata are shown — secrets stay in the vault. The "Add credential" button and
// the page chrome live in the page's Page.Header (matching Gateway / Node Definitions); this
// component just renders the list + the (controlled) add dialog.
import {
  credentialKeys,
  useCredentials,
  useDeleteCredential,
  useTestCredential,
  useUpdateCredential,
} from "@/features/workflows/hooks/apis/credentials";
import { CredentialSetupDialog } from "@/features/workflows/components/node-config-controls/credential-setup-dialog";
import type { ICredentialSummary } from "@/features/workflows/types/credentials";
import { useLanguage } from "@/hooks/use-language";
import { isCredentialsConfigured } from "@/lib/credentials-api";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { Switch } from "@ldc/ui/components/switch";
import { useQueryClient } from "@ldc/tanstack-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-green-100 text-green-700",
  EXPIRING_SOON: "bg-amber-100 text-amber-700",
  EXPIRED: "bg-red-100 text-red-700",
  REVOKED: "bg-red-100 text-red-700",
};

interface ICredentialsSettingsProps {
  addOpen: boolean;
  onAddOpenChange: (open: boolean) => void;
}

const CredentialsSettings = ({ addOpen, onAddOpenChange }: ICredentialsSettingsProps) => {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const configured = isCredentialsConfigured();
  const { data, isLoading } = useCredentials();
  const test = useTestCredential();
  const del = useDeleteCredential();
  const updateCredential = useUpdateCredential();
  const [testingId, setTestingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [grantingId, setGrantingId] = useState<string | null>(null);

  const credentials: ICredentialSummary[] = data?.credentials ?? [];

  const runTest = (id: string) => {
    setTestingId(id);
    test.mutate(id);
  };

  // SA-2279. Sends ONLY automationAllowed: a PATCH that also carried
  // displayName/formData would re-validate and re-encrypt the secret, which is
  // not what flipping a switch should do. `version` goes out as If-Match so a
  // stale row loses to a concurrent edit instead of silently overwriting it.
  const onToggleAutomation = (credential: ICredentialSummary, next: boolean) => {
    setGrantingId(credential.id);
    updateCredential.mutate(
      {
        credentialId: credential.id,
        body: { automationAllowed: next },
        version: credential.version,
      },
      {
        onSettled: () => {
          setGrantingId(null);
          queryClient.invalidateQueries({ queryKey: credentialKeys.list() });
        },
      },
    );
  };

  const runDelete = (id: string) =>
    del.mutate(id, {
      onSettled: () => {
        setConfirmId(null);
        queryClient.invalidateQueries({ queryKey: credentialKeys.list() });
      },
    });

  if (!configured) {
    return (
      <section className="flex flex-col gap-4 px-4 pb-6" data-testid="credentials-not-configured">
        <p className="text-sm text-muted-foreground py-10 text-center">
          {t("credentials_settings.not_configured")}
        </p>
      </section>
    );
  }

  return (
    // flex-1 + min-h-0 + overflow-auto makes this the scroll region inside the
    // overflow-hidden Page shell (matches settings.tsx). Page.Header stays fixed
    // above it; long credential lists scroll instead of being clipped (SA-2578).
    <section className="flex flex-1 min-h-0 flex-col gap-4 overflow-auto px-4 pb-6">
      {isLoading && <div className="loader-bar w-full" />}

      {!isLoading && credentials.length === 0 && (
        <p className="text-sm text-muted-foreground py-10 text-center" data-testid="credentials-empty">
          {t("credentials_settings.empty")}
        </p>
      )}

      {credentials.length > 0 && (
        <div className="rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 border-b border-border bg-muted text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">{t("credentials_settings.col_name")}</th>
                <th className="px-4 py-2.5 font-medium">{t("credentials_settings.col_provider")}</th>
                <th className="px-4 py-2.5 font-medium">{t("credentials_settings.col_kind")}</th>
                <th className="px-4 py-2.5 font-medium">{t("credentials_settings.col_status")}</th>
                <th className="px-4 py-2.5 font-medium">{t("credentials_settings.col_expires")}</th>
                <th className="px-4 py-2.5 font-medium">
                  {t("credentials_settings.col_automation")}
                </th>
                <th className="px-4 py-2.5 font-medium text-right">{t("credentials_settings.col_actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border" data-testid="credentials-table-body">
              {credentials.map(c => (
                <tr key={c.id} className="hover:bg-muted/50 transition-colors">
                  <td className="px-4 py-3 font-medium">{c.displayName}</td>
                  <td className="px-4 py-3">
                    <Badge variant="outline">{c.provider}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary">{c.credentialKind}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", STATUS_TONE[c.status] ?? "bg-gray-100 text-gray-700")}>
                      {c.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {c.expiresAt ? c.expiresAt.slice(0, 10) : t("credentials_settings.never_expires")}
                  </td>
                  {/* SA-2279. Scheduled and event-triggered runs have no user
                      behind them, so the vault refuses this credential to them
                      unless its owner grants it here. Off by default. */}
                  <td className="px-4 py-3">
                    <Switch
                      data-testid={`credentials-automation-${c.id}`}
                      aria-label={t("credentials_settings.automation_aria")}
                      checked={c.automationAllowed === true}
                      disabled={grantingId === c.id || c.status === "REVOKED"}
                      onCheckedChange={next => onToggleAutomation(c, next)}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-2">
                      {testingId === c.id && test.data?.valid === true && (
                        <span className="text-xs font-medium text-green-600">{t("credentials_settings.test_ok")}</span>
                      )}
                      {testingId === c.id && test.data?.valid === false && (
                        <span className="text-xs font-medium text-red-600">{test.data.error ?? t("credentials_settings.test_fail")}</span>
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        data-testid={`credentials-test-${c.id}`}
                        disabled={test.isPending && testingId === c.id}
                        onClick={() => runTest(c.id)}
                      >
                        {test.isPending && testingId === c.id ? t("credentials_settings.testing") : t("credentials_settings.test")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        data-testid={`credentials-edit-${c.id}`}
                        onClick={() => navigate(`/credentials/${c.id}/edit`)}
                      >
                        {t("credentials_settings.edit")}
                      </Button>
                      {confirmId === c.id ? (
                        <>
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            data-testid={`credentials-delete-confirm-${c.id}`}
                            disabled={del.isPending}
                            onClick={() => runDelete(c.id)}
                          >
                            {t("credentials_settings.delete_confirm")}
                          </Button>
                          <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmId(null)}>
                            {t("credentials_settings.delete_cancel")}
                          </Button>
                        </>
                      ) : (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          data-testid={`credentials-delete-${c.id}`}
                          onClick={() => setConfirmId(c.id)}
                        >
                          {t("credentials_settings.delete")}
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <CredentialSetupDialog
        open={addOpen}
        onOpenChange={onAddOpenChange}
        title={t("credentials_settings.add_title")}
        onCreated={() => queryClient.invalidateQueries({ queryKey: credentialKeys.list() })}
      />
    </section>
  );
};

export default CredentialsSettings;
