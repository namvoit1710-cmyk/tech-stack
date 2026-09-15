"""Regression: ``/test/full`` must resolve node credential_refs into the run's
encrypted ``__credentials`` map — exactly like a real ``StartRun``.

Before the fix, ``TestFullWorkflowUseCase`` never called the credential
producer step, so a test run reached the worker with no ``__credentials``.
The worker's ``borrow_credential`` then returned ``None`` and the request went
out unauthenticated → the live ``401 "Requires authentication"`` on GitHub's
``/user/repos``. These tests pin credential seeding on BOTH branches (fresh run
and the reuse/reset branch) and confirm the dormant-vault path is unchanged.
"""
from __future__ import annotations

import contextlib

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
    TestFullWorkflowCommand, TestFullWorkflowUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
    MemoryWorkflowSnapshotRepository,
)
from tests.conftest import DummyLogger, DummyOrchestrationEngine


class _LockableEngine(DummyOrchestrationEngine):
    def locked_run(self, run_id):
        return contextlib.nullcontext()


class _FakeVault:
    """Stand-in for VaultCredentialClient. Records calls; returns a
    self-describing credential plus a transport extra (``expires_at``) that
    ``collect_and_resolve`` must strip."""

    def __init__(self):
        self.calls = []

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        self.calls.append((tenant_id, credential_id, user_id))
        return {
            "type": "bearer",
            "config": {"token": "secret-tok"},
            "expires_at": "2026-01-01T00:00:00Z",
        }


class _FakeCipher:
    def encrypt(self, data):
        return {"__enc__": data}


def _seed_workflow(repo: MemoryWorkflowRepository) -> Workflow:
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n1", name="N1", node_type=NodeType.TASK, worker_type="w"),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="n1")],
    )
    # The credential picker writes a static credential id onto the node; the
    # producer reads it from config/parameters/input_binding.
    node = wf.nodes[1]
    node.config = {**(node.config or {}), "credential_ref": "c1"}
    repo.save(wf)
    return wf


def _use_case(workflow_repo, run_repo, task_repo, *, vault=None, cipher=None):
    return TestFullWorkflowUseCase(
        logger=DummyLogger(),
        workflow_repo=workflow_repo,
        workflow_snapshot_repo=MemoryWorkflowSnapshotRepository(),
        run_repo=run_repo,
        task_repo=task_repo,
        orchestration_engine=_LockableEngine(),
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
        credential_cipher=cipher,
        vault_credential_client=vault,
    )


@pytest.mark.asyncio
async def test_test_full_seeds_encrypted_credentials_on_fresh_run():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    vault = _FakeVault()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, MemoryTaskRepository(),
                   vault=vault, cipher=_FakeCipher())

    res = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    assert res.error == ""

    # Vault was consulted for the node's ref, under the default "system" tenant
    # AND "system" user. Passing user_id=None here was the live 403: the vault's
    # owner check (ResolveCredentialUseCase) rejects a caller whose user_id does
    # not match a user-owned credential's owner ("system" on tenant-1), so the
    # credential silently never seeded and the worker went out unauthenticated.
    assert vault.calls == [("system", "c1", "system")]

    creds = run_repo.get_by_id(res.run_id).context.variables.get("__credentials")
    # Encrypted blob wrapping exactly {ref: {type, config}} — transport extras
    # (expires_at) stripped by collect_and_resolve.
    assert creds == {"__enc__": {"c1": {"type": "bearer",
                                        "config": {"token": "secret-tok"}}}}


@pytest.mark.asyncio
async def test_test_full_reseeds_credentials_on_reuse_branch():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    vault = _FakeVault()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, MemoryTaskRepository(),
                   vault=vault, cipher=_FakeCipher())

    first = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    # Second call hits the reuse/reset branch, which rebuilds context.variables.
    second = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    assert first.run_id == second.run_id  # same permanent test run

    creds = run_repo.get_by_id(second.run_id).context.variables.get("__credentials")
    assert creds == {"__enc__": {"c1": {"type": "bearer",
                                        "config": {"token": "secret-tok"}}}}, (
        "reuse branch must re-seed __credentials — wiping it was the 401 cause"
    )


@pytest.mark.asyncio
async def test_test_full_forwards_command_user_id_to_vault():
    # The vault's owner check resolves a user-owned credential only for its
    # owner, so the resolve call must carry the caller's user_id. A caller
    # (FE) overriding tenant/user must have both reach collect_and_resolve.
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    vault = _FakeVault()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, MemoryTaskRepository(),
                   vault=vault, cipher=_FakeCipher())

    res = await uc.execute(
        TestFullWorkflowCommand(workflow_id="wf", tenant_id="acme", user_id="u-42")
    )
    assert res.error == ""
    assert vault.calls == [("acme", "c1", "u-42")]


class _RejectingVault:
    """Vault that rejects every resolve (e.g. wrong tenant → 403)."""

    async def resolve_by_id(self, tenant_id, credential_id, user_id=None):
        raise RuntimeError(f"vault rejected resolve of {credential_id!r}: 403")


@pytest.mark.asyncio
async def test_test_full_vault_reject_is_non_fatal():
    # Regression: a vault reject (wrong tenant / deleted credential) must NOT
    # 500 the whole test run. The run proceeds without seeded credentials; the
    # credential node then fail-closes at the worker instead.
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, MemoryTaskRepository(),
                   vault=_RejectingVault(), cipher=_FakeCipher())

    res = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    assert res.error == ""  # no 500 — the run started
    assert "__credentials" not in run_repo.get_by_id(res.run_id).context.variables


@pytest.mark.asyncio
async def test_test_full_no_vault_leaves_no_credentials():
    # Dormant vault (no client wired) — path unchanged, no __credentials key.
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, MemoryTaskRepository())

    res = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    assert res.error == ""
    assert "__credentials" not in run_repo.get_by_id(res.run_id).context.variables
