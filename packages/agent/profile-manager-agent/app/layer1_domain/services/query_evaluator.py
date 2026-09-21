from app.layer1_domain.entities.query_result import QueryEvaluation, QueryResult


class QueryEvaluator:
    """Pure enterprise rule: judge a query result as correct / warning / incorrect.

    Lifted verbatim from ``ProfileManagerProcessor.evaluate_query_result`` so the
    verdicts are byte-identical; only the shape changed (dict -> domain entities).
    """

    def evaluate(self, result: QueryResult) -> QueryEvaluation:
        items = result.users or []
        expression = result.odata_query or ""
        user_prompt = result.user_prompt or ""
        error = result.error

        # 1. Error from the API -> INCORRECT
        if error:
            return QueryEvaluation(
                valid="incorrect",
                message=f"This rule is not supported: {error}",
                item_size=str(len(items)),
                expression=expression,
                user_prompt=user_prompt,
            )

        # 2. Empty result -> WARNING
        if len(items) == 0:
            return QueryEvaluation(
                valid="warning",
                message="Rule is valid but no matching. Are you sure?",
                item_size="0",
                expression=expression,
                user_prompt=user_prompt,
            )

        # 3. Has results -> CORRECT
        return QueryEvaluation(
            valid="correct",
            message="",
            item_size=str(len(items)),
            expression=expression,
            user_prompt=user_prompt,
        )
