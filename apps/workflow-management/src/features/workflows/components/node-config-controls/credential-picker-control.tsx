// Custom @ldc/autoform control for a node's reserved `credential_ref` field (T7).
// Lets a builder pick a stored credentials-management credential; the node stores ONLY
// the id (never a secret — F14). Registered on the node-detail Builder's fieldControl map;
// controlProps {provider, kinds} are spread in as flat props by the Builder.
//
// Shape (T7.2): [ dropdown of existing credentials ] [ Setup credential ] — the dropdown
// is disabled when none exist, and "Setup credential" opens a popup form. Add lives in the
// shared CredentialSetupDialog (also used by Settings → Credentials).
//
// ponytail: was a native <select> v1 (robustly testable, accessible) — but a native
// select's option list is painted by the browser with the system palette, so in dark
// mode it came up white-on-white. Now SimpleSelect, i.e. the same @ldc/ui Select every
// other builder dropdown uses. Upgrade to the Popover+Command combobox
// WorkflowPickerControl uses if credential counts grow enough to need search.
import { SimpleSelect } from "@/components/simple-select/simple-select";
import { useCredentials, useTestCredential } from "@/features/workflows/hooks/apis/credentials";
import type { ICredentialSummary } from "@/features/workflows/types/credentials";
import { filterCredentials, findCredential } from "@/features/workflows/utils/credential-filter";
import { useLanguage } from "@/hooks/use-language";
import { cn } from "@ldc/ui";
import { Button } from "@ldc/ui/components/button";
import { useState } from "react";
import { CredentialSetupDialog } from "./credential-setup-dialog";

interface ICredentialPickerControlProps {
  name?: string;
  value?: string;
  onChange?: (event: { target: { name: string; value: string } }) => void;
  provider?: string;
  kinds?: string[];
  disabled?: boolean;
  className?: string;
}

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-green-100 text-green-700",
  EXPIRING_SOON: "bg-amber-100 text-amber-700",
  EXPIRED: "bg-red-100 text-red-700",
  REVOKED: "bg-red-100 text-red-700",
};

const StatusBadge = ({ status }: { status: string }) => (
  <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", STATUS_TONE[status] ?? "bg-gray-100 text-gray-700")}>
    {status}
  </span>
);

export const CredentialPickerControl = ({
  name = "credential_ref",
  value,
  onChange,
  provider,
  kinds,
  disabled = false,
  className,
}: ICredentialPickerControlProps) => {
  const { t } = useLanguage();
  const { data, isLoading } = useCredentials();
  const test = useTestCredential();
  const [setupOpen, setSetupOpen] = useState(false);

  const all: ICredentialSummary[] = data?.credentials ?? [];
  const filtered = filterCredentials(all, { provider, kinds });
  const selected = findCredential(all, value ?? "");
  const missing = !!value && !selected;

  // Keep the selected credential selectable even if the provider/kind filter would hide it.
  const options =
    selected && !filtered.some(c => c.id === selected.id) ? [selected, ...filtered] : filtered;
  const noCredentials = options.length === 0;

  const emit = (next: string) => {
    test.reset(); // clear a stale green/red when the selection changes (no misleading feedback)
    onChange?.({ target: { name, value: next } });
  };

  return (
    <div className={cn("flex w-full flex-col gap-2", className)}>
      <div className="flex items-center gap-2">
        <SimpleSelect
          data-testid="credential-picker-select"
          className="h-10!"
          value={selected ? value : ""}
          disabled={disabled || isLoading || noCredentials}
          onValueChange={emit}
          emptyLabel={noCredentials ? t("nodes.credential.empty") : t("nodes.credential.placeholder")}
          options={options.map(c => ({ value: c.id, label: c.displayName }))}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          data-testid="credential-setup-open"
          onClick={() => setSetupOpen(true)}
        >
          {t("nodes.credential.setup")}
        </Button>
      </div>

      {selected && (
        <div data-testid="credential-summary" className="flex items-center gap-2 text-sm">
          <span className="truncate font-medium">{selected.displayName}</span>
          <span className="text-xs text-muted-foreground">{selected.credentialKind}</span>
          <StatusBadge status={selected.status} />
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!value || test.isPending}
          onClick={() => value && test.mutate(value)}
        >
          {test.isPending ? t("nodes.credential.testing") : t("nodes.credential.test")}
        </Button>
        {test.data?.valid === true && (
          <span data-testid="credential-test-ok" className="text-xs font-medium text-green-600">
            {t("nodes.credential.test_ok")}
          </span>
        )}
        {test.data?.valid === false && (
          <span data-testid="credential-test-fail" className="text-xs font-medium text-red-600">
            {test.data.error ?? t("nodes.credential.test_fail")}
          </span>
        )}
      </div>

      <CredentialSetupDialog
        open={setupOpen}
        onOpenChange={setSetupOpen}
        provider={provider}
        kinds={kinds}
        onCreated={id => emit(id)}
      />

      {missing && <p className="text-xs text-red-600">{t("nodes.credential.pick_another")}</p>}
    </div>
  );
};

CredentialPickerControl.displayName = "CredentialPickerControl";
