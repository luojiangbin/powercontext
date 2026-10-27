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

"""HTTP cross-encoder reranking with batching and global stable selection."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score Memory candidates through one HTTP service and select globally."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("rerank endpoint must be a non-empty string")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Request every batch, then select by score with the original rank as tiebreaker."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")

        scores: dict[int, float] = {}
        requests = 0
        for offset in range(0, len(candidates), self._batch_size):
            batch_ranks = list(range(offset + 1, min(offset + self._batch_size, len(candidates)) + 1))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidates[rank - 1].text} for rank in batch_ranks
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            scores.update(_parse_scores(response.json(), batch_ranks))

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ranked[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(payload: Any, batch_ranks: list[int]) -> dict[int, float]:
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("rerank response must contain exactly one 'scores' field")
    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("rerank 'scores' must be a list")

    expected = set(batch_ranks)
    parsed: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("each rerank score must contain exactly 'rank' and 'score'")
        rank = entry["rank"]
        if not isinstance(rank, int) or isinstance(rank, bool) or rank not in expected:
            raise ValueError(f"rerank score rank {rank!r} is not part of this batch")
        if rank in parsed:
            raise ValueError(f"duplicate rerank score rank {rank!r}")
        score = entry["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("rerank score must be numeric")
        value = float(score)
        if not math.isfinite(value):
            raise ValueError("rerank score must be finite")
        parsed[rank] = value

    if set(parsed) != expected:
        raise ValueError("rerank response must cover every rank in the batch exactly once")
    return parsed


__all__ = ["DEFAULT_HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
