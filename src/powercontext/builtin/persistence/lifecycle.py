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

"""Persistence for Source-dependent lifecycle registrations and availability."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import LIFECYCLE_NODES_TABLE

SOURCE_FAMILY = "source"
MEMORY_FAMILY = "memory"
TOPIC_FAMILY = "topic"
ACTIVE_STATE = "active"
INACTIVE_STATE = "inactive"


@dataclass(frozen=True)
class LifecycleNodeRecord:
    """One registered lifecycle node with its derived availability.

    ``reference`` is the canonical JSON payload of the bound SourceRef, MemoryCitation,
    or ArtifactRef. Sources carry the caller-declared lifecycle ``revision`` and the last
    applied event ``sequence``; derived nodes carry the bound reference revision instead.
    """

    node_id: str
    family: str
    reference: bytes
    revision: int
    sequence: int
    state: str
    parents: tuple[str, ...]
    policy: str
    artifact_id: str | None = None

    @property
    def active(self) -> bool:
        return self.state == ACTIVE_STATE


class LifecycleNodeRepository:
    """Read and write immutable registrations plus their derived state."""

    async def load(self, connection: AsyncConnection, scope_id: str, /) -> tuple[LifecycleNodeRecord, ...]:
        rows = (
            (
                await connection.execute(
                    select(LIFECYCLE_NODES_TABLE).where(LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                )
            )
            .mappings()
            .all()
        )
        return tuple(_decode_row(row) for row in rows)

    async def insert(self, connection: AsyncConnection, scope_id: str, node: LifecycleNodeRecord, /) -> None:
        await connection.execute(
            insert(LIFECYCLE_NODES_TABLE).values(
                scope_id=scope_id,
                node_id=node.node_id,
                family=node.family,
                state=node.state,
                reference=node.reference,
                revision=node.revision,
                sequence=node.sequence,
                parents=encode_parents(node.parents),
                policy=node.policy,
                artifact_id=node.artifact_id,
            )
        )

    async def save(self, connection: AsyncConnection, scope_id: str, nodes: Sequence[LifecycleNodeRecord], /) -> None:
        """Persist state transitions; registration fields are never rewritten."""

        for node in nodes:
            await connection.execute(
                update(LIFECYCLE_NODES_TABLE)
                .where(
                    LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
                )
                .values(state=node.state, sequence=node.sequence)
            )

    async def inactive_topic_artifact_ids(self, connection: AsyncConnection, scope_id: str, /) -> frozenset[str]:
        """Return Topic artifact identities whose supporting evidence is currently withdrawn."""

        artifact_ids = (
            await connection.execute(
                select(LIFECYCLE_NODES_TABLE.c.artifact_id).where(
                    LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    LIFECYCLE_NODES_TABLE.c.family == TOPIC_FAMILY,
                    LIFECYCLE_NODES_TABLE.c.state == INACTIVE_STATE,
                    LIFECYCLE_NODES_TABLE.c.artifact_id.is_not(None),
                )
            )
        ).scalars()
        return frozenset(str(artifact_id) for artifact_id in artifact_ids)


def encode_parents(parents: Sequence[str], /) -> bytes:
    return json.dumps(list(parents), separators=(",", ":")).encode()


def decode_parents(payload: object, /) -> tuple[str, ...]:
    if isinstance(payload, bytearray | memoryview):
        payload = bytes(payload)
    if not isinstance(payload, bytes):
        raise ValueError("lifecycle parents must be stored as JSON bytes")  # noqa: TRY003
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError("lifecycle parents must be a JSON string list")  # noqa: TRY003
    return tuple(decoded)


def _decode_row(values: Mapping[str, Any]) -> LifecycleNodeRecord:
    return LifecycleNodeRecord(
        node_id=str(values["node_id"]),
        family=str(values["family"]),
        reference=bytes(values["reference"]),
        revision=int(values["revision"]),
        sequence=int(values["sequence"]),
        state=str(values["state"]),
        parents=decode_parents(values["parents"]),
        policy=str(values["policy"]),
        artifact_id=None if values["artifact_id"] is None else str(values["artifact_id"]),
    )


__all__ = [
    "ACTIVE_STATE",
    "INACTIVE_STATE",
    "MEMORY_FAMILY",
    "SOURCE_FAMILY",
    "TOPIC_FAMILY",
    "LifecycleNodeRecord",
    "LifecycleNodeRepository",
    "decode_parents",
    "encode_parents",
]
