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

"""Prefix-reusing Context Pack wire format for incremental context transfer."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DEFAULT_CHUNK_BYTES = 512


class ContextPackError(ValueError):
    """Report an invalid, tampered, or unstorable Context Pack payload."""


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(values: Any, field_name: str) -> tuple[bytes, ...]:
    if not isinstance(values, list):
        raise ContextPackError(f"{field_name} must be a list of Base64 strings")
    decoded: list[bytes] = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError(f"{field_name} must contain Base64 strings")
        try:
            decoded.append(base64.b64decode(value.encode("ascii"), validate=True))
        except (binascii.Error, UnicodeEncodeError) as error:
            raise ContextPackError(f"{field_name} contains invalid Base64") from error
    return tuple(decoded)


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pack_digest(items: tuple[bytes, ...]) -> str:
    canonical = _canonical({"schema": PACK_SCHEMA, "items": _encode_items(items)})
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _require_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ContextPackError(f"{field_name} must be a sha256 digest")
    try:
        int(value[7:], 16)
    except ValueError as error:
        raise ContextPackError(f"{field_name} must be a sha256 digest") from error
    return value


def _require_prefix(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context payload split into ordered UTF-8-safe chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Iterable[bytes], /) -> ContextPack:
        """Build a complete pack from already chunked raw bytes."""

        chunked = tuple(bytes(item) for item in items)
        digest = _pack_digest(chunked)
        byte_count = 0
        while True:
            data = _canonical(
                {
                    "schema": PACK_SCHEMA,
                    "items": _encode_items(chunked),
                    "digest": digest,
                    "byte_count": byte_count,
                }
            )
            if len(data) == byte_count:
                break
            byte_count = len(data)
        return cls(items=chunked, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = _DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split content deterministically into valid UTF-8 chunks within the byte bound."""

        if not isinstance(content, str):
            raise ContextPackError("content must be text")
        if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")

        chunks: list[str] = []
        current: list[str] = []
        current_bytes = 0
        for character in content:
            encoded = len(character.encode("utf-8"))
            if encoded > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")
            if current and current_bytes + encoded > chunk_bytes:
                chunks.append("".join(current))
                current = []
                current_bytes = 0
            current.append(character)
            current_bytes += encoded
        if current:
            chunks.append("".join(current))
        return cls.build(chunk.encode("utf-8") for chunk in chunks)

    def to_bytes(self) -> bytes:
        """Serialize the exact wire payload the receiver consumes."""

        return _canonical(
            {
                "schema": PACK_SCHEMA,
                "items": _encode_items(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPack:
        """Decode and validate one complete pack, including digest and byte count."""

        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ContextPackError("pack payload is not valid JSON") from error
        if not isinstance(payload, dict) or set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack payload must contain exactly schema, items, digest and byte_count")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unexpected pack schema")
        items = _decode_items(payload["items"], "items")
        digest = _require_digest(payload["digest"], "digest")
        if digest != _pack_digest(items):
            raise ContextPackError("pack digest does not match its items")
        byte_count = payload["byte_count"]
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise ContextPackError("byte_count must be a non-negative integer")
        if byte_count != len(data):
            raise ContextPackError("byte_count does not match the payload length")
        return cls(items=items, digest=digest, byte_count=byte_count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """The retained base prefix plus the target suffix of one context update."""

    base_digest: str = field()
    target_digest: str = field()
    prefix_items: int = field()
    appended: tuple[bytes, ...] = field()

    def __post_init__(self) -> None:
        _require_digest(self.base_digest, "base_digest")
        _require_digest(self.target_digest, "target_digest")
        _require_prefix(self.prefix_items)
        object.__setattr__(self, "appended", tuple(bytes(item) for item in self.appended))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Keep the common prefix and carry the remaining target chunks."""

        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPackDelta:
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ContextPackError("delta payload is not valid JSON") from error
        expected = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ContextPackError("delta payload must contain exactly the documented fields")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unexpected delta schema")
        return cls(
            base_digest=_require_digest(payload["base_digest"], "base_digest"),
            target_digest=_require_digest(payload["target_digest"], "target_digest"),
            prefix_items=_require_prefix(payload["prefix_items"]),
            appended=_decode_items(payload["appended"], "appended"),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a trusted base pack and one delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta does not apply to this base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack")
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack does not match the target digest")
    return rebuilt


__all__ = [
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
