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

"""Capture one selected Pi session branch as immutable event evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
_CONTROL_TYPES = frozenset({"compaction", "branch_summary", "custom_message"})


class PiSessionInput(BaseModel):
    """One Pi v3 JSONL archive plus the leaf that selects the visible branch."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, preserving its raw record."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi event whose identity is session plus event."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one Pi event."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pi event source id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return identity


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse the whole archive and return visible events from root to the selected leaf."""

    text = value.content.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session archive must not be empty")

    header = _load_object(lines[0])
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session header must declare type=session and version=3")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must declare a non-empty id")

    records: dict[str, dict[str, JsonValue]] = {}
    order: list[str] = []
    for line in lines[1:]:
        record = _load_object(line)
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("every Pi record must declare a non-empty id")
        if event_id in records:
            raise ValueError(f"duplicate Pi record id: {event_id!r}")
        parent_id = record.get("parentId")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in records):
            raise ValueError(f"Pi record {event_id!r} references an unknown parent")
        records[event_id] = record
        order.append(event_id)

    if not order:
        raise ValueError("Pi session archive must contain at least one event record")

    leaf_id = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf_id not in records:
        raise ValueError(f"selected Pi leaf {leaf_id!r} does not exist")

    chain: list[dict[str, JsonValue]] = []
    cursor: str | None = leaf_id
    while cursor is not None:
        record = records[cursor]
        chain.append(record)
        parent = record.get("parentId")
        cursor = parent if isinstance(parent, str) else None
    chain.reverse()

    events: list[PiEventInput] = []
    for record in chain:
        visible = _visible_event(session_id, record)
        if visible is not None:
            events.append(visible)
    return tuple(events)


def _visible_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_id = record["id"]
    assert isinstance(event_id, str)
    parent_id = record.get("parentId")
    event_type = record.get("type")

    role: str | None = None
    body: str | None = None
    if event_type == "message":
        role, body = _message_event(record)
    elif event_type in ("compaction", "branch_summary"):
        summary = record.get("summary")
        if isinstance(summary, str) and summary:
            role, body = str(event_type), summary
    elif event_type == "custom_message":
        text = _blocks_text(record.get("content"))
        if text:
            role, body = "custom", text

    if role is None or body is None or not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id if isinstance(parent_id, str) else None,
        event_type=str(event_type),
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_event(record: dict[str, JsonValue]) -> tuple[str | None, str | None]:
    message = record.get("message")
    if not isinstance(message, dict):
        return None, None
    role = message.get("role")
    if not isinstance(role, str):
        return None, None
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        command_text = command if isinstance(command, str) else ""
        output_text = output if isinstance(output, str) else ""
        body = f"$ {command_text}"
        if output_text:
            body = f"{body}\n{output_text}"
        return role, body
    if role == "toolResult":
        name = message.get("toolName")
        call_id = message.get("toolCallId")
        text = _blocks_text(message.get("content"))
        prefix = f"[tool_result {name if isinstance(name, str) else ''} {call_id if isinstance(call_id, str) else ''}]"
        return role, f"{prefix} {text}" if text else prefix
    parts: list[str] = []
    for block in _blocks(message.get("content")):
        if isinstance(block, str):
            parts.append(block)
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            arguments = block.get("arguments")
            rendered = json.dumps(
                arguments if isinstance(arguments, dict) else {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            parts.append(f"[tool_call {name if isinstance(name, str) else ''} {call_id if isinstance(call_id, str) else ''}] {rendered}")
    return role, "\n".join(parts)


def _blocks(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _blocks_text(value: Any) -> str:
    parts: list[str] = []
    for block in _blocks(value):
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _load_object(line: str) -> dict[str, JsonValue]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"malformed Pi JSON record: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("every Pi JSON record must be an object")
    return value


def _record_digest(record: dict[str, JsonValue]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PiEventSourceAdapter:
    """Resolve and read one Pi event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import one selected Pi branch incrementally without repeating history."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))

        state = session.checkpoint if isinstance(session.checkpoint, dict) else {}
        known = dict(state.get("events") or {})
        order = list(state.get("order") or [])

        progressed = False
        for event in events:
            digest = _record_digest(event.record)
            previous = known.get(event.event_id)
            if previous is not None:
                if previous != digest:
                    raise ValueError(f"Pi event {event.event_id!r} changed after it was captured")
                continue
            outcome = await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=None)
            known[event.event_id] = digest
            order.append(event.event_id)
            progressed = True

        if not progressed:
            return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=session.checkpoint)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"events": known, "order": order},
        )


__all__ = [
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
