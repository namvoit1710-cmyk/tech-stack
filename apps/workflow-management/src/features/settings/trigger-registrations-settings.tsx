// Trigger registrations list body (rendered by the /triggers page, SA-2059 B9).
// Lists inbound webhook registrations (one per source), shows their recent
// deliveries, and lets the user delete a row. Secrets are never shown — only a
// has-secret flag. The "New registration" button lives in the page header.
import RegistrationDialog from "@/features/trigger-registrations/components/registration-dialog";
import {
  ITriggerRegistration,
  triggerRegistrationKeys,
  useDeleteTriggerRegistration,
  useTriggerRegistrationDeliveries,
  useTriggerRegistrationDelivery,
  useTriggerRegistrations,
} from "@/features/trigger-registrations/hooks/trigger-registrations";
import { cn } from "@ldc/ui";
import { Badge } from "@ldc/ui/components/badge";
import { Button } from "@ldc/ui/components/button";
import { useQueryClient } from "@ldc/tanstack-query";
import { Fragment, useState } from "react";

const OUTCOME_TONE: Record<string, string> = {
  published: "bg-green-100 text-green-700",
  dropped_loop: "bg-amber-100 text-amber-700",
  filtered: "bg-amber-100 text-amber-700",
  rejected: "bg-red-100 text-red-700",
  error: "bg-red-100 text-red-700",
};

const PAGE_SIZE = 20;

// Pretty-print JSON payloads; fall back to the raw text for non-JSON bodies.
const prettyJson = (raw: string) => {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
};

// The raw payload is fetched only when a row is expanded (the list omits it).
const DeliveryPayload = ({ registrationId, deliveryId }: { registrationId: string; deliveryId: string }) => {
  const { data, isLoading } = useTriggerRegistrationDelivery(registrationId, deliveryId);

  if (isLoading) return <div className="loader-bar w-full" />;

  const payload = data?.payload ?? "";
  return (
    <pre
      className="max-h-80 overflow-auto rounded bg-background p-2 font-mono text-[11px] whitespace-pre-wrap break-all"
      data-testid={`delivery-payload-${deliveryId}`}
    >
      {payload ? prettyJson(payload) : "(no payload captured)"}
    </pre>
  );
};

