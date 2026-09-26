"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000

    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    # cheap, low-latency model for routing/classify, plan, titles, summaries,
    # follow-ups, claim-verification and repair — off the final-answer path.
    groq_fast_model: str = "llama-3.1-8b-instant"

    database_url: str = "sqlite:///./data/app.db"
    data_dir: str = "./data"

    search_provider: str = "duckduckgo"
    tavily_api_key: str = ""
    brave_api_key: str = ""
    serper_api_key: str = ""
    google_search_api_key: str = ""
    google_search_cx: str = ""
    max_search_results: int = 8
    max_page_size: int = 200000

    enable_web_search: bool = True
    enable_query_repair: bool = True  # typo correction (fuzzy + LLM retry on empty)
    enable_arxiv: bool = True
    enable_wikipedia: bool = True
    enable_calculator: bool = True
    enable_document_rag: bool = True
    enable_followups: bool = True
    enable_claim_verification: bool = True
    enable_cache: bool = True
    enable_python_analysis: bool = True
    python_sandbox: str = "docker"  # docker | restricted | disabled (auto-probed)
    cache_backend: str = "memory"
    redis_url: str = ""

    default_mode: str = "auto"
    # RESEARCH DEPTH tiers (spec): quick 1-3 searches, standard 3-6,
    # deep = query decomposition + multiple searches + source analysis + loops.
    # `standard` reuses research_max_iterations / research_max_sources below.
    research_quick_max_iterations: int = 1
    research_quick_max_sources: int = 3
    research_quick_max_searches: int = 3
    research_standard_max_searches: int = 6
    research_deep_max_iterations: int = 3
    research_deep_max_sources: int = 8
    research_deep_first_queries: int = 4
    research_deep_followup_queries: int = 3
    research_max_iterations: int = 2
    research_max_sources: int = 6

    secret_key: str = ""

    demo_mode: bool = False

    # RAG tuning
    chunk_size: int = 1200
    chunk_overlap: int = 150
    max_upload_size_mb: int = 20
    top_k_retrieval: int = 4
    # optional reranking stage between vector top-k and the context window
    enable_reranking: bool = True
    rerank_mode: str = "hybrid"  # none | hybrid (lexical+cross-encoder-free, offline)
    # embedding_provider: "local" (fastembed ONNX, free/offline) | "remote" | "mock" (demo)
    embedding_provider: str = "local"
    embedding_model_repo: str = "sentence-transformers/all-MiniLM-L6-v2"
    # remote (OpenAI-compatible) embedding endpoint — only used when embedding_provider="remote"
    embedding_remote_url: str = ""
    embedding_remote_model: str = "text-embedding-3-small"
    embedding_remote_api_key: str = ""

    max_history_messages: int = 20

    # long-term user memory (persisted profile facts, reused across conversations)
    memory_enabled: bool = True
    memory_max_facts: int = 20
    memory_conv_chars: int = 4000
    memory_context_chars: int = 2500

    # per-conversation rolling summaries (context management for long chats)
    enable_conversation_summaries: bool = True
    conversation_summary_every: int = 5     # re-summarize every N messages
    conversation_summary_min: int = 6      # first summary after this many messages

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def vector_index_path(self) -> Path:
        return self.data_path / "faiss.index"

    @property
    def is_demo(self) -> bool:
        return self.demo_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()