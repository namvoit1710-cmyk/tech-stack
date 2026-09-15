"""Resolves a NodeDefinition inheritance chain into a single flattened result.

Walks parent_definition_id links from leaf to root, then merges root-to-leaf
so child values override parent values per the merge strategy.
"""

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.exceptions import (
    CircularInheritanceError,
    InheritanceDepthExceededError,
)

MAX_INHERITANCE_DEPTH = 10


def _parse_semver(s: str) -> tuple[int, int, int]:
    """Minimal semver parser for max() key. Non-numeric parts fall back to 0."""
    parts = (s or "0.0.0").split(".")
    out: list[int] = []
    for i in range(3):
        try:
            out.append(int(parts[i]) if i < len(parts) else 0)
        except (ValueError, IndexError):
            out.append(0)
    return out[0], out[1], out[2]


@dataclass
class ResolvedNodeDefinition:
    """The fully-merged result of walking a NodeDefinition inheritance chain."""
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] = field(default_factory=dict)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    input_mapping: dict[str, Any] = field(default_factory=dict)
    output_mapping: dict[str, Any] = field(default_factory=dict)
    icon: str = ""
    color: str = ""
    tags: list[str] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)
    data_mode: str = "json"
    edit_mode: str = "none"
    # Contract identity resolved from the leaf of the chain.
    resolved_spec_version: str = "1.0.0"


class NodeDefinitionResolver:
    """Resolves a NodeDefinition (possibly with parents) into a flat merged result."""

    def resolve(
        self,
        nd_id: str,
        repo: Any,
        spec_version: str | None = None,
    ) -> ResolvedNodeDefinition:
        """Resolve a definition by walking its parent chain and merging.

        Args:
            nd_id: NodeDefinition id (leaf of the chain).
            repo: Repository providing ``get_by_id(nd_id)``.
            spec_version: If provided, the leaf's ``spec_version`` must match;
                otherwise raises ``ValueError``. When ``None`` (unversioned
                reference), the leaf's ``spec_version`` is returned as
                ``resolved_spec_version``.

        Phase 0 scope: enforces exact-match pinning when ``spec_version`` is
        given. Phase 1 will extend this to search the inheritance chain for a
        compatible version and to prefer the highest non-deprecated version
        when unpinned.
        """
        chain = self._build_chain(nd_id, repo)
        result = self._merge_chain(chain)
        if chain:
            leaf = chain[-1]
            leaf_spec = getattr(leaf, "spec_version", "1.0.0") or "1.0.0"
            result.resolved_spec_version = leaf_spec
            if spec_version is not None and spec_version != leaf_spec:
                raise ValueError(
                    f"spec_version mismatch for node_definition_id={nd_id}: "
                    f"pinned={spec_version!r}, leaf={leaf_spec!r}"
                )
        return result

    def resolve_worker_spec_version(
        self,
        worker_type: str,
        pinned: str | None,
        workers: list[dict],
    ) -> str:
        """Resolve the ``spec_version`` to use for a worker_type reference.

        Rule:
            - If ``pinned`` is a non-empty string, return it unchanged.
            - Else, return the highest ``spec_version`` among registered
              workers matching ``worker_type``.
            - Else, return the default ``"1.0.0"``.

        Phase 0 scope: the rule is formalized here; the engine does not yet
        call this — Phase 1 wires it in at dispatch time.
        """
        if pinned:
            return pinned
        candidates = [
            (w.get("spec_version") or "1.0.0")
            for w in workers
            if w.get("worker_type") == worker_type
        ]
        if not candidates:
            return "1.0.0"
        return max(candidates, key=_parse_semver)

    def validate_parent_chain(self, nd_id: str, parent_id: str | None, repo: Any) -> None:
        """Validate that setting parent_id on nd_id won't create a cycle or exceed depth.

        Call this *before* persisting the change.
        """
        if not parent_id:
            return
        if parent_id == nd_id:
            raise CircularInheritanceError(
                f"NodeDefinition {nd_id} cannot be its own parent"
            )
        # Walk from the proposed parent upward to detect cycles and depth
        visited: set[str] = {nd_id}
        current_id: str | None = parent_id
        depth = 1
        while current_id:
            if current_id in visited:
                raise CircularInheritanceError(
                    f"Circular inheritance detected: {current_id} already in chain"
                )
            if depth > MAX_INHERITANCE_DEPTH:
                raise InheritanceDepthExceededError(
                    f"Inheritance depth exceeds maximum of {MAX_INHERITANCE_DEPTH}"
                )
            visited.add(current_id)
            nd = repo.get_by_id(current_id)
            if not nd:
                break  # parent not found — will be caught by use case validation
            current_id = nd.parent_definition_id
            depth += 1

    def _build_chain(self, nd_id: str, repo: Any) -> list[NodeDefinition]:
        """Walk from nd_id up through parents, returning [root, ..., leaf]."""
        chain: list[NodeDefinition] = []
        visited: set[str] = set()
        current_id: str | None = nd_id

        while current_id:
            if current_id in visited:
                raise CircularInheritanceError(
                    f"Circular inheritance detected: {current_id} already in chain"
                )
            if len(chain) > MAX_INHERITANCE_DEPTH:
                raise InheritanceDepthExceededError(
                    f"Inheritance depth exceeds maximum of {MAX_INHERITANCE_DEPTH}"
                )
            visited.add(current_id)
            nd = repo.get_by_id(current_id)
            if not nd:
                break
            chain.append(nd)
            current_id = nd.parent_definition_id

        # chain is [leaf, ..., root], reverse to [root, ..., leaf]
        chain.reverse()
        return chain

    def _merge_chain(self, chain: list[NodeDefinition]) -> ResolvedNodeDefinition:
        """Merge a chain [root, ..., leaf] into a single resolved result."""
        if not chain:
            return ResolvedNodeDefinition()

        result = ResolvedNodeDefinition()

        for nd in chain:
            # locked_defaults: shallow merge, child keys override
            result.locked_defaults = {**result.locked_defaults, **nd.locked_defaults}

            # input_schema: child replaces if non-empty
            if nd.input_schema:
                result.input_schema = nd.input_schema

            # output_schema: child replaces if non-empty
            if nd.output_schema:
                result.output_schema = nd.output_schema

            # input_mapping: shallow merge, child keys override
            result.input_mapping = {**result.input_mapping, **nd.input_mapping}

            # output_mapping: shallow merge, child keys override
            result.output_mapping = {**result.output_mapping, **nd.output_mapping}

            # base_worker_id: child wins if set
            if nd.base_worker_id:
                result.base_worker_id = nd.base_worker_id

            # data_mode: child wins if not default "json"
            if nd.data_mode != "json":
                result.data_mode = nd.data_mode

            # edit_mode: child wins if not default "none"
            if nd.edit_mode != "none":
                result.edit_mode = nd.edit_mode

            # icon: child wins if non-empty
            if nd.icon:
                result.icon = nd.icon

            # color: child wins if non-empty
            if nd.color:
                result.color = nd.color

            # tags: union (deduplicated)
            seen = set(result.tags)
            for tag in nd.tags:
                if tag not in seen:
                    result.tags.append(tag)
                    seen.add(tag)

            # ports: child replaces if non-empty
            if nd.ports:
                result.ports = nd.ports

        return result
