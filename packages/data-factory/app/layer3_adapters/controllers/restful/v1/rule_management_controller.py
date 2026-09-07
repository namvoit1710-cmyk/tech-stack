from fastapi import APIRouter, HTTPException, Request, Body
from typing import List, Dict, Any
from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto
from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import CreateRuleSetCommand
from dataclasses import asdict


router = APIRouter()


class CreateRuleSetDto(BaseInputDto):
    name: str
    rules: List[Dict[str, Any]]
    description: str = ""


def get_usecase(request: Request):
    return request.app.state.container.get("rule_management_usecase")


@router.post("")
async def create_rule_set(request: Request, dto: CreateRuleSetDto):
    usecase = get_usecase(request)
    command = CreateRuleSetCommand(name=dto.name, rules=dto.rules, description=dto.description)
    result = await usecase.create_rule_set(command)
    return asdict(result)


@router.get("")
async def get_all_rule_sets(request: Request):
    usecase = get_usecase(request)
    result = await usecase.get_all_rules()
    return asdict(result)


@router.put("/update/{rule_set_id}")
async def update_rule_set(request: Request, rule_set_id: str, rules: List[Dict[str, Any]] = Body(..., embed=True)):
    usecase = get_usecase(request)
    result = await usecase.update_rule_set(rule_set_id, rules)
    if not result.success:
        raise HTTPException(400, result.message)
    return asdict(result)


@router.post("/delete")
async def delete_rule_sets(request: Request, ids: List[str] = Body(..., embed=True)):
    usecase = get_usecase(request)
    result = await usecase.delete_rule_sets(ids)
    return asdict(result)


@router.get("/keywords")
async def get_keywords(request: Request):
    usecase = get_usecase(request)
    return await usecase.get_keywords()


@router.post("/match")
async def match_rules(request: Request, headers: List[str] = Body(..., embed=True)):
    usecase = get_usecase(request)
    rules, descs = await usecase.find_matching_rules(headers)
    return {"rule_templates": rules, "descriptions": descs}
