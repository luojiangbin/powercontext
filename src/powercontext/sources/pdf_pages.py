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

"""Parse every physical PDF page into searchable page-level evidence."""

from __future__ import annotations

import hashlib
import io
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"


class PDFDocumentInput(BaseModel):
    """One original PDF file to split into physical pages."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a PDF, numbered from the first page."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured PDF page bound to the exact file revision it came from."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build a bounded, collision-free identity for one page revision."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    document_key = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:20]
    return f"pdf:{document_key}:{document_sha256[:20]}:{page_number}"


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Extract every physical page, failing atomically on any unreadable input."""

    document_id = value.document_id
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must not be empty or whitespace")
    if not value.content:
        raise ValueError("PDF content must not be empty")

    try:
        reader = PdfReader(io.BytesIO(value.content), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a user password")
        pages: list[PDFPageInput] = []
        digest = hashlib.sha256(value.content).hexdigest()
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                # 兜底：plain 模式提取为空时尝试 layout 模式，避免漏掉排版特殊页的可提取正文
                try:
                    text = page.extract_text(extraction_mode="layout") or text
                except Exception:
                    pass
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=digest,
                    page_number=index,
                    text=text.strip(),
                )
            )
    except ValueError:
        raise
    except Exception as error:  # pypdf raises several unrelated exception types
        raise ValueError(f"unreadable PDF: {error}") from error

    if not pages:
        raise ValueError("PDF must contain at least one physical page")
    return tuple(pages)


class PDFPageSourceAdapter:
    """Resolve and read one physical PDF page Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one physical page as document-prefixed text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


_SEARCH_STOPWORDS = frozenset(
    """
    a an and are as at be been by did do does for from had has have how in into is it its of on or that the their
    there these this to was were what when where which who why will with year years company companies total
    """.split()
)


def _search_terms(text: str) -> list[str]:
    import re

    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in _SEARCH_STOPWORDS]


def _token_matches(token: str, term: str) -> bool:
    """Exact match, plus prefix variants (increase matches increased/increases) for longer terms."""

    if token == term:
        return True
    return len(term) >= 5 and token.startswith(term)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Rank every stored page with BM25, phrase boost and document-prioritised allocation."""

    import math
    import re

    from powercontext.builtin.artifacts.memory.models import MemoryCitation

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be positive")

    entries_page = await memory.list()
    if not entries_page.entries:
        return ()

    terms = _search_terms(question)
    if not terms:
        return ()

    pages: list[tuple[MemoryCitation, str, str, list[str], int]] = []
    for record in entries_page.entries:
        entry = record.entry
        text = entry.text
        document_id, _, body = text.partition("\n")
        tokens = re.findall(r"[a-z0-9]+", body.lower())
        citation = MemoryCitation(
            memory_ref=record.memory_ref,
            entry_id=entry.entry_id,
            entry_version_id=entry.entry_version_id,
        )
        pages.append((citation, document_id, body, tokens, len(tokens)))

    total = len(pages)
    average_length = sum(length for *_, length in pages) / total or 1.0
    document_frequency: dict[str, int] = {}
    for term in terms:
        document_frequency[term] = sum(1 for *_, _, _, tokens, _ in pages if any(_token_matches(t, term) for t in tokens))

    k1, b = 1.5, 0.75
    scored: list[tuple[float, str, int, MemoryCitation]] = []
    for order, (citation, document_id, body, tokens, length) in enumerate(pages):
        score = 0.0
        for term in terms:
            occurrences = sum(1 for token in tokens if _token_matches(token, term))
            if not occurrences:
                continue
            idf = math.log(1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = occurrences + k1 * (1 - b + b * length / average_length)
            weight = 2.0 if term.isdigit() else 1.0
            score += weight * idf * occurrences * (k1 + 1) / denominator
        # 短语加权：相邻查询词在页内成短语出现时显著加分
        for left, right in zip(terms, terms[1:]):
            phrase_hits = body.count(f"{left} {right}")
            if phrase_hits:
                score += 0.8 * math.log1p(phrase_hits) * (2.0 if left.isdigit() or right.isdigit() else 1.0)
        scored.append((score, document_id, order, citation))

    scored.sort(key=lambda item: (-item[0], item[2]))

    # 文档优先分配：相关度最高的文档拿大头，其余文档按比例保底，避免单一文档霸榜
    document_rank: dict[str, float] = {}
    document_order: list[str] = []
    for _, document_id, _, _ in scored:
        if document_id not in document_rank:
            document_rank[document_id] = 0.0
            document_order.append(document_id)
    for score, document_id, _, _ in scored:
        document_rank[document_id] = max(document_rank[document_id], score)

    ranked_documents = sorted(document_order, key=lambda d: (-document_rank[d], document_order.index(d)))
    if len(ranked_documents) <= 1:
        return tuple(citation for _, _, _, citation in scored[:limit])

    primary_cap = max(1, math.ceil(limit * 0.6))
    secondary_cap = max(1, math.ceil(limit * 0.25))
    budget: dict[str, int] = {}
    remaining = limit
    for index, document_id in enumerate(ranked_documents):
        if index == 0:
            allowance = min(primary_cap, remaining)
        elif index == 1:
            allowance = min(secondary_cap, remaining)
        else:
            allowance = remaining // max(len(ranked_documents) - index, 1) or (1 if remaining else 0)
        budget[document_id] = allowance
        remaining -= allowance
        if remaining <= 0:
            break
    if remaining > 0:
        budget[ranked_documents[0]] = budget.get(ranked_documents[0], 0) + remaining

    picks: list[tuple[float, int, MemoryCitation]] = []
    for index, (score, document_id, order, citation) in enumerate(scored):
        if budget.get(document_id, 0) <= 0:
            continue
        budget[document_id] -= 1
        picks.append((score, index, citation))
        if len(picks) >= limit:
            break
    picks.sort(key=lambda item: (-item[0], item[1]))
    return tuple(citation for _, _, citation in picks)


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
