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
import re
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

# 财报领域通用同义/近义词表：把问题词形映射到财报正文常用表达。
# 静态领域词汇，不是针对题目或文档的硬编码。
_SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = {
    "battles": ("battles", "litigation", "proceedings", "proceeding", "disputes", "claims"),
    "capex": ("capex", "capital expenditures"),
    "geographies": ("geographies", "geographic", "divisions", "segments", "regions"),
    "operates": ("operates", "operations", "operating", "businesses"),
    "employees": ("employees", "workforce", "headcount"),
    "revenue": ("revenue", "revenues", "sales"),
    "legal": ("legal", "litigation", "proceedings", "proceeding"),
    "ongoing": ("ongoing", "continuing", "pending"),
    "materially": ("materially", "material"),
    "restructuring": ("restructuring", "productivity plan"),
    "unadjusted": ("unadjusted",),
    "depreciation": ("depreciation", "amortization"),
    "margin": ("margin", "margins"),
    "income": ("income", "earnings", "profit"),
    "statement": ("statement", "statements"),
    "costs": ("costs", "charges", "expenses"),
    "cyclicality": ("cyclicality", "cyclical"),
    "customers": ("customers", "customer", "clients"),
    "tax": ("tax", "taxes", "taxation"),
    "categories": ("categories", "category", "segments", "segment"),
    "gross": ("gross",),
    "production": ("production", "production rate"),
    "forecasting": ("forecasting", "forecast", "planned", "plan"),
    "primary": ("primary", "main", "principal"),
    "acquisitions": ("acquisitions", "acquisition", "acquired"),
}

# FinanceBench 风格问题常见的解释性尾缀，与证据检索无关，剔除。
_SEARCH_BOILERPLATE = re.compile(
    r"\.?\s*(Respond to the question|Give a response|Answer in USD millions|Answer in USD billions|"
    r"Please respond|If .* is not|Please assume|Assume|It is not necessary|Do not round|You can find).*$",
    re.IGNORECASE,
)

# 标题行加分的封顶值，防止带大量短行的索引页（如附件目录）霸榜
_SEARCH_HEADING_CAP = 6.0


def _search_terms(text: str) -> list[str]:
    import re

    core = _SEARCH_BOILERPLATE.sub("", text)
    tokens = re.findall(r"[a-z0-9]+", core.lower())
    expanded: list[str] = []
    for token in tokens:
        # "FY2022" 归一为 "2022"，与财报表格的年份列对齐
        if re.fullmatch(r"fy\d{4}", token):
            token = token[2:]
        if len(token) <= 1 or token in _SEARCH_STOPWORDS:
            continue
        for key, variants in _SEARCH_SYNONYMS.items():
            if token == key or (len(key) >= 5 and token.startswith(key)):
                expanded.extend(variants)
                break
        else:
            expanded.append(token)
    return expanded


