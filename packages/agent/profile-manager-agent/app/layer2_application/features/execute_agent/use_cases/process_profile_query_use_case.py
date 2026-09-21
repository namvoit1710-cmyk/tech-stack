"""Application use case: the single core of the profile-manager agent.

Owns the whole flow — plan -> execute -> evaluate -> build response — that used
to live in the layer4 ``ProfileManagerProcessor``. It depends only on layer2
ports and layer1 domain, so the dependency rule points inward (P1, P6).
"""
import logging
from dataclasses import dataclass
from itertools import groupby
from typing import Any, Dict, Iterable, List, Tuple

from app.layer1_domain.entities.execution_plan import ExecutionPlan, PlanStep
from app.layer1_domain.entities.query_result import QueryEvaluation, QueryResult
from app.layer1_domain.services.query_evaluator import QueryEvaluator
from app.layer2_application.interfaces.planner_port import IPlannerPort
from app.layer2_application.interfaces.user_query_port import IUserQueryPort


@dataclass
class ProcessProfileQueryCommand:
    message: str
    token: str | None = None
    agent_type: str = ""


@dataclass
class ProcessProfileQueryResult:
    """Value object over the ``formatted_response`` wire shape (C5).

    The response dict is built here so the use case is the single source of the
    contract; the node/controller only pass it through.
    """

    formatted_response: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return self.formatted_response


class ProcessProfileQueryUseCase:
    def __init__(
        self,
        planner: IPlannerPort,
        user_query: IUserQueryPort,
        evaluator: QueryEvaluator | None = None,
    ) -> None:
        self._planner = planner
        self._user_query = user_query
        self._evaluator = evaluator or QueryEvaluator()

    async def execute(
        self, command: ProcessProfileQueryCommand
    ) -> ProcessProfileQueryResult:
        agent_type = command.agent_type
        logging.info("event=message_received message=%s", command.message)
        try:
            plan = await self._planner.plan(user_prompt=command.message)
            logging.info("step=generate_execution_plan steps=%d", len(plan.steps))

            if not plan.is_supported:
                return self._build_not_support_response(agent_type)

            query_result = await self._run_plan(plan, command.token)
            logging.info("step=execute_plan query_result=%s", query_result)

            evaluation = self._evaluator.evaluate(query_result)
            return self._build_success_response(evaluation, agent_type)
        except Exception as e:  # noqa: BLE001 — preserve the catch-all error contract
            logging.exception("process_profile_query_failed")
            return self._build_error_response(e, agent_type)

    # -- flow helpers -------------------------------------------------------- #

    async def _run_plan(self, plan: ExecutionPlan, token: str | None) -> QueryResult:
        """Group steps by scope and dispatch each batch to its port.

        Mirrors the old ``_process_file``: the ``user`` scope is the only
        supported one; an unknown scope raises ``ValueError`` (caught above and
        turned into an error response). Empty plans return an empty result
        (previously an ``UnboundLocalError`` — now a defined behaviour).
        """
        executors = {"user": self._user_query}
        result = QueryResult()
        for scope, batch in self._group_by_scope(plan.steps):
            logging.info("step=execute_scope scope=%s", scope)
            executor = executors.get(scope)
            if not executor:
                raise ValueError(f"Unsupported scope: {scope}")
            result = await executor.query(batch, token)
        result.user_prompt = plan.user_prompt
        return result

    @staticmethod
    def _group_by_scope(
        steps: List[PlanStep],
    ) -> Iterable[Tuple[str, List[PlanStep]]]:
        for scope, group in groupby(steps, key=lambda s: s.scope):
            yield scope, list(group)

    # -- response builders (lifted verbatim from ProfileManagerProcessor) ---- #

    def _build_success_response(
        self, evaluation: QueryEvaluation, agent_type: str
    ) -> ProcessProfileQueryResult:
        logging.info("Profile manager run success with output=%s", evaluation)
        return ProcessProfileQueryResult(
            {
                "content": "Profile Manager Agent execute successfully.",
                "status": "success",
                "agent_type": agent_type,
                "agent_data": self._build_agent_data(
                    {
                        "user_prompt": evaluation.user_prompt,
                        "expression": evaluation.expression,
                        "valid": evaluation.valid,
                        "message": evaluation.message,
                        "item_size": evaluation.item_size,
                    },
                    agent_type,
                ),
                "transport_state": "RECEIVED",
            }
        )

    def _build_error_response(
        self, e: Exception, agent_type: str
    ) -> ProcessProfileQueryResult:
        return ProcessProfileQueryResult(
            {
                "content": "Profile Manager Agent execute fail.",
                "status": "error",
                "agent_type": agent_type,
                "agent_data": self._build_agent_data(
                    {"valid": "incorrect", "message": str(e)}, agent_type
                ),
            }
        )

    def _build_not_support_response(
        self, agent_type: str
    ) -> ProcessProfileQueryResult:
        return ProcessProfileQueryResult(
            {
                "content": "Profile Manager Agent not supported.",
                "status": "error",
                "agent_type": agent_type,
                "agent_data": self._build_agent_data(
                    {
                        "valid": "incorrect",
                        "message": "Profile Manager Agent not supported.",
                    },
                    agent_type,
                ),
            }
        )

    @staticmethod
    def _build_agent_data(
        payload: Dict[str, Any], agent_type: str
    ) -> Dict[str, Any]:
        return {"agent_type": agent_type, **payload}
