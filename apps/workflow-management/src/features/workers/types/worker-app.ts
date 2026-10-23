export type ReplicaIdentity = "known" | "pending";

export interface IWorkerAppAggregate {
    worker_app: string;
    delivery_mode: "push" | "pull" | "";
    /** DISTINCT node types the app offers — invariant under replica count. */
    nodes_total: number;
    nodes_healthy: number;
    /**
     * The node-type names behind nodes_total, sorted. Ships with the summary so
     * the nodes chip can list them on a collapsed row; reading them off the
     * lazily-fetched instance list left the popover empty until the row was
     * expanded. Optional so a control-plane that predates the field degrades to
     * a count-only chip instead of crashing.
     */
    node_types?: string[];
    /**
     * Meaningless when replica_identity is "pending" — do not render.
     *
     * When replica_identity is "pending", the registry was never told
     * which process each registration came from, so replicas_total and
     * replicas_healthy are zeros standing in for *unknown*, not a real
     * count of zero replicas.
     */
    replicas_total: number;
    /** See replicas_total — same "pending" caveat applies. */
    replicas_healthy: number;
    replica_identity: ReplicaIdentity;
    oldest_heartbeat_age_seconds: number | null;
}

export interface IWorkerAppInstance {
    instance_id: string;
    status: string;
    last_heartbeat: string;
    age_seconds: number | null;
    host: string;
    version: string;
    sdk_version: string;
    started_at: string;
    delivery_mode: string;
    nodes: string[];
}

export interface IWorkerAppHealthPayload extends IWorkerAppAggregate {
    at: string;
}
