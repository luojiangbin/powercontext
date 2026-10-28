# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Source-dependent lifecycle for Memory and Topic Memory availability.

Callers register the evidence graph for one Scope explicitly: captured Sources, the
Memory entries they support, and the Topic Memory artifacts those entries support.
A Source event then propagates through the ``all``/``any`` dependency policies and
withdraws or restores every affected descendant. Registration, events, and the
derived availability live in the relational store; Memory entry state is owned by the
Memory service and Topic availability is projected onto native Topic search.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.lifecycle import (
    ACTIVE_STATE,
    INACTIVE_STATE,
    MEMORY_FAMILY,
    SOURCE_FAMILY,
    TOPIC_FAMILY,
    LifecycleNodeRecord,
    LifecycleNodeRepository,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.limits import MAX_LIFECYCLE_NODE_ID_LENGTH
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.persistence.database import AsyncDatabase
    from powercontext.builtin.runtime.application import BuiltinRuntime

LifecyclePolicy = Literal["all", "any"]
LifecyclePurpose = Literal["generation", "audit"]
LifecycleReference = SourceRef | MemoryCitation | ArtifactRef
ReferenceT = TypeVar("ReferenceT", bound=BaseModel)

_POLICIES = frozenset({"all", "any"})
_PURPOSES = frozenset({"generation", "audit"})
_REFERENCE_MODELS: dict[str, type[BaseModel]] = {
    SOURCE_FAMILY: SourceRef,
    MEMORY_FAMILY: MemoryCitation,
    TOPIC_FAMILY: ArtifactRef,
}
_REGISTRATION_REASON = "source lifecycle registration"
_REPOSITORY = LifecycleNodeRepository()


class SourceLifecycleError(ValueError):
    """Report a lifecycle registration or event that violates the dependency contract."""


class SourceLifecycleEvent(BaseModel):
    """One caller-declared availability change for a registered Source."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty, non-blank string")  # noqa: TRY003
        return value


class SourceLifecycleApplication:
    """Select the Source lifecycle service for one Scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    """Register an evidence graph and apply Source events inside one Scope.

    A Scope has a single writer, so registrations and events are not serialized against
    each other here; the database primary key keeps node identities unique. Derived state
    is authoritative in ``pc_lifecycle_nodes`` and only transitioned nodes are written to
    the owning Memory service, which makes one interrupted event safe to retry.
    """

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        revision: int = 1,
    ) -> None:
        """Bind one captured Source, or repeat an identical registration."""

        node_id = _require_node_id(node_id)
        reference = _require_reference(SourceRef, source_ref, "source-reference")
        revision = _require_positive_int(revision, "revision")
        nodes = await self._nodes()
        existing = nodes.get(node_id)
        if existing is not None:
            if _matches_registration(existing, SOURCE_FAMILY, reference, (), "all", revision):
                return
            raise SourceLifecycleError("node-conflict")
        await self._require_source(reference)
        record = LifecycleNodeRecord(
            node_id=node_id,
            family=SOURCE_FAMILY,
            reference=_encode_reference(reference),
            revision=revision,
            sequence=0,
            state=ACTIVE_STATE,
            parents=(),
            policy="all",
        )
        async with self._database().transaction() as connection:
            await _REPOSITORY.insert(connection, self.scope_id, record)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Sequence[str] = (),
        policy: LifecyclePolicy = "all",
    ) -> None:
        """Bind one readable Memory entry and derive its state from its parents now."""

        node_id = _require_node_id(node_id)
        reference = _require_reference(MemoryCitation, citation, "memory-citation")
        parents = _require_parents(parents)
        policy = _require_policy(policy)
        _require_not_self_dependent(node_id, parents)
        nodes = await self._nodes()
        existing = nodes.get(node_id)
        if existing is not None:
            if _matches_registration(
                existing,
                MEMORY_FAMILY,
                reference,
                parents,
                policy,
                reference.memory_ref.revision,
            ):
                return
            raise SourceLifecycleError("node-conflict")
        _require_parents_registered(parents, nodes)
        await self._require_memory_citation(reference)
        state = _derived_state(nodes, parents, policy)
        if state == INACTIVE_STATE:
            await self._write_memory_state(reference, state, _REGISTRATION_REASON)
        record = LifecycleNodeRecord(
            node_id=node_id,
            family=MEMORY_FAMILY,
            reference=_encode_reference(reference),
            revision=reference.memory_ref.revision,
            sequence=0,
            state=state,
            parents=parents,
            policy=policy,
            artifact_id=reference.memory_ref.artifact_id,
        )
        async with self._database().transaction() as connection:
            await _REPOSITORY.insert(connection, self.scope_id, record)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Sequence[str] = (),
        policy: LifecyclePolicy = "all",
    ) -> None:
        """Bind one readable Topic Memory revision and derive its state from its parents."""

        node_id = _require_node_id(node_id)
        reference = _require_reference(ArtifactRef, artifact_ref, "topic-reference")
        if reference.family != TopicMemory.family:
            raise SourceLifecycleError("topic-reference")
        parents = _require_parents(parents)
        policy = _require_policy(policy)
        _require_not_self_dependent(node_id, parents)
        nodes = await self._nodes()
        existing = nodes.get(node_id)
        if existing is not None:
            if _matches_registration(existing, TOPIC_FAMILY, reference, parents, policy, reference.revision):
                return
            raise SourceLifecycleError("node-conflict")
        _require_parents_registered(parents, nodes)
        await self._require_artifact(reference)
        state = _derived_state(nodes, parents, policy)
        record = LifecycleNodeRecord(
            node_id=node_id,
            family=TOPIC_FAMILY,
            reference=_encode_reference(reference),
            revision=reference.revision,
            sequence=0,
            state=state,
            parents=parents,
            policy=policy,
            artifact_id=reference.artifact_id,
        )
        async with self._database().transaction() as connection:
            await _REPOSITORY.insert(connection, self.scope_id, record)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source event and return every available node in sorted order.

        Memory transitions run before the derived state is persisted, so an interrupted
        propagation retries from the previous event and rewrites only what is still
        inactive/active. Replaying an already applied event is a pure read.
        """

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event-type")
        nodes = await self._nodes()
        source = nodes.get(event.node_id)
        if source is None or source.family != SOURCE_FAMILY:
            raise SourceLifecycleError("source-unknown")
        if event.revision != source.revision:
            raise SourceLifecycleError("revision-mismatch")
        if event.sequence < source.sequence:
            raise SourceLifecycleError("sequence-order")
        if event.sequence == source.sequence:
            if event.available == source.active:
                return sorted(node_id for node_id, record in nodes.items() if record.active)
            raise SourceLifecycleError("sequence-conflict")

        states = _resolve_states(
            nodes,
            event.node_id,
            ACTIVE_STATE if event.available else INACTIVE_STATE,
        )
        changed = [
            replace(
                record,
                state=states[record.node_id],
                sequence=event.sequence if record.node_id == event.node_id else record.sequence,
            )
            for record in nodes.values()
            if record.node_id == event.node_id or states[record.node_id] != record.state
        ]
        memory_changes = [
            record
            for record in changed
            if record.family == MEMORY_FAMILY and record.state != nodes[record.node_id].state
        ]
        for record in _dependency_order(nodes, memory_changes):
            await self._write_memory_state(
                _require_reference(MemoryCitation, _decode_reference(record), "memory-citation"),
                record.state,
                event.reason,
            )
        async with self._database().transaction() as connection:
            await _REPOSITORY.save(connection, self.scope_id, changed)
        return sorted(node_id for node_id, state in states.items() if state == ACTIVE_STATE)

    async def resolve(self, node_id: str, purpose: LifecyclePurpose = "generation") -> LifecycleReference:
        """Return the original reference, rejecting unavailable generation reads."""

        purpose = _require_purpose(purpose)
        if not isinstance(node_id, str) or not node_id.strip():
            raise SourceLifecycleError("node-unknown")
        record = (await self._nodes()).get(node_id)
        if record is None:
            raise SourceLifecycleError("node-unknown")
        if purpose == "generation" and not record.active:
            raise SourceLifecycleError("node-unavailable")
        return _decode_reference(record)

    async def inactive_topic_artifact_ids(self) -> frozenset[str]:
        """Topic artifacts whose current support is withdrawn, for native recall filters."""

        database = self._runtime.lifecycle_database
        if database is None:
            # Native recall without the lifecycle store has no withdrawn registrations to hide.
            return frozenset()
        async with database.transaction() as connection:
            return await _REPOSITORY.inactive_topic_artifact_ids(connection, self.scope_id)

    def _database(self) -> AsyncDatabase:
        database = self._runtime.lifecycle_database
        if database is None:
            raise SourceLifecycleError("lifecycle-unavailable")
        return database

    async def _nodes(self) -> dict[str, LifecycleNodeRecord]:
        async with self._database().transaction() as connection:
            records = await _REPOSITORY.load(connection, self.scope_id)
        return {record.node_id: record for record in records}

    async def _require_source(self, reference: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                reference.source_type,
                reference.source_id,
            )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("source-unknown") from error

    async def _require_memory_citation(self, citation: MemoryCitation) -> None:
        try:
            async with self._runtime._context(self.scope_id) as context:
                await context.artifacts.memory.validate_citation(citation)
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("memory-citation") from error

    async def _require_artifact(self, reference: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError("topic-artifact-unknown") from error

    async def _write_memory_state(self, citation: MemoryCitation, state: str, reason: str | None) -> None:
        """Move one Memory entry pointer through the Memory service's own transaction."""

        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            memory = await service.head(citation.memory_ref.artifact_id)
            entry = next(
                (item for item in await service.entries(memory) if item.entry_id == citation.entry_id),
                None,
            )
            if entry is None:
                raise SourceLifecycleError("memory-entry-missing")
            if state == ACTIVE_STATE:
                await service.reactivate(memory, entries=(entry,), reason=reason)
            else:
                await service.forget(memory, entries=(entry,), reason=reason)


