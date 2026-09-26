"""Optional reranking stage between vector search and context assembly.

The vector store returns cosine-similarity ordering. A reranker can re-order the
candidate pool so context ordering better matches lexical/query intent. This is
kept OPTIONAL and provider-agnostic:

    Reranker (ABC)
        ├── NoopReranker    (reranking disabled — pure FAISS order)
        └── HybridReranker  (offline deterministic blend of cosine + lexical overlap)

No model weights or network are required for the hybrid mode; it simply demotes
lexically-irrelevant high-vector hits. Swap in a cross-encoder later by
subclassing the same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.config import settings

RerankHit = tuple[str, float, str]  # (chunk_id, cosine_score, text)


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list[RerankHit]) -> list[RerankHit]:
        """Return the same hits re-ordered from most to least relevant."""


class NoopReranker(Reranker):
    def rerank(self, query: str, candidates: list[RerankHit]) -> list[RerankHit]:
        return candidates


# Character/word ngram tokens that survive punctuation and case.
def _tokens(text: str, n: int = 3) -> set[str]:
    lower = text.lower()
    return {
        lower[i : i + n]
        for i in range(max(0, len(lower) - n + 1))
        if not lower[i : i + n].isspace()
    }


class HybridReranker(Reranker):
    """Blend the vector cosine score with a query/context lexical-overlap bonus.

    score = w_cos * cosine + w_lex * lexical
    lexical = |query_ngrams ∩ chunk_ngrams| / |query_ngrams|
    Both components live on [0, 1]; documents close to the query's wording climb,
    which typically improves precision for extractive/short-fact questions.
    """

    def __init__(self, w_cos: float = 0.7, w_lex: float = 0.3) -> None:
        self.w_cos = w_cos
        self.w_lex = w_lex

    def rerank(self, query: str, candidates: list[RerankHit]) -> list[RerankHit]:
        if not candidates:
            return candidates
        q_tokens = _tokens(query)
        if not q_tokens:
            return candidates

        enriched: list[tuple[float, RerankHit]] = []
        for hit in candidates:
            chunk_id, cosine, text = hit
            overlap = len(q_tokens & _tokens(text)) / len(q_tokens)
            blended = self.w_cos * float(cosine) + self.w_lex * overlap
            enriched.append((blended, hit))
        # stable: ties keep the vector (original) order
        enriched.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in enriched]


_reranker: Reranker | None = None
_RESOLVED = False


def reset_reranker() -> None:
    global _reranker, _RESOLVED
    _reranker = None
    _RESOLVED = False


def get_reranker() -> Reranker | None:
    """Returns the configured reranker, or None when the stage is disabled."""
    global _reranker, _RESOLVED
    if _RESOLVED:
        return _reranker
    mode = settings.rerank_mode if settings.enable_reranking else "none"
    if mode == "hybrid":
        _reranker = HybridReranker()
    else:
        _reranker = None
    _RESOLVED = True
    return _reranker