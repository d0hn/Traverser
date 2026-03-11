"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GITHUB_COPILOT = "github-copilot"


class Config(BaseSettings):
    """
    All settings are read from environment variables (case-insensitive).
    A `.env` file in the working directory is loaded automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str | None = Field(
        default=None, description="GitHub Personal Access Token"
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    llm_provider: LLMProvider = Field(
        default=LLMProvider.OPENAI, description="openai | anthropic"
    )
    llm_model: str = Field(default="gpt-4o", description="Model identifier")
    llm_max_tokens: int = Field(default=4096, ge=256, le=16384)
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)

    # ── API keys ──────────────────────────────────────────────────────────────
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # ── Concurrency ───────────────────────────────────────────────────────────
    max_concurrent_llm_requests: int = Field(default=5, ge=1, le=50)

    # ── Processing limits ────────────────────────────────────────────────────
    max_file_size_kb: int = Field(
        default=500, ge=1, description="Skip files larger than this (KB)"
    )
    # Max characters of file content sent to the LLM (prevents huge context windows)
    max_content_chars: int = Field(default=80_000)

    # ── Paths ─────────────────────────────────────────────────────────────────
    output_dir: Path = Field(default=Path("output"))
    cache_dir: Path = Field(default=Path(".cache/traverser"))
    cache_enabled: bool = Field(default=True)

    # ── File filtering ────────────────────────────────────────────────────────
    skip_directories: list[str] = Field(
        default=[
            "node_modules",
            "__pycache__",
            ".git",
            ".tox",
            ".venv",
            "venv",
            "env",
            "dist",
            "build",
            ".next",
            ".nuxt",
            ".svelte-kit",
            "coverage",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "target",  # Java / Rust
            ".gradle",
            "vendor",  # Go / PHP
            "tmp",
            "temp",
        ]
    )

    binary_extensions: list[str] = Field(
        default=[
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".bmp",
            ".webp",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".zip",
            ".tar",
            ".gz",
            ".bz2",
            ".rar",
            ".7z",
            ".exe",
            ".bin",
            ".so",
            ".dylib",
            ".dll",
            ".a",
            ".o",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
            ".wav",
            ".ogg",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".otf",
            ".pyc",
            ".pyo",
            ".pyd",
            ".class",
            ".jar",
            ".war",
            ".lock",
        ]
    )

    @model_validator(mode="after")
    def _check_api_key_present(self) -> "Config":
        """Warn if the selected provider has no API key — fail at use time, not import time."""
        return self

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def binary_extensions_set(self) -> frozenset[str]:
        return frozenset(self.binary_extensions)

    @property
    def skip_directories_set(self) -> frozenset[str]:
        return frozenset(self.skip_directories)

    @property
    def active_api_key(self) -> str | None:
        if self.llm_provider == LLMProvider.GITHUB_COPILOT:
            return None  # Copilot CLI authenticates via `copilot` login / GITHUB_TOKEN
        return (
            self.openai_api_key
            if self.llm_provider == LLMProvider.OPENAI
            else self.anthropic_api_key
        )

    def require_api_key(self) -> str:
        if self.llm_provider == LLMProvider.GITHUB_COPILOT:
            return "copilot-cli"  # no explicit key needed
        key = self.active_api_key
        if not key:
            provider = self.llm_provider.value.upper()
            var = "OPENAI_API_KEY" if self.llm_provider == LLMProvider.OPENAI else "ANTHROPIC_API_KEY"
            raise ValueError(
                f"{provider} API key is required. "
                f"Set {var} in your environment or .env file."
            )
        return key