def _require_node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("node-id")
    if len(value) > MAX_LIFECYCLE_NODE_ID_LENGTH:
        raise SourceLifecycleError("node-id")
    return value


def _require_positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError(field)
    return value


def _require_reference(
    model: type[ReferenceT],
    value: object,
    code: str,
) -> ReferenceT:
    if not isinstance(value, model):
        raise SourceLifecycleError(code)
    return value


def _require_parents(parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, str):
        raise SourceLifecycleError("dependency-parents")
    try:
        values = tuple(parents)
    except TypeError as error:
        raise SourceLifecycleError("dependency-parents") from error
    if not values:
        raise SourceLifecycleError("dependency-parents")
    for value in values:
        _require_node_id(value)
    if len(set(values)) != len(values):
        raise SourceLifecycleError("dependency-duplicate")
    return values


def _require_policy(policy: str) -> LifecyclePolicy:
    if policy not in _POLICIES:
        raise SourceLifecycleError("dependency-policy")
    return cast(LifecyclePolicy, policy)


def _require_purpose(purpose: str) -> LifecyclePurpose:
    if purpose not in _PURPOSES:
        raise SourceLifecycleError("resolve-purpose")
    return cast(LifecyclePurpose, purpose)


def _require_not_self_dependent(node_id: str, parents: tuple[str, ...]) -> None:
    # Parents must already be registered, so a self-reference is the only cycle a new
    # node can introduce; the resolver still re-checks the full graph defensively.
    if node_id in parents:
        raise SourceLifecycleError("dependency-cycle")


