"""Citation management: numbering, deduplication, validation and markdown formatting.

Never invent citations — only sources actually retrieved are added here.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.models.schemas import Citation, Evidence

logger = get_logger("citations")

MARKER_RE = re.compile(r"\[(\d+(?:[,;\-\s]\d+)*)\]")

_QUALITY_RANK = {
    "official documentation": 1,
    "primary research": 2,
    "government": 3,
    "academic": 4,
    "major publication": 5,
    "company documentation": 6,
    "technical blog": 7,
    "wikipedia": 8,
    "community discussion": 9,
    "unknown": 10,
}

# Authority is what lets the answer distinguish Wikipedia from primary sources.
# Wikipedia is deliberately SECONDARY — encyclopedic background, not the final word.
_PRIMARY_KINDS = {"official documentation", "primary research", "government", "academic"}
_SECONDARY_KINDS = {"wikipedia", "major publication", "company documentation", "technical blog"}
_COMMUNITY_KINDS = {"community discussion"}


def source_authority(quality: str) -> str:
    """primary | secondary | community | unknown — independent of ranking."""
    if quality in _PRIMARY_KINDS:
        return "primary"
    if quality in _SECONDARY_KINDS:
        return "secondary"
    if quality in _COMMUNITY_KINDS:
        return "community"
    return "unknown"


def source_label(quality: str) -> str:
    """Human-readable label for the Sources section, e.g. 'encyclopedia (secondary)'."""
    authority = source_authority(quality)
    if quality == "user document":
        return "uploaded file (your content)"
    if quality == "wikipedia":
        return "encyclopedia (secondary reference)"
    if authority in ("primary", "community"):
        return f"{quality} (primary)" if authority == "primary" else f"{quality} (community)"
    if authority == "secondary":
        return f"{quality} (secondary)"
    return ""


def infer_quality(url: str, title: str = "", source: str = "") -> str:
    host = (urlparse(url).netloc or source or "").lower()
    text = f"{title} {url}".lower()
    if any(k in host for k in ("github.com", "langchain.com", "langgraph", "groq", "openai.com")) or "documentation" in text:
        return "official documentation"
    if any(k in host for k in ("help.salesforce.com", "support.microsoft.com", "knowledgebase.", "kb.", "help.", "intercom.help", "support.apple.com")):
        return "company documentation"
    if "arxiv.org" in host or "ieee" in host or "doi.org" in host or "acm.org" in host or "research" in host:
        return "academic"
    if any(k in host for k in (".gov", "edu")) or ".gov" in host:
        return "government" if ".gov" in host else "academic"
    if "wikipedia.org" in host or "wikimedia" in host:
        return "wikipedia"
    if "blog" in text or "medium.com" in host or "substack" in host or "dev.to" in host or "hashnode" in host:
        return "technical blog"
    if any(k in host for k in ("stackoverflow.com", "reddit.com", "forum", "discuss")):
        return "community discussion"
    if any(k in host for k in ("nytimes.com", "bbc.com", "cnn.com", "reuters.com", "theguardian.com", "wired.com")):
        return "major publication"
    return "unknown"


def dedupe_evidence(evidence: list[Evidence]) -> list[Evidence]:
    seen: dict[str, Evidence] = {}
    for e in evidence:
        key = e.url.rstrip("/") if e.url else e.title
        if not key:
            continue
        if key in seen:
            merged = seen[key]
            if len(e.relevant_text) > len(merged.relevant_text):
                seen[key] = e
            continue
        seen[key] = e
    return list(seen.values())


def rank_evidence(evidence: list[Evidence]) -> list[Evidence]:
    ranked = sorted(
        evidence,
        key=lambda e: (
            _QUALITY_RANK.get(e.source_quality, 10),
            -e.confidence,
        ),
    )
    return ranked


def build_citations(evidence: list[Evidence]) -> list[Citation]:
    return [
        Citation(
            index=i + 1,
            url=e.url,
            title=e.title or e.url,
            source=e.source_type,
            quality=e.source_quality,
        )
        for i, e in enumerate(dedupe_evidence(rank_evidence(evidence)))
    ]


def build_document_citations(retrieved: list) -> list[Citation]:
    """Numbered citations for uploaded-document chunks.

    The answer's [n] markers stay numeric and consistent with the web sources,
    and each entry surfaces the file + page/chunk so a claim can be traced back.
    """
    cites: list[Citation] = []
    for i, chunk in enumerate(retrieved, 1):
        if not getattr(chunk, "text", ""):
            continue
        filename = getattr(chunk, "filename", "") or getattr(chunk, "document_title", "") or "document"
        cites.append(
            Citation(
                index=i,
                url="",
                title=filename,
                source="document",
                quality="user document",
                page=int(getattr(chunk, "page", 0) or 0),
                chunk_id=getattr(chunk, "chunk_id", ""),
            )
        )
    return cites


def build_citation_block(citations: list[Citation]) -> str:
    if not citations:
        return ""
    lines = ["", "## Sources"]
    for c in citations:
        title = (c.title or c.url).strip()
        url = c.url
        label = source_label(c.quality)
        if c.source == "document":
            # uploaded-file citation: reference the document + page/chunk
            loc = f"page {c.page}" if c.page else "document"
            if c.chunk_id:
                loc += f" · chunk {str(c.chunk_id)[-6:]}"
            lines.append(f"[{c.index}] {title}")
            lines.append(f"    {loc}")
        elif title and url and title != url:
            lines.append(f"[{c.index}] {title}")
            lines.append(f"    {url}")
        else:
            lines.append(f"[{c.index}] {url}")
        if label:
            lines.append(f"    Type: {label}")
    return "\n".join(lines)


def validate_markers(text: str, max_index: int) -> set[int]:
    """Returns the set of cited indices that exceed available sources (must not exist)."""
    invalid: set[int] = set()
    for match in MARKER_RE.finditer(text):
        for part in match.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    for n in range(int(a), int(b) + 1):
                        if not 1 <= n <= max_index:
                            invalid.add(n)
                except ValueError:
                    continue
            else:
                try:
                    n = int(part)
                except ValueError:
                    continue
                if not 1 <= n <= max_index:
                    invalid.add(n)
    return invalid


def attach_sources_section(answer: str, citations: list[Citation]) -> str:
    """Appends the Sources block when there are citations, avoiding duplication."""
    if not citations:
        return answer
    if "## Sources" in answer:
        return answer
    return f"{answer.rstrip()}\n\n{build_citation_block(citations).strip()}"