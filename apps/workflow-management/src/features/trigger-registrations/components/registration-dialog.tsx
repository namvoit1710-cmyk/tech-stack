// Create/edit registration dialog (SA-2059 B9). One registration per SOURCE:
// path + source + secret/HMAC + IP allowlist + "emit as event topic". The URL it
// defines fans out to N workflows via the topic — never bound 1:1 to a workflow.
// Pass `registration` to edit (PATCH); omit it to create (POST).
import { useLanguage } from "@/hooks/use-language";
import { useQueryClient } from "@ldc/tanstack-query";
import { toast } from "@ldc/ui/blocks/toast/toast";
import { Button } from "@ldc/ui/components/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@ldc/ui/components/dialog";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Spinner } from "@ldc/ui/components/spinner";
import { Switch } from "@ldc/ui/components/switch";
import { useEffect, useId, useState } from "react";
import {
  ITriggerRegistration,
  triggerRegistrationKeys,
  useCreateTriggerRegistration,
  useUpdateTriggerRegistration,
} from "../hooks/trigger-registrations";

const SOURCES = ["jira", "github", "stripe", "generic"];

// ponytail: browser crypto beats a lib for a random secret. 32 hex chars = 128 bits.
const genSecret = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");

// Accept comma / newline / space separated CIDRs or IPs.
const parseIps = (raw: string) => raw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Present = edit mode (PATCH); absent = create mode (POST).
  registration?: ITriggerRegistration | null;
}