def _require_parents_registered(parents: tuple[str, ...], nodes: Mapping[str, LifecycleNodeRecord]) -> None:
    if any(parent not in nodes for parent in parents):
        raise SourceLifecycleError("dependency-parent-unknown")


def _matches_registration(
    record: LifecycleNodeRecord,
    family: str,
    reference: LifecycleReference,
    parents: tuple[str, ...],
    policy: str,
    revision: int,
) -> bool:
    return (
        record.family == family
        and record.reference == _encode_reference(reference)
        and record.parents == parents
        and record.policy == policy
        and record.revision == revision
    )


def _derived_state(
    nodes: Mapping[str, LifecycleNodeRecord],
    parents: tuple[str, ...],
    policy: str,
) -> str:
    support = [nodes[parent].active for parent in parents]
    return ACTIVE_STATE if (all(support) if policy == "all" else any(support)) else INACTIVE_STATE


def _resolve_states(
    nodes: Mapping[str, LifecycleNodeRecord],
    source_id: str,
    source_state: str,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    visiting: set[str] = set()

    def state_of(node_id: str) -> str:
        if node_id in resolved:
            return resolved[node_id]
        if node_id in visiting:
            raise SourceLifecycleError("dependency-cycle")
        record = nodes.get(node_id)
        if record is None:
            raise SourceLifecycleError("dependency-parent-unknown")
        if record.family == SOURCE_FAMILY:
            state = source_state if node_id == source_id else record.state
        else:
            visiting.add(node_id)
            try:
                support = [state_of(parent) == ACTIVE_STATE for parent in record.parents]
            finally:
                visiting.discard(node_id)
            state = ACTIVE_STATE if (all(support) if record.policy == "all" else any(support)) else INACTIVE_STATE
        resolved[node_id] = state
        return state

    for node_id in nodes:
        state_of(node_id)
    return resolved


def _dependency_order(
    nodes: Mapping[str, LifecycleNodeRecord],
    selected: Sequence[LifecycleNodeRecord],
) -> tuple[LifecycleNodeRecord, ...]:
    """Order transitions parents-first so every entry sees its final support."""

    selected_by_id = {record.node_id: record for record in selected}
    ordered: list[LifecycleNodeRecord] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for parent in nodes[node_id].parents:
            if parent in nodes:
                visit(parent)
        if node_id in selected_by_id:
            ordered.append(selected_by_id[node_id])

    for node_id in sorted(selected_by_id):
        visit(node_id)
    return tuple(ordered)


def _encode_reference(reference: LifecycleReference) -> bytes:
    return reference.model_dump_json().encode("utf-8")


def _decode_reference(record: LifecycleNodeRecord) -> LifecycleReference:
    model = _REFERENCE_MODELS.get(record.family)
    if model is None:
        raise SourceLifecycleError("stored-reference")
    try:
        return cast(LifecycleReference, model.model_validate_json(record.reference, strict=True))
    except ValidationError as error:
        raise SourceLifecycleError("stored-reference") from error


__all__ = [
    "LifecyclePolicy",
    "LifecyclePurpose",
    "LifecycleReference",
    "ScopedSourceLifecycle",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
