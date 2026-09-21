from dataclasses import dataclass
from typing import Any

from app.layer1_domain.entities.diagnostic_report import DiagnosticReport
from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class ReportInputDto:
    report: DiagnosticReport


@dataclass
class ReportOutputDto:
    report: dict[str, Any]
    summary: str


class ReportUseCase:
    """Step 3 — assemble the caller-facing report: the structured diagnosis + a one-line summary."""

    def __init__(self, logger: ILogger):
        self._logger = logger

    async def execute(self, dto: ReportInputDto) -> ReportOutputDto:
        report = dto.report
        # The user-facing message is the plain-language explanation when we have one; the
        # technical verdict stays in the structured report (agent_data). Fall back to the
        # one-line technical summary only when no explanation was produced (keeps prior behavior).
        summary = report.explanation or (
            f"Run {report.run_id or '(unknown)'}: {report.category.value} "
            f"[{report.severity}] — {report.root_cause}"
        )
        self._logger.info(
            f"Report for run {report.run_id or '(unknown)'}: {report.category.value} "
            f"[{report.severity}]"
        )
        return ReportOutputDto(report=report.to_dict(), summary=summary)