const RegistrationDialog = ({ open, onOpenChange, registration }: Props) => {
  const { t } = useLanguage();
  const uid = useId();
  const queryClient = useQueryClient();
  const isEdit = !!registration;

  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [source, setSource] = useState("jira");
  const [emitTopic, setEmitTopic] = useState("");
  const [secret, setSecret] = useState("");
  const [hmacSecret, setHmacSecret] = useState("");
  const [ipAllowlist, setIpAllowlist] = useState("");
  const [integrationActor, setIntegrationActor] = useState("");
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    if (!open) return;
    // Prefill from the row in edit mode; secrets stay blank (write-only, never returned).
    setName(registration?.name ?? "");
    setPath(registration?.path ?? "");
    setSource(registration?.source ?? "jira");
    setEmitTopic(registration?.emit_topic ?? "");
    setSecret("");
    setHmacSecret("");
    setIpAllowlist((registration?.ip_allowlist ?? []).join(", "));
    setIntegrationActor(registration?.integration_actor ?? "");
    setEnabled(registration?.enabled ?? true);
  }, [open, registration]);

  const { mutate: create, isPending: creating } = useCreateTriggerRegistration();
  const { mutate: update, isPending: updating } = useUpdateTriggerRegistration();
  const isPending = creating || updating;

  // F7 (review): a registration with no secret AND no HMAC is an open ingress — _verify
  // skips all checks, so anyone who knows the path can publish. Require at least one
  // verification method. In edit an already-stored secret/hmac counts (blank = keep).
  // ponytail: FE gate only; the backend must also refuse open registrations (F7 backend).
  const hasVerification =
    !!secret.trim() || !!hmacSecret.trim() ||
    (isEdit && (!!registration?.has_secret || !!registration?.has_hmac_secret));
  const canSave =
    !!name.trim() && !!emitTopic.trim() && (isEdit || !!path.trim()) && hasVerification;

  const submit = () => {
    if (!canSave) return;
    if (isEdit) {
      update(
        {
          id: registration!.id,
          body: {
            name: name.trim(),
            source,
            emit_topic: emitTopic.trim(),
            // blank secret/hmac = keep existing; a value rotates it.
            secret: secret.trim() || undefined,
            hmac_secret: hmacSecret.trim() || undefined,
            ip_allowlist: parseIps(ipAllowlist),
            integration_actor: integrationActor.trim() || undefined,
            enabled,
          },
        },
        {
          onSuccess: () => {
            toast.success(t("notification.success"), "Registration updated.");
            queryClient.invalidateQueries({ queryKey: triggerRegistrationKeys.list() });
            onOpenChange(false);
          },
          onError: (e: any) =>
            toast.error(t("notification.error"), e?.response?.data?.error?.message || "Update failed."),
        },
      );
      return;
    }
    create(
      {
        name: name.trim(),
        path: path.trim().startsWith("/") ? path.trim() : `/${path.trim()}`,
        source,
        emit_topic: emitTopic.trim(),
        secret: secret.trim() || undefined,
        hmac_secret: hmacSecret.trim() || undefined,
        ip_allowlist: parseIps(ipAllowlist),
        integration_actor: integrationActor.trim() || undefined,
        enabled,
      },
      {
        onSuccess: () => {
          toast.success(t("notification.success"), "Registration created.");
          queryClient.invalidateQueries({ queryKey: triggerRegistrationKeys.list() });
          onOpenChange(false);
        },
        onError: (e: any) =>
          toast.error(t("notification.error"), e?.response?.data?.error?.message || "Create failed."),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto" data-testid="registration-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit trigger registration" : "New trigger registration"}</DialogTitle>
          <DialogDescription>
            One registration per source system. Paste the generated URL + secret into the provider
            (e.g. Jira Automation "Send web request"); workflows subscribe to the same topic.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-1">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-name`}>Name <span className="text-destructive">*</span></Label>
            <Input id={`${uid}-name`} data-testid="registration-name" value={name}
              onChange={e => setName(e.target.value)} placeholder="e.g. jira-mdg" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Source</Label>
            <Select value={source} onValueChange={setSource}>
              <SelectTrigger className="w-full" data-testid="registration-source"><SelectValue /></SelectTrigger>
              <SelectContent>
                {SOURCES.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-path`}>Ingress path <span className="text-destructive">*</span></Label>
            <Input id={`${uid}-path`} data-testid="registration-path" value={path}
              onChange={e => setPath(e.target.value)} placeholder="/hooks/jira"
              disabled={isEdit} readOnly={isEdit} />
            <span className="text-xs text-muted-foreground">
              {isEdit ? "Path is fixed once registered." : `The URL is POST /webhooks${path || "/hooks/jira"}`}
            </span>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-topic`}>Emit as event topic <span className="text-destructive">*</span></Label>
            <Input id={`${uid}-topic`} data-testid="registration-emit-topic" value={emitTopic}
              onChange={e => setEmitTopic(e.target.value)} placeholder="mdg.part.created" />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-secret`}>Shared secret</Label>
            <div className="flex gap-2">
              <Input id={`${uid}-secret`} data-testid="registration-secret" type="text" value={secret}
                onChange={e => setSecret(e.target.value)} autoComplete="off" className="font-mono"
                placeholder={isEdit && registration?.has_secret ? "•••••• (leave blank to keep)" : "echoed by the provider in X-Webhook-Secret"} />
              <Button type="button" variant="outline" size="sm" data-testid="registration-secret-generate"
                onClick={() => setSecret(genSecret())}>
                {isEdit ? "Regenerate" : "Generate"}
              </Button>
            </div>
            {isEdit && (
              <span className="text-xs text-muted-foreground">
                Secret is write-only — leave blank to keep the current one, or set/regenerate to rotate it.
              </span>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-hmac`}>HMAC secret (optional)</Label>
            <Input id={`${uid}-hmac`} data-testid="registration-hmac-secret" type="text" value={hmacSecret}
              onChange={e => setHmacSecret(e.target.value)} autoComplete="off" className="font-mono"
              placeholder={isEdit && registration?.has_hmac_secret ? "•••••• (leave blank to keep)" : "verifies X-Hub-Signature-256 over the raw body"} />
            <span className="text-xs text-muted-foreground">
              Provide a shared secret and/or an HMAC secret — a registration with neither is an open ingress.
            </span>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${uid}-ips`}>IP allowlist (optional)</Label>
            <Input id={`${uid}-ips`} data-testid="registration-ip-allowlist" value={ipAllowlist}
              onChange={e => setIpAllowlist(e.target.value)}
              placeholder="e.g. 185.166.140.0/22, 18.136.0.0/16" />
            <span className="text-xs text-muted-foreground">
              Comma/space separated CIDRs or IPs. Leave blank to allow any source IP.
            </span>
          </div>

          {source === "jira" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${uid}-actor`}>Integration actor (loop guard)</Label>
              <Input id={`${uid}-actor`} data-testid="registration-actor" value={integrationActor}
                onChange={e => setIntegrationActor(e.target.value)} placeholder="accountId of the CP's own Jira user" />
              <span className="text-xs text-muted-foreground">Events from this actor are dropped, so the CP's own writes don't re-trigger.</span>
            </div>
          )}

          <label className="flex items-center gap-2 text-sm">
            <Switch checked={enabled} onCheckedChange={setEnabled} data-testid="registration-enabled" />
            Enabled
          </label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>{t("cancel")}</Button>
          <Button onClick={submit} disabled={!canSave || isPending} data-testid="registration-submit">
            {isPending && <Spinner className="size-4 mr-2" />}{t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RegistrationDialog;
