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

"""Coalesce concurrent Embedding requests and cache successful vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(slots=True)
class _Batch:
    """One underlying provider request shared by every waiting caller."""

    usage: InferenceUsage | None = None
    numbers: list[int] = field(default_factory=list)


class CoalescingEmbeddingModel:
    """Share in-flight embedding work and reuse successful vectors across calls."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not isinstance(max_batch_size, int) or isinstance(max_batch_size, bool) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")

        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._waiters: dict[str, asyncio.Future[tuple[EmbeddingVector, _Batch]]] = {}
        self._pending: list[str] = []
        self._dispatcher: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def profile(self):  # noqa: ANN201 - mirrors the wrapped model's declared profile
        return self._model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique = list(dict.fromkeys(texts))
        resolved: dict[str, EmbeddingVector] = {}
        waited: dict[str, asyncio.Future[tuple[EmbeddingVector, _Batch]]] = {}

        async with self._lock:
            for text in unique:
                cached = self._cache.get(text)
                if cached is not None:
                    self._cache.move_to_end(text)
                    resolved[text] = cached
                    continue
                future = self._waiters.get(text)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    future.add_done_callback(_silence_unretrieved_error)
                    self._waiters[text] = future
                    self._pending.append(text)
                waited[text] = future
            self._ensure_dispatcher()

        usage = InferenceUsage(requests=0)
        counted: set[int] = set()
        for text, future in waited.items():
            vector, batch = await asyncio.shield(future)
            resolved[text] = vector
            if id(batch) not in counted:
                counted.add(id(batch))
                usage = _merge_usage(usage, batch.usage)

        return EmbeddingResult(vectors=tuple(resolved[text] for text in texts), usage=usage)

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher is not None and not self._dispatcher.done():
            return
        self._dispatcher = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        if self._coalesce_seconds:
            await asyncio.sleep(self._coalesce_seconds)
        else:
            await asyncio.sleep(0)
        while True:
            async with self._lock:
                if not self._pending:
                    return
                batch_texts = self._pending[: self._max_batch_size]
                del self._pending[: len(batch_texts)]
            await self._run_batch(batch_texts)

    async def _run_batch(self, batch_texts: list[str]) -> None:
        batch = _Batch()
        try:
            result = await self._model.embed(tuple(batch_texts))
            if len(result.vectors) != len(batch_texts):
                raise ValueError("embedding provider returned the wrong number of vectors")
            batch.usage = result.usage
        except Exception as error:  # provider failures isolate to their own batch
            async with self._lock:
                for text in batch_texts:
                    future = self._waiters.pop(text, None)
                    if future is not None and not future.done():
                        future.set_exception(error)
            return

        async with self._lock:
            for text, vector in zip(batch_texts, result.vectors, strict=True):
                self._remember(text, vector)
                future = self._waiters.pop(text, None)
                if future is not None and not future.done():
                    future.set_result((vector, batch))

    def _remember(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _merge_usage(total: InferenceUsage, part: InferenceUsage | None) -> InferenceUsage:
    if part is None:
        return total
    return InferenceUsage(
        requests=total.requests + part.requests,
        input_tokens=_add(total.input_tokens, part.input_tokens),
        output_tokens=_add(total.output_tokens, part.output_tokens),
    )


def _add(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def _silence_unretrieved_error(future: asyncio.Future[object]) -> None:
    if future.cancelled():
        return
    if future.exception() is not None:
        return


__all__ = ["CoalescingEmbeddingModel"]
