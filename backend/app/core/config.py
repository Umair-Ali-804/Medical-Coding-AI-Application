"""Application settings.

All configuration comes from environment variables (12-factor). Secrets are
never given usable production defaults: in `production` the app refuses to
start if they are missing or left at development values.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]

DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me-0123456789"  # noqa: S105
# Fernet key used only in development/test. Production must provide ENCRYPTION_KEYS.
DEV_ENCRYPTION_KEY = "ZGV2LW9ubHktZW5jcnlwdGlvbi1rZXktMzJieXRlcyE="  # noqa: S105


class Settings(BaseSettings):
    # env_ignore_empty: "KEY=" lines in .env mean "use the default", not "empty string"
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # --- General -----------------------------------------------------------
    app_name: str = "Medical Coding AI"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])
    pipeline_version: str = "1.1.0"  # 1.1: guideline coder rules, injury phrases, soft-wrap sentences

    # --- Database ------------------------------------------------------------
    database_url: str = "postgresql+psycopg://medcoding:medcoding@localhost:5432/medcoding"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # --- Security ------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480
    # Comma-separated Fernet keys. First key encrypts; all keys decrypt (rotation).
    encryption_keys: SecretStr = SecretStr(DEV_ENCRYPTION_KEY)
    login_rate_limit_per_minute: int = 10
    max_upload_mb: int = 20
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    # --- Storage -------------------------------------------------------------
    storage_dir: Path = Path("./var/storage")

    # --- LLM (OpenRouter, OpenAI-compatible) ---------------------------------
    llm_provider: Literal["openrouter", "heuristic"] = "heuristic"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "anthropic/claude-sonnet-4.5"
    # Optional second model used for agreement scoring (empty = disabled)
    llm_secondary_model: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4000
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 3
    llm_use_json_schema: bool = True
    # USD per 1M tokens, used when the provider does not return cost
    llm_price_input_per_m: float = 3.0
    llm_price_output_per_m: float = 15.0
    llm_extraction_enabled: bool = True  # use the LLM for entity extraction (plus deterministic ConText)
    http_referer: str = "https://github.com/medical-coding-ai"

    # --- Embeddings / RAG ----------------------------------------------------
    embedding_provider: Literal["fastembed", "sentence_transformers", "openai", "hash"] = "fastembed"
    embedding_device: str | None = None  # sentence_transformers: "cuda" | "cpu" (default: auto)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_cache_dir: str | None = None  # baked into the docker image at build time
    embedding_api_base: str | None = None
    embedding_api_key: SecretStr | None = None
    vector_store: Literal["qdrant", "memory", "disk"] = "qdrant"
    vector_dir: Path = Path("./var/vectors")  # vector_store=disk
    qdrant_url: str | None = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_path: str | None = None  # embedded/local mode (dev & tests)
    qdrant_collection_prefix: str = "codes"
    retrieval_top_k: int = 8
    retrieval_dense_k: int = 25
    retrieval_lexical_k: int = 40
    retrieval_rrf_k: int = 60

    # --- Knowledge base ------------------------------------------------------
    icd10cm_version: str = "2026"
    hcpcs_version: str | None = None
    cpt_version: str | None = None  # set only if a licensed CPT file was loaded
    enabled_code_systems: CsvList = Field(default_factory=lambda: ["ICD-10-CM"])

    # --- Confidence / routing ------------------------------------------------
    confidence_threshold: float = 0.80
    calibration_file: Path | None = None
    encounter_type_default: Literal["outpatient", "inpatient"] = "outpatient"

    # --- Integrations --------------------------------------------------------
    webhook_url: str | None = None  # e.g. n8n webhook receiving platform events
    webhook_secret: SecretStr | None = None
    webhook_timeout_s: float = 10.0
    # Hosts allowed as per-request callback URLs (SSRF protection). Empty = callbacks disabled.
    callback_allowed_hosts: CsvList = Field(default_factory=list)

    # --- Worker --------------------------------------------------------------
    worker_poll_interval_s: float = 2.0
    worker_concurrency: int = 2
    job_max_attempts: int = 3
    job_lock_timeout_s: int = 900

    @field_validator("cors_origins", "enabled_code_systems", "callback_allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            if v.strip().startswith("["):
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _check_production_secrets(self) -> Settings:
        if self.environment == "production":
            problems = []
            if (
                self.jwt_secret.get_secret_value() == DEV_JWT_SECRET
                or len(self.jwt_secret.get_secret_value()) < 32
            ):
                problems.append("JWT_SECRET must be set to a random value of >= 32 chars")
            if DEV_ENCRYPTION_KEY in self.encryption_keys.get_secret_value():
                problems.append("ENCRYPTION_KEYS must be set (generate with `python -m app.cli gen-key`)")
            if self.llm_provider == "openrouter" and not self.openrouter_api_key:
                problems.append("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
            if "*" in self.cors_origins:
                problems.append("CORS_ORIGINS must not contain '*' in production")
            if problems:
                raise ValueError("Insecure production configuration: " + "; ".join(problems))
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def retrieval_version(self) -> str:
        return f"hybrid-rrf-v1:{self.embedding_provider}:{self.embedding_model}:k{self.retrieval_top_k}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
