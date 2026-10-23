// SA-2590 — Dashboard → Worker. Scaffolded placeholder (route + nav wired),
// metrics deferred to a later iteration (U5).
import Page from "@/components/containers/page";

const DashboardWorkerPage = () => {
    return (
        <Page>
            <Page.Header title="Worker analytics" description="Per-worker throughput & health" />
            <div className="flex-1 overflow-y-auto px-4 pb-8">
                <div className="rounded-xl border bg-card p-4">
                    <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
                        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-dashed text-2xl">🛠️</div>
                        <div className="text-base font-semibold">Worker analytics — coming soon</div>
                        <p className="max-w-md text-sm text-muted-foreground">
                            This tab is scaffolded (route + sidebar entry) but intentionally empty for this iteration.
                            The panel layout will mirror the Workflow tab once worker-level metrics are wired.
                        </p>
                    </div>
                </div>
            </div>
        </Page>
    );
};

export default DashboardWorkerPage;