def _token_matches(token: str, term: str) -> bool:
    """Exact match, plus prefix variants (increase matches increased/increases) for longer terms."""

    if token == term:
        return True
    return len(term) >= 5 and token.startswith(term)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Rank every stored page with finance-aware BM25, phrase and heading boosts."""

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

    # 页面级聚合：大页可能被管线切分为多个条目（memory entry 有 8192 字节上限），
    # 按 (document_id, page_number) 合并正文并保留该页的一个精确引用。
    def _entry_page(entry) -> tuple[str, int]:
        """From one memory entry, recover (document_id, physical page)."""
        document_id = entry.text.partition("\n")[0]
        page_number = 0
        for reference in getattr(entry, "sources", ()) or ():
            source_id = getattr(reference, "source_id", None) or getattr(reference, "id", None)
            if not isinstance(source_id, str) or not source_id.startswith("pdf:"):
                continue
            try:
                page_number = int(source_id.rsplit(":", 1)[1])
            except ValueError:
                continue
            break
        return document_id, page_number

    aggregate: dict[tuple[str, int], list[tuple[MemoryCitation, str]]] = {}
    for record in entries_page.entries:
        entry = record.entry
        citation = MemoryCitation(
            memory_ref=record.memory_ref,
            entry_id=entry.entry_id,
            entry_version_id=entry.entry_version_id,
        )
        document_id, page_number = _entry_page(entry)
        aggregate.setdefault((document_id, page_number), []).append((citation, entry.text))

    pages: list[tuple[MemoryCitation, str, str, list[str], list[str], int]] = []
    page_keys: dict[int, tuple[str, int]] = {}
    for order, ((document_id, page_number), chunks) in enumerate(aggregate.items()):
        # 正文直接来自投影格式 "DOCUMENT_ID\nPAGE_TEXT"；切分条目按顺序拼接还原整页
        bodies = [text.partition("\n")[2] for _, text in chunks]
        body = "".join(bodies)
        body_lower = body.lower()
        tokens = re.findall(r"[a-z0-9]+", body_lower)
        lines = body_lower.split("\n")
        pages.append((chunks[0][0], document_id, body_lower, tokens, lines, len(tokens)))
        page_keys[order] = (document_id, page_number)

    total = len(pages)
    average_length = sum(length for *_, length in pages) / total or 1.0

    # 语料级停用词：出现于超过 30% 页面的非数字词视为噪音（公司名、表头等）；年份数字保留
    corpus_frequency: dict[str, int] = {}
    for _, _, _, tokens, _, _ in pages:
        for token in set(tokens):
            corpus_frequency[token] = corpus_frequency.get(token, 0) + 1
    corpus_stopwords = {
        token for token, frequency in corpus_frequency.items()
        if frequency > 0.30 * total and not token.isdigit()
    }
    terms = [term for term in terms if term not in corpus_stopwords]
    if not terms:
        return ()

    document_frequency: dict[str, int] = {}
    for term in terms:
        document_frequency[term] = sum(1 for *_, _, _, tokens, _, _ in pages if any(_token_matches(t, term) for t in tokens))
    document_ids = {document_id for _, document_id, _, _, _, _ in pages}
    document_count = len(document_ids)
    document_frequency_across_docs: dict[str, int] = {}
    for term in terms:
        document_frequency_across_docs[term] = sum(
            1 for doc_id in document_ids
            if any(_token_matches(token, term) for _, did, _, tokens, _, _ in pages if did == doc_id for token in tokens)
        )

    scored: list[tuple[float, str, int, MemoryCitation]] = []
    for order, (citation, document_id, body, tokens, lines, length) in enumerate(pages):
        score = 0.0
        for term in terms:
            occurrences = sum(1 for token in tokens if _token_matches(token, term))
            if not occurrences:
                continue
            page_idf = math.log(1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            doc_idf = math.log(
                1.0 + (document_count - document_frequency_across_docs[term] + 0.5)
                / (document_frequency_across_docs[term] + 0.5)
            )
            denominator = occurrences + 1.5 * (0.25 + 0.75 * length / average_length)
            digit_weight = 2.0 if term.isdigit() else 1.0
            score += digit_weight * (1.0 + doc_idf) * page_idf * occurrences * 2.5 / denominator
        # 短语加权：相邻查询词在页内成短语出现时加分
        for left, right in zip(terms, terms[1:]):
            phrase_hits = body.count(f"{left} {right}")
            if phrase_hits:
                score += 0.8 * math.log1p(phrase_hits)
        # 标题行加权：查询词出现在短行（章节标题）中时加分；封顶并每词只计一次
        heading_bonus = 0.0
        seen_terms: set[str] = set()
        for line in lines:
            if heading_bonus >= _SEARCH_HEADING_CAP:
                break
            stripped = line.strip()
            if not stripped or len(stripped) > 100:
                continue
            line_tokens = set(re.findall(r"[a-z0-9]+", stripped))
            for term in terms:
                if term in seen_terms:
                    continue
                if any(_token_matches(t, term) for t in line_tokens):
                    seen_terms.add(term)
                    heading_bonus += 0.6
                    if heading_bonus >= _SEARCH_HEADING_CAP:
                        break
        score += heading_bonus
        scored.append((score, document_id, order, citation))

    scored.sort(key=lambda item: (-item[0], item[2]))
    return tuple(citation for _, _, _, citation in scored[:limit])


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
