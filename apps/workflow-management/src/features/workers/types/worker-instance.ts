// SA-2424: one registered instance (replica) of a worker_type, as returned by
// GET /api/v2/worker-replicas/{worker_type} (WCP proxy -> WES R6). Shape mirrors
// the control-plane's _to_worker_instance mapping.
export interface IWorkerInstance {
    /** mirrors instance_id — used as a stable React key */
    id: string;
    instance_id: string;
    /** human worker name (WorkerRegistration.name), BE-provided; falls back to instance_id */
    name: string | null;
    host: string | null;
    health_status: string | null;
    last_heartbeat: string | null;
    age_seconds: number | null;
    version: string | null;
}
