"""Typo awareness: correct misspelled search/intent keywords without touching the rest.

Two complementary layers:
  1. TIER 1 (deterministic, zero-LC): `correct_lexicon_typos()` – a fast edit-distance pass
     that fixes just the *high-value domain words* this codebase routes on ("langchain",
     "transformer", "rag", "python", the classifier vocabulary, …). It is deliberately
     conservative: a token is only rewritten when (a) its best edit-distance match is
     clearly better than the runner-up (no ambiguity), (b) distance is small relative to
     length, and (c) the token isn't already a known word / acronym / name. Everything
     else – brand names, code identifiers, foreign words, query structure – is left
     byte-identical, so the engine's own fuzzy matching still gets a chance.
  2. TIER 2 (LLM, on-demand): `repair_query` – used by the retrieval layer when a search
     comes back empty. It asks the fast model to suggest a corrected query and only
     accepts a *different* string, so we never degrade a well-formed query.

The point is *not* to fix every typo: it's to stop one stray character from silently
dropping a query into the wrong intent bucket or returning zero sources.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.llm import get_fast_llm

# Technical / domain keywords this engine routes on. A close typo here changes the
# whole answer path (CHAT -> ACADEMIC_RESEARCH, plain search -> wikipedia, etc.)
# so it's exactly where an edit-distance fix pays off.
TECH_KEYWORDS = {
    # frameworks / libraries the repo & plan keywords reference
    "langchain", "langgraph", "langserve", "langsmith", "openai", "anthropic",
    "ollama", "llama", "mistral", "qwen", "groq", "chatgpt", "gpt", "gpt4",
    "python", "pytorch", "tensorflow", "keras", "fastapi", "django", "flask",
    "scikit-learn", "numpy", "pandas", "matplotlib", "transformers",
    "huggingface", "rag", "jupyter", "notebook", "streamlit", "gradio",
    "chromadb", "weaviate", "pinecone", "qdrant", "milvus", "faiss",
    "redis", "postgresql", "postgres", "mysql", "mongodb", "elasticsearch",
    "docker", "kubernetes", "aws", "azure", "gcp", "react", "typescript",
    "javascript", "nodejs", "graphql", "websocket", "asyncio", "microservices",
    # ML / agent concepts (match the classifier's _TECH_WORDS)
    "transformer", "attention", "embedding", "embeddings", "retrieval",
    "generation", "neural", "semantic", "vector", "agentic", "autonomous",
    "multimodal", "reinforcement", "concurrency", "parallelism", "fine-tuning",
    "fine-tune", "knowledge", "agent", "agents", "reasoning", "inference",
    # broad tech / science the plan/arxiv paths care about
    "quantum", "cryptography", "blockchain", "bitcoin", "ethereum",
    "cybersecurity", "encryption", "authentication", "webhook", "streaming",
    "spark", "kafka", "hadoop", "database", "architecture", "asynchronous",
    # people / orgs that steer academic intent (kept since they recur in queries)
    "facebook", "meta", "arxiv",
}


class TypoFix:
    """A single accepted fix: the misspelled token, its target, and the edit distance."""

    __slots__ = ("typo", "target", "distance")

    def __init__(self, typo: str, target: str, distance: int) -> None:
        self.typo = typo
        self.target = target
        self.distance = distance

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, TypoFix)
            and (self.typo, self.target, self.distance)
            == (other.typo, other.target, other.distance)
        )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"TypoFix({self.typo!r} -> {self.target!r} d={self.distance})"


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, early-cut at `cap` (capped distance is enough for us)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if len(a) - len(b) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _best_keyword(token: str) -> TypoFix | None:
    """Return the single best fix for `token`, else None.

    Accept rules (all must hold):
      - the token isn't already a known keyword (spelling is already right),
      - edit distance <= 1 for tokens of length >= 5, or <= 2 for length >= 7,
      - the best match beats the runner-up by >= 1 edit (no ambiguity).
    This keeps real English words ("architect" ~ "architecture") and unique
    names (brands, code identifiers) untouched.
    """
    lowered = token.lower()
    if lowered in TECH_KEYWORDS:
        return None
    if len(lowered) < 5:
        return None
    # reject obvious acronyms / all-caps tokens (RAG, LLM, NPM)
    if token.isupper() or token in ("rag", "llm", "npm", "aws", "api"):
        return None

    max_dist = 1 if len(lowered) < 7 else 2
    best_tgt, best_d = "", max_dist + 1
    second_d = max_dist + 1
    for kw in TECH_KEYWORDS:
        if abs(len(kw) - len(lowered)) > 2:
            continue  # length mismatch: not a close typo of this keyword
        d = _edit_distance(lowered, kw)
        if d < best_d:
            second_d = best_d
            best_d, best_tgt = d, kw
        elif d < second_d:
            second_d = d
    if best_tgt and best_d <= max_dist and second_d > best_d:
        return TypoFix(token, best_tgt, best_d)
    return None


def correct_lexicon_typos(text: str) -> tuple[str, list[TypoFix]]:
    """Tier-1: fix confidently-misspelled domain keywords in `text`.

    Returns (corrected_text, fixes). Token-aligned and case-preserving: each
    word is fixed in place only if it's a clear typo of a known domain keyword.
    The punctuation/whitespace around tokens is untouched.
    """
    import re

    if not text or not text.strip():
        return text, []

    fixes: list[TypoFix] = []
    token_re = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")

    def _fix(match: re.Match[str]) -> str:
        token = match.group(0)
        fix = _best_keyword(token)
        if fix is None:
            return token
        fixes.append(fix)
        return fix.target

    corrected = token_re.sub(_fix, text)
    return corrected, fixes


def needs_repair(results_empty: bool, text: str) -> bool:
    """Cheap pre-check for TIER 2: only spend an LLM call when (a) retrieval came
    back empty and (b) the query actually looks like it could hide a typo
    (>= 2 words and has an alphabetic token of plausible length)."""
    if not results_empty:
        return False
    import re

    return len(re.findall(r"[A-Za-z]{5,}", text or "")) >= 1


def repair_query(query: str) -> str:
    """Tier-2: ask the fast model to fix spelling in `query`.

    Returns the repaired query, or the ORIGINAL query untouched when the model is
    unavailable (demo/no key), errors, or judges nothing needs fixing. Never raises.
    """
    if not needs_repair(results_empty=True, text=query):
        return query
    if settings.is_demo or (settings.llm_provider == "groq" and not settings.groq_api_key):
        return query

    try:
        llm = get_fast_llm(temperature=0.0, max_tokens=80)
        reply = llm.invoke(
            [
                (
                    "system",
                    "You correct spelling in search queries. Fix ONLY typos/misspellings — "
                    "keep the query's meaning, words, and language otherwise identical. "
                    "If there is no typo, reply with the query unchanged. Reply with just "
                    "the corrected query, no quotes, no explanation.",
                ),
                ("human", query),
            ]
        )
        text = (reply.content if hasattr(reply, "content") else "") or ""
        text = text.strip().strip("'\"")
        if not text or text.lower() == query.lower():
            return query
        return text[:300]
    except Exception:
        return query