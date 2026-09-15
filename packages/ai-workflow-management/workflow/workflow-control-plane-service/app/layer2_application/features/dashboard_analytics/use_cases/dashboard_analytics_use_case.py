"""SA-2597 — Dashboard analytics use case.

Aggregates the run/execution store (via the run repository) into the two shapes
the workflow-management Dashboard tab (SA-2590) renders:

  * ``overview``  — tenant-wide KPIs + runs-over-time + outcomes + top workflows
  * ``workflows`` — per-workflow usage table + ranking + trend, filterable by
                    time range / owner / name / status

Owner ids (Keycloak ``sub``, stored on ``AIW_WORKFLOWS.OWNER`` by SA-2570) are
resolved to display names server-side (cached) so the FE shows names, never ids.
Only real, top-level, non-test runs are counted — editor "test" runs are excluded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger

_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}
_FMT = "%Y-%m-%dT%H:%M:%S"


def _cutoff(rng: str) -> str:
    days = _RANGE_DAYS.get(rng, 30)
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(_FMT)


def _rel(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.strptime(str(ts)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return str(ts)
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 90:
        return "just now"
    for unit, s in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= s:
            return f"{int(secs // s)}{unit} ago"
    return "just now"


class DashboardAnalyticsUseCase:
    def __init__(self, logger: ILogger, run_repo: Any,
                 keycloak_admin_client: Any = None, **kwargs: Any) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.keycloak_admin_client = keycloak_admin_client

    async def _names(self) -> dict[str, str]:
        """Keycloak sub → display name (cached in the client). Empty on any failure."""
        if not self.keycloak_admin_client:
            return {}
        try:
            return await self.keycloak_admin_client.name_map()
        except Exception as exc:  # pragma: no cover - degrade gracefully
            self.logger.warning(f"owner name resolution failed: {exc}")
            return {}

    async def overview(self, rng: str, tenant: str | None) -> dict:
        if not hasattr(self.run_repo, "dashboard_overview"):
            return self._empty_overview(rng)
        d = self.run_repo.dashboard_overview(_cutoff(rng), tenant)
        agg = d["agg"] or {}
        total = int(agg.get("total") or 0)
        ok = int(agg.get("ok") or 0)
        failed = int(agg.get("failed") or 0)
        settled = ok + failed
        return {
            "range": rng,
            "kpis": {
                "workflows": d["total_workflows"],
                "runs": total,
                "successRate": round(ok / settled * 100, 1) if settled else 0.0,
                "activeWorkers": 0,  # worker metrics deferred (SA-2596)
            },
            "runsOverTime": [
                {"date": r["d"], "success": int(r["ok"] or 0), "failed": int(r["failed"] or 0)}
                for r in d["series"]
            ],
            "outcomes": {"success": ok, "failed": failed},
            "topWorkflows": [{"label": r["name"], "value": int(r["c"] or 0)} for r in d["top"]],
            "workers": [],
        }

    async def workflows(self, rng: str, tenant: str | None,
                        owner: str | None, q: str | None, status: str | None,
                        page: int = 1, page_size: int = 10) -> dict:
        names = await self._names()
        name_to_id = {v: k for k, v in names.items()}
        owner_id = name_to_id.get(owner, owner) if owner else None
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        if not hasattr(self.run_repo, "dashboard_workflows"):
            return self._empty_workflows(rng, owner, q, status, page, page_size)
        d = self.run_repo.dashboard_workflows(
            _cutoff(rng), tenant, owner_id, q, status, (page - 1) * page_size, page_size)
        rows = []
        for r in d["rows"]:
            runs = int(r["runs"] or 0)
            ok = int(r["ok"] or 0)
            owner_id_val = r.get("owner")
            rows.append({
                "id": r["id"],
                "name": r["name"],
                "owner": names.get(owner_id_val, owner_id_val or "Unknown"),
                "status": r.get("status") or "",
                "runs": runs,
                # SA-2626: direct vs as-sub-flow split for the Runs-cell hover
                # breakdown; runs == direct + sub (single total column).
                "direct": int(r.get("direct") or 0),
                "sub": int(r.get("sub") or 0),
                "successRate": round(ok / runs * 100, 1) if runs else 0.0,
                "avgMs": int((r.get("avg_s") or 0) * 1000),
                "lastRun": _rel(r.get("last_run")),
            })
        vals = [int(x["c"] or 0) for x in d["daily"]]
        dates = [x["d"] for x in d["daily"]]
        trend: list[int] = []
        trend_labels: list[str] = []  # SA-2625: bucket start-date → x-axis label
        if vals:
            step = max(1, len(vals) // 8)
            buckets = list(range(0, len(vals), step))
            trend = [sum(vals[i:i + step]) for i in buckets][-8:]
            trend_labels = [dates[i] for i in buckets][-8:]
        matched = int(d.get("matched") or 0)
        total_runs = int(d.get("total_runs") or 0)
        return {
            "range": rng,
            "filters": {"owner": owner or "", "q": q or "", "status": status or "all"},
            "matched": matched,
            "total": d["total"],
            "runsInRange": total_runs,
            "avgRuns": round(total_runs / matched) if matched else 0,
            # ranked (top-6) is computed across the WHOLE matched set, not just the page
            "ranked": [{"label": r["name"], "value": int(r["runs"] or 0)} for r in d.get("ranked", [])],
            "trend": trend,
            "trendLabels": trend_labels,
            "rows": rows,
            "page": page,
            "pageSize": page_size,
            "totalPages": (matched + page_size - 1) // page_size if matched else 1,
        }

    @staticmethod
    def _empty_overview(rng: str) -> dict:
        return {"range": rng, "kpis": {"workflows": 0, "runs": 0, "successRate": 0.0, "activeWorkers": 0},
                "runsOverTime": [], "outcomes": {"success": 0, "failed": 0}, "topWorkflows": [], "workers": []}

    @staticmethod
    def _empty_workflows(rng: str, owner: str | None, q: str | None, status: str | None,
                         page: int = 1, page_size: int = 10) -> dict:
        return {"range": rng, "filters": {"owner": owner or "", "q": q or "", "status": status or "all"},
                "matched": 0, "total": 0, "runsInRange": 0, "avgRuns": 0,
                "ranked": [], "trend": [], "trendLabels": [], "rows": [],
                "page": page, "pageSize": page_size, "totalPages": 1}
