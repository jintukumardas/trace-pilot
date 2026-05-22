"""Centralized, environment-driven configuration.

All services import ``get_settings()``. Values come from environment variables
(see ``.env.example``). The cache makes ``Settings`` effectively a singleton.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the whole platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: Literal["local", "dev", "prod"] = "local"
    log_level: str = "INFO"
    log_json: bool = False

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # --- Storage / metadata ---
    data_dir: str = Field(default="./.tracepilot", description="Root for metadata DB + clones")
    workspaces_dir: str = Field(
        default="./.tracepilot/workspaces", description="Where repos are cloned/mounted"
    )
    database_url: str = Field(default="sqlite:///./.tracepilot/tracepilot.db")

    # --- Redis (cache + job state) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Qdrant (vectors) ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "tracepilot_chunks"

    # --- Ollama (models) ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_gen_model: str = "llama3.1:8b"
    ollama_reasoning_model: str = "qwen2.5-coder:7b"
    ollama_embed_model: str = "nomic-embed-text"
    model_temperature: float = 0.1
    model_num_ctx: int = 8192
    request_timeout_seconds: int = 120

    # --- Embeddings ---
    embedding_provider: Literal["fastembed", "ollama"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # --- Langfuse (observability + evals) ---
    langfuse_enabled: bool = True
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # --- Retrieval ---
    retrieval_top_k: int = 8
    hybrid_alpha: float = Field(default=0.6, description="Weight of dense vs sparse in fusion (0..1)")
    rerank_enabled: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    max_context_chars: int = 16000

    # --- Tooling sandbox ---
    tool_timeout_seconds: int = 30
    tool_max_output_bytes: int = 64_000
    tool_allowlist: str = Field(default="", description="Comma-separated extra allowed path prefixes")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def tool_allowlist_paths(self) -> list[str]:
        return [p.strip() for p in self.tool_allowlist.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