const Deliveries = ({ registrationId }: { registrationId: string }) => {
  const { data, isLoading, isFetching, refetch } = useTriggerRegistrationDeliveries(registrationId);
  const deliveries = data ?? [];
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);

  if (isLoading) return <div className="loader-bar w-full" />;

  const pageCount = Math.ceil(deliveries.length / PAGE_SIZE);
  const safePage = Math.min(page, Math.max(pageCount - 1, 0));
  const rows = deliveries.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  return (
    <div>
      <div className="flex items-center justify-between px-4 py-2 text-xs text-muted-foreground">
        <span>{deliveries.length} {deliveries.length === 1 ? "delivery" : "deliveries"}</span>
        <Button type="button" variant="outline" size="sm"
          data-testid={`deliveries-refresh-${registrationId}`}
          disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {deliveries.length === 0 ? (
        <p className="text-xs text-muted-foreground px-4 py-2" data-testid="deliveries-empty">No deliveries yet.</p>
      ) : (
      <>
      <table className="w-full text-xs" data-testid="deliveries-table">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="px-4 py-1.5 font-medium">Received</th>
            <th className="px-4 py-1.5 font-medium">Issue</th>
            <th className="px-4 py-1.5 font-medium">Event</th>
            <th className="px-4 py-1.5 font-medium">Outcome</th>
            <th className="px-4 py-1.5 font-medium">Topic</th>
            <th className="px-4 py-1.5 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map(d => (
            <Fragment key={d.id}>
              <tr
                className="cursor-pointer hover:bg-muted/50"
                data-testid={`delivery-row-${d.id}`}
                onClick={() => setOpenId(openId === d.id ? null : d.id)}
              >
                <td className="px-4 py-1.5 text-muted-foreground">{d.received_at.slice(0, 19).replace("T", " ")}</td>
                <td className="px-4 py-1.5 font-mono" data-testid={`delivery-issue-${d.id}`}>{d.issue_key || "—"}</td>
                <td className="px-4 py-1.5 text-muted-foreground" data-testid={`delivery-event-${d.id}`}>{d.webhook_event || "—"}</td>
                <td className="px-4 py-1.5">
                  <span className={cn("rounded px-1.5 py-0.5 font-medium", OUTCOME_TONE[d.outcome] ?? "bg-gray-100 text-gray-700")}>
                    {d.outcome}
                  </span>
                </td>
                <td className="px-4 py-1.5">{d.event_topic || "—"}</td>
                <td className="px-4 py-1.5 text-muted-foreground">{d.status_code}</td>
              </tr>
              {openId === d.id && (
                <tr className="bg-muted/30">
                  <td colSpan={6} className="px-4 py-2">
                    {d.detail && <p className="mb-1 text-muted-foreground">{d.detail}</p>}
                    <DeliveryPayload registrationId={registrationId} deliveryId={d.id} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>

      {pageCount > 1 && (
        <div className="flex items-center justify-end gap-3 px-4 py-2 text-xs text-muted-foreground">
          <span data-testid="deliveries-page-info">Page {safePage + 1} of {pageCount}</span>
          <Button type="button" variant="outline" size="sm" data-testid="deliveries-prev"
            disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
            Prev
          </Button>
          <Button type="button" variant="outline" size="sm" data-testid="deliveries-next"
            disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>
            Next
          </Button>
        </div>
      )}
      </>
      )}
    </div>
  );
};

interface Props {
  addOpen: boolean;
  onAddOpenChange: (open: boolean) => void;
}

const TriggerRegistrationsSettings = ({ addOpen, onAddOpenChange }: Props) => {
  const queryClient = useQueryClient();
  const { data, isLoading } = useTriggerRegistrations();
  const del = useDeleteTriggerRegistration();
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editReg, setEditReg] = useState<ITriggerRegistration | null>(null);

  const registrations: ITriggerRegistration[] = data ?? [];

  const runDelete = (id: string) =>
    del.mutate(id, {
      onSettled: () => {
        setConfirmId(null);
        queryClient.invalidateQueries({ queryKey: triggerRegistrationKeys.list() });
      },
    });

  return (
    <section className="flex flex-col gap-4 px-4 pb-6">
      {isLoading && <div className="loader-bar w-full" />}

      {!isLoading && registrations.length === 0 && (
        <p className="text-sm text-muted-foreground py-10 text-center" data-testid="registrations-empty">
          No trigger registrations yet. Create one per source system (Jira / GitHub / Stripe).
        </p>
      )}

      {registrations.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">Name</th>
                <th className="px-4 py-2.5 font-medium">Source</th>
                <th className="px-4 py-2.5 font-medium">Path</th>
                <th className="px-4 py-2.5 font-medium">Emit topic</th>
                <th className="px-4 py-2.5 font-medium">Secret</th>
                <th className="px-4 py-2.5 font-medium">Enabled</th>
                <th className="px-4 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border" data-testid="registrations-table-body">
              {registrations.map(r => (
                <Fragment key={r.id}>
                  <tr className="hover:bg-muted/50 transition-colors">
                    <td className="px-4 py-3 font-medium">{r.name}</td>
                    <td className="px-4 py-3"><Badge variant="outline">{r.source}</Badge></td>
                    <td className="px-4 py-3 font-mono text-xs">{r.path}</td>
                    <td className="px-4 py-3"><Badge variant="secondary">{r.emit_topic}</Badge></td>
                    <td className="px-4 py-3">{r.has_secret ? "✓" : "—"}</td>
                    <td className="px-4 py-3">{r.enabled ? "✓" : "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        <Button type="button" variant="outline" size="sm"
                          data-testid={`registration-deliveries-${r.id}`}
                          onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}>
                          {expandedId === r.id ? "Hide deliveries" : "Deliveries"}
                        </Button>
                        <Button type="button" variant="ghost" size="sm"
                          data-testid={`registration-edit-${r.id}`}
                          onClick={() => setEditReg(r)}>
                          Edit
                        </Button>
                        {confirmId === r.id ? (
                          <>
                            <Button type="button" variant="destructive" size="sm"
                              data-testid={`registration-delete-confirm-${r.id}`}
                              disabled={del.isPending} onClick={() => runDelete(r.id)}>
                              Confirm
                            </Button>
                            <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmId(null)}>
                              Cancel
                            </Button>
                          </>
                        ) : (
                          <Button type="button" variant="ghost" size="sm"
                            data-testid={`registration-delete-${r.id}`}
                            onClick={() => setConfirmId(r.id)}>
                            Delete
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {expandedId === r.id && (
                    <tr className="bg-muted/20">
                      <td colSpan={7} className="p-0">
                        <Deliveries registrationId={r.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <RegistrationDialog open={addOpen} onOpenChange={onAddOpenChange} />
      <RegistrationDialog
        open={!!editReg}
        onOpenChange={open => !open && setEditReg(null)}
        registration={editReg}
      />
    </section>
  );
};

export default TriggerRegistrationsSettings;
