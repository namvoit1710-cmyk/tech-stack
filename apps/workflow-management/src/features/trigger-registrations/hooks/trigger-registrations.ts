// React-query hooks for inbound webhook trigger registrations (SA-2059 B9).
// Talks to the workflow control plane (/api/v1/trigger-registrations). A
// registration = path + source + secret + emit_topic; its URL fans out to N
// workflows via the topic. Secrets are write-only — the list never returns them
// (only has_secret / has_hmac_secret flags).
import api from "@/lib/api";
import { queryKeyFactory, useMutation, useQuery } from "@ldc/tanstack-query";

export interface ITriggerRegistration {
  id: string;
  name: string;
  path: string;
  source: string;
  emit_topic: string;
  ip_allowlist: string[];
  integration_actor: string;
  tenant_id: string;
  enabled: boolean;
  has_secret: boolean;
  has_hmac_secret: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface IWebhookDelivery {
  id: string;
  registration_id: string;
  received_at: string;
  outcome: string;
  status_code: number;
  event_topic: string;
  event_id: string;
  detail: string;
  // Derived server-side from the payload for the list view (see
  // _delivery_summary): the Jira webhookEvent (e.g. "jira:issue_updated") and
  // issue key (e.g. "SA-1234"). Empty string for non-Jira / non-JSON bodies.
  webhook_event?: string;
  issue_key?: string;
  // Present only on the single-delivery detail fetch; the list omits it.
  payload?: string;
}

export interface ICreateRegistrationBody {
  name: string;
  path: string;
  source: string;
  secret?: string;
  hmac_secret?: string;
  emit_topic: string;
  ip_allowlist?: string[];
  integration_actor?: string;
  tenant_id?: string;
  enabled?: boolean;
}

interface IEnvelope<T> {
  status: string;
  data: T;
  total?: number;
}

const _keys = queryKeyFactory("trigger-registrations");

export const triggerRegistrationKeys = {
  ..._keys,
  list: () => [..._keys.lists(), "registrations"] as const,
  deliveries: (id: string) => [..._keys.details(), id, "deliveries"] as const,
  delivery: (id: string, deliveryId: string) =>
    [..._keys.details(), id, "deliveries", deliveryId] as const,
};

export const useTriggerRegistrations = () =>
  useQuery({
    queryKey: triggerRegistrationKeys.list(),
    queryFn: () =>
      api.get<IEnvelope<ITriggerRegistration[]>>("/trigger-registrations").then(r => r.data),
  });

export const useTriggerRegistrationDeliveries = (id: string) =>
  useQuery({
    queryKey: triggerRegistrationKeys.deliveries(id),
    enabled: Boolean(id),
    queryFn: () =>
      api
        // deliveries are capped ~100/reg server-side; fetch the lot and page
        // client-side (20/page) — no server pagination needed at this volume.
        // The list omits each row's raw payload (can be tens of KB); it's
        // fetched on demand via useTriggerRegistrationDelivery on row click.
        .get<IEnvelope<IWebhookDelivery[]>>(`/trigger-registrations/${id}/deliveries?limit=100`)
        .then(r => r.data),
  });

// Single delivery incl. its raw payload — fetched only when a row is expanded.
export const useTriggerRegistrationDelivery = (id: string, deliveryId: string | null) =>
  useQuery({
    queryKey: triggerRegistrationKeys.delivery(id, deliveryId ?? ""),
    enabled: Boolean(id) && Boolean(deliveryId),
    queryFn: () =>
      api
        .get<IEnvelope<IWebhookDelivery>>(`/trigger-registrations/${id}/deliveries/${deliveryId}`)
        .then(r => r.data),
  });

export const useCreateTriggerRegistration = () =>
  useMutation({
    mutationFn: (body: ICreateRegistrationBody) =>
      api.post<IEnvelope<ITriggerRegistration>>("/trigger-registrations", body).then(r => r.data),
  });

// PATCH: all fields optional; path is NOT updatable (backend UpdateRegistrationRequest
// has no path). Secret is write-only — omit it to keep the current one, set it to rotate.
export interface IUpdateRegistrationBody {
  name?: string;
  source?: string;
  secret?: string;
  hmac_secret?: string;
  emit_topic?: string;
  ip_allowlist?: string[];
  integration_actor?: string;
  enabled?: boolean;
}

export const useUpdateTriggerRegistration = () =>
  useMutation({
    mutationFn: ({ id, body }: { id: string; body: IUpdateRegistrationBody }) =>
      api.patch<IEnvelope<ITriggerRegistration>>(`/trigger-registrations/${id}`, body).then(r => r.data),
  });

export const useDeleteTriggerRegistration = () =>
  useMutation({
    mutationFn: (id: string) => api.delete(`/trigger-registrations/${id}`),
  });
