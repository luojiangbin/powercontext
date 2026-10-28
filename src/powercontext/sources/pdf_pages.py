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

    terms = [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1]
    return [token for token in terms if token not in _SEARCH_STOPWORDS]


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


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Rank every stored page against the question with a BM25-style lexical score."""

    import math

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

    documents: list[tuple[MemoryCitation, str, int]] = []
    for record in entries_page.entries:
        entry = record.entry
        text = entry.text.lower()
        citation = MemoryCitation(
            memory_ref=record.memory_ref,
            entry_id=entry.entry_id,
            entry_version_id=entry.entry_version_id,
        )
        documents.append((citation, text, len(text.split())))

    total = len(documents)
    average_length = sum(length for _, _, length in documents) / max(total, 1) or 1.0
    document_frequency: dict[str, int] = {}
    for term in terms:
        document_frequency[term] = sum(1 for _, text, _ in documents if term in text)

    k1, b = 1.5, 0.75
    scored: list[tuple[float, int, MemoryCitation]] = []
    for order, (citation, text, length) in enumerate(documents):
        score = 0.0
        for term in terms:
            occurrences = text.count(term)
            if not occurrences:
                continue
            idf = math.log(1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = occurrences + k1 * (1 - b + b * length / average_length)
            weight = 2.0 if term.isdigit() else 1.0
            score += weight * idf * occurrences * (k1 + 1) / denominator
        scored.append((score, order, citation))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(citation for _, _, citation in scored[:limit])


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
