from dataclasses import dataclass
from typing import List, Dict, Any, Tuple
import re
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.rule_repository_interface import IRuleRepository
from app.layer1_domain.entities.rule_management import RuleSet


@dataclass(kw_only=True)
class CreateRuleSetCommand:
    name: str
    rules: List[Dict[str, Any]]
    description: str = ""


@dataclass(kw_only=True)
class RuleManagementResult:
    success: bool
    data: Any = None
    message: str = ""


class RuleManagementUseCase:
    def __init__(self, logger: ILogger, repository: IRuleRepository):
        self.logger = logger
        self.repository = repository

    async def create_rule_set(self, command: CreateRuleSetCommand) -> RuleManagementResult:
        self.logger.info(f"Creating RuleSet: {command.name}")
        rule_set = RuleSet.create(name=command.name, rules=command.rules, description=command.description)
        saved = await self.repository.save(rule_set)
        return RuleManagementResult(success=True, data=saved, message="Created successfully")

    async def update_rule_set(self, id: str, rules: List[Dict[str, Any]]) -> RuleManagementResult:
        rule_set = await self.repository.find_by_id(id)
        if not rule_set:
            return RuleManagementResult(success=False, message="RuleSet not found")
        rule_set.rules = rules
        saved = await self.repository.save(rule_set)
        return RuleManagementResult(success=True, data=saved, message="Updated successfully")

    async def delete_rule_sets(self, ids: List[str]) -> RuleManagementResult:
        for rid in ids:
            await self.repository.delete(rid)
        return RuleManagementResult(success=True, message="Deleted successfully")

    async def get_all_rules(self) -> RuleManagementResult:
        rules = await self.repository.find_all()
        return RuleManagementResult(success=True, data=rules)

    async def get_keywords(self) -> Dict[str, Any]:
        """Get summary of keywords for frontend mapping"""
        all_rules = await self.repository.find_all()
        keyword_rules = []
        for rs in all_rules:
            if rs.status == "inactive":
                continue
            for r in rs.rules:
                if r.get("keywords"):
                    keyword_rules.append({
                        "rule_id": r.get("rule_id", rs.id),
                        "rule_name": r.get("rule_name"),
                        "keywords": r.get("keywords")
                    })
        return {
            "description": "Keywords mapping summary.",
            "rules": keyword_rules
        }

    async def find_matching_rules(self, headers: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Match imported headers to rule templates via keywords"""
        all_rule_sets = await self.repository.find_all()
        relevant, descriptions = [], []

        normalized_headers = {re.sub(r'[^a-z0-9]', '', h.lower()): h for h in headers}

        def get_real_header(inp):
            clean = re.sub(r'[^a-z0-9]', '', str(inp).lower())
            for clean_h, real_h in normalized_headers.items():
                if clean in clean_h:
                    return real_h
            return None

        for rs in all_rule_sets:
            if rs.status == "inactive":
                continue
            for rule in rs.rules:
                params = rule.get("params", {}).copy()
                keywords = rule.get("keywords", [])
                expr = params.get("expression", "")

                is_template = not params.get("columns") and (
                    "(col)" in expr or "cols[" in expr or "pl.col(" in expr
                )

                if is_template and keywords:
                    for clean_h, real_h in normalized_headers.items():
                        if any(k.lower() in clean_h for k in keywords):
                            inst = rule.copy()
                            new_params = params.copy()
                            new_params["columns"] = [real_h]

                            if expr:
                                def repl(m):
                                    return f"pl.col({m.group(1)}{get_real_header(real_h)}{m.group(1)})"
                                new_params["expression"] = re.sub(
                                    r"pl\.col\(\s*(['\"])(.*?)\1\s*\)",
                                    repl,
                                    expr
                                )
                                if "(col)" in new_params["expression"]:
                                    new_params["expression"] = (
                                        new_params["expression"].replace(
                                            "(col)", f"pl.col('{real_h}')"
                                        )
                                    )

                            inst["params"] = new_params
                            inst["rule_name"] = (
                                f"{rule.get('rule_name')} ({real_h})"
                            )
                            relevant.append(inst)
                            descriptions.append(
                                rule.get("description", "").replace("{col}", real_h)
                            )

        return relevant, descriptions
