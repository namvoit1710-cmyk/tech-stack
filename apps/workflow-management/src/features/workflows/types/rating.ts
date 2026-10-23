// SA-2741 / SA-2876: per-run stars (GitHub-style, permanent). `count` is the
// total number of stars; a workflow's total is over all its runs.

// {count} = total stars of one workflow (Workflows tab) or one run.
export interface IRatingAggregate {
    count: number;
}

// One Execution-History run row: the run's own stars, its workflow's total, and
// whether the viewer already starred it (then the Star button is locked).
export interface IRunRatingView extends IRatingAggregate {
    workflow_id: string | null;
    workflow_total: number;
    my_starred: boolean;
}

// GET /runs/rating/summary?ids=  -> { [workflow_id]: {count} }
export type IWorkflowRatingsResponse = Record<string, IRatingAggregate>;

// GET /runs/rating/by-run?ids=   -> { [run_id]: IRunRatingView }
export type IRunRatingsResponse = Record<string, IRunRatingView>;

// GET /runs/rating/raters?workflow_id=  -> names only (privacy)
export interface IRatersResponse {
    total: number;
    names: string[];
}

// POST /runs/{run_id}/star response (201 new star / 200 already starred)
export interface IRateRunResponse {
    run_id: string;
    starred: boolean;
    already_starred: boolean;
    workflow_ids: string[];
    aggregates: IWorkflowRatingsResponse;
    rated_by: string;
    is_system: boolean;
}
