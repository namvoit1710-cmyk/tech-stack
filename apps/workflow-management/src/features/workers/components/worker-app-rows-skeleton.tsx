import { cn } from "@ldc/ui";
import { Skeleton } from "@ldc/ui/components/skeleton";
import { APP_ROW_GRID } from "@/features/workers/components/worker-app-row";

interface WorkerAppRowsSkeletonProps {
    /** How many placeholder rows to draw. */
    rows?: number;
}

/**
 * First-load placeholder for the app list, built on the SAME grid template the
 * real rows use — so the columns are already where they will be and nothing
 * shifts when the snapshot lands.
 *
 * Replaces the spinner overlay: a spinner says only "wait", while this says
 * what is coming. It renders rows, never a count — the fleet size is exactly
 * the thing not known yet, and drawing five bars must not read as five workers
 * (hence aria-hidden; the live region announcing the load lives on the page).
 */
const WorkerAppRowsSkeleton = ({ rows = 5 }: WorkerAppRowsSkeletonProps) => (
    <div data-testid="workers-skeleton" aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => (
            <div key={i} className={cn(APP_ROW_GRID, "border-b px-4 py-3.5")}>
                <span />
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-14" />
                <Skeleton className="h-4 w-12" />
                <Skeleton className="h-4 w-8" />
                <Skeleton className="h-4 w-20" />
            </div>
        ))}
    </div>
);

export default WorkerAppRowsSkeleton;
