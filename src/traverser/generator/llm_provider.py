"""LLM provider abstraction — supports OpenAI, Anthropic, and GitHub Copilot CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

import anthropic
import openai
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from traverser.config import Config, LLMProvider

logger = logging.getLogger(__name__)

# ── Retry helpers ─────────────────────────────────────────────────────────────

_MAX_ATTEMPTS = 5


def _openai_before_sleep(rs: RetryCallState) -> None:
    exc = rs.outcome.exception() if rs.outcome else None
    logger.warning(
        "OpenAI %s — %s — retrying (attempt %d/%d, next wait ~%.0fs)...",
        type(exc).__name__ if exc else "error",
        str(exc) if exc else "",
        rs.attempt_number,
        _MAX_ATTEMPTS,
        rs.next_action.sleep if rs.next_action else 0,
    )


def _anthropic_before_sleep(rs: RetryCallState) -> None:
    exc = rs.outcome.exception() if rs.outcome else None
    logger.warning(
        "Anthropic %s — %s — retrying (attempt %d/%d, next wait ~%.0fs)...",
        type(exc).__name__ if exc else "error",
        str(exc) if exc else "",
        rs.attempt_number,
        _MAX_ATTEMPTS,
        rs.next_action.sleep if rs.next_action else 0,
    )


# ── Providers ─────────────────────────────────────────────────────────────────


class LLMProviderProtocol(Protocol):
    """Minimal interface all LLM providers must implement."""

    async def complete(self, prompt: str) -> str:
        """Send *prompt* and return the model's response text."""
        ...

    @property
    def model_name(self) -> str:
        """Human-readable identifier for the model being used."""
        ...


class OpenAIProvider:
    """Async OpenAI chat completions provider."""

    def __init__(self, config: Config) -> None:
        api_key = config.require_api_key()
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            # Use a 60s timeout instead of the default 600s — avoids very long
            # hangs when the API is slow, and lets tenacity retry sooner.
            timeout=60.0,
            # Disable the SDK's built-in retry so tenacity has sole control.
            # Without this, a single tenacity attempt can internally make up to
            # 3 HTTP calls (SDK retries=2), turning 5 tenacity attempts into
            # up to 15 actual requests and greatly increasing rate-limit risk.
            max_retries=0,
        )
        self._model = config.llm_model
        self._max_tokens = config.llm_max_tokens
        self._temperature = config.llm_temperature

    @property
    def model_name(self) -> str:
        return self._model

    @staticmethod
    def _prefers_max_completion_tokens(model: str) -> bool:
        """Return True for model families that expect `max_completion_tokens`."""
        name = model.lower()
        return name.startswith(("gpt-5", "o1", "o3", "o4"))

    @retry(
        retry=retry_if_exception_type(
            (
                openai.RateLimitError,
                openai.APIConnectionError,  # includes APITimeoutError
                openai.InternalServerError,  # transient 500/503 from OpenAI
            )
        ),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        before_sleep=_openai_before_sleep,
        reraise=True,  # re-raise the original exception, not RetryError
    )
    async def complete(self, prompt: str) -> str:
        request_kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
        }

        if self._prefers_max_completion_tokens(self._model):
            request_kwargs["max_completion_tokens"] = self._max_tokens
        else:
            request_kwargs["max_tokens"] = self._max_tokens

        try:
            response = await self._client.chat.completions.create(**request_kwargs)
        except openai.BadRequestError as exc:
            # Some OpenAI models reject `max_tokens` and require
            # `max_completion_tokens` (and vice versa for older families).
            message = str(exc)
            if "max_completion_tokens" in message and "max_tokens" in request_kwargs:
                request_kwargs.pop("max_tokens", None)
                request_kwargs["max_completion_tokens"] = self._max_tokens
                response = await self._client.chat.completions.create(**request_kwargs)
            elif "max_tokens" in message and "max_completion_tokens" in request_kwargs:
                request_kwargs.pop("max_completion_tokens", None)
                request_kwargs["max_tokens"] = self._max_tokens
                response = await self._client.chat.completions.create(**request_kwargs)
            else:
                raise

        return response.choices[0].message.content or ""


class AnthropicProvider:
    """Async Anthropic messages provider."""

    def __init__(self, config: Config) -> None:
        api_key = config.require_api_key()
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=60.0,
            max_retries=0,
        )
        self._model = config.llm_model
        self._max_tokens = config.llm_max_tokens
        self._temperature = config.llm_temperature

    @property
    def model_name(self) -> str:
        return self._model

    @retry(
        retry=retry_if_exception_type(
            (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,  # includes APITimeoutError
                anthropic.InternalServerError,
            )
        ),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        before_sleep=_anthropic_before_sleep,
        reraise=True,
    )
    async def complete(self, prompt: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self._max_tokens,
        )
        # response.content is a list of ContentBlock
        return "".join(
            block.text for block in response.content if hasattr(block, "text")
        )


class GitHubCopilotProvider:
    """GitHub Copilot CLI provider — uses the standalone `copilot` binary.

    Requires the new GitHub Copilot CLI (https://github.com/github/copilot-cli)
    installed and authenticated.  Uses the **programmatic** interface
    (`copilot -p "..."`) so no interactive session is needed.

    Install:  brew install copilot-cli
    Auth:     copilot   (first-run login) or set GITHUB_TOKEN / GH_TOKEN
    """

    # Models supported by the new Copilot CLI (from --help)
    SUPPORTED_MODELS = (
        "claude-sonnet-4.6",
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
        "claude-opus-4.6",
        "claude-opus-4.6-fast",
        "claude-opus-4.5",
        "gpt-5.2",
        "gpt-4.1",
        "gpt-4o",
        "o3",
        "o4-mini",
    )

    def __init__(self, config: Config) -> None:
        self._copilot_bin = self._find_copilot()
        # Use the configured model if set, otherwise let copilot use its default
        self._model: str | None = (
            config.llm_model
            if config.llm_model and config.llm_model != "claude-3-5-sonnet-20241022"
            else None
        )

    @staticmethod
    def _find_copilot() -> str:
        """Locate a working standalone `copilot` binary.

        VS Code may inject an internal shim on PATH:
        .../github.copilot-chat/copilotCli/copilot
        That shim can return empty output in non-interactive subprocess usage.
        Prefer the standalone CLI (Homebrew/npm install) when available.
        """
        override = os.getenv("COPILOT_CLI_PATH")
        candidates: list[str] = []
        if override:
            candidates.append(override)

        which_path = shutil.which("copilot")
        if which_path:
            candidates.append(which_path)

        # Common standalone install locations on macOS
        candidates.extend([
            "/opt/homebrew/bin/copilot",
            "/usr/local/bin/copilot",
        ])

        existing: list[str] = []
        for c in candidates:
            p = Path(c).expanduser()
            if p.exists() and os.access(str(p), os.X_OK):
                existing.append(str(p))

        def is_vscode_shim(p: str) -> bool:
            return "github.copilot-chat/copilotCli/copilot" in p.replace("\\", "/")

        # Prefer non-shim candidate
        for p in existing:
            if not is_vscode_shim(p):
                return p

        # Fall back to shim only if that's all we have
        if existing:
            logger.warning(
                "Using VS Code Copilot shim (%s). If prompt mode returns empty output, "
                "install/use standalone Copilot CLI and set COPILOT_CLI_PATH.",
                existing[0],
            )
            return existing[0]

        if not which_path:
            raise ValueError(
                "GitHub Copilot CLI not found on $PATH. "
                "Install with: brew install copilot-cli  "
                "(see https://github.com/github/copilot-cli)"
            )
        return which_path

    @property
    def model_name(self) -> str:
        return self._model or "copilot-cli-default"

    async def complete(self, prompt: str) -> str:
        """Send *prompt* via `copilot -p` and return the response text."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._call_copilot, prompt)

    def _call_copilot(self, prompt: str) -> str:
        """Synchronous subprocess call to `copilot -p`."""
        cmd: list[str] = [
            self._copilot_bin,
            "-p", prompt,
            "--output-format", "text",
            # Deny all tool usage — we only want text generation, not
            # file edits or shell commands.
            "--deny-tool", "shell", "write",
        ]
        if self._model:
            cmd.extend(["--model", self._model])

        # Complex prompts (e.g., architecture for large repos) may need > 180s.
        # Use 600s (10 min) timeout for better reliability on slower/overloaded systems.
        timeout_sec = 600
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"GitHub Copilot CLI timed out after {timeout_sec} s "
                f"(prompt size: {len(prompt)} chars). Try: "
                f"(1) reducing max_content_chars in config; "
                f"(2) using --focus to restrict analysis; "
                f"(3) checking if copilot process is hanging."
            ) from exc
        except FileNotFoundError as exc:
            raise ValueError(
                "copilot binary disappeared — is copilot-cli still installed?"
            ) from exc

        if result.returncode != 0:
            logger.error(
                "Copilot CLI exit %d — stderr: %s",
                result.returncode,
                result.stderr.strip(),
            )
            raise ValueError(
                f"GitHub Copilot CLI failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
            )

        # stdout has the clean response; stderr has usage stats
        text = result.stdout.strip()
        if not text:
            raise ValueError(
                "GitHub Copilot CLI returned an empty response. "
                f"Active binary: {self._copilot_bin}. "
                "If this is the VS Code shim, use standalone CLI (e.g. /opt/homebrew/bin/copilot) "
                "or set COPILOT_CLI_PATH, then run `copilot` once interactively to verify auth."
            )
        return text


def build_provider(
    config: Config,
) -> OpenAIProvider | AnthropicProvider | GitHubCopilotProvider:
    """Factory — returns the configured LLM provider."""
    if config.llm_provider == LLMProvider.OPENAI:
        return OpenAIProvider(config)
    if config.llm_provider == LLMProvider.ANTHROPIC:
        return AnthropicProvider(config)
    if config.llm_provider == LLMProvider.GITHUB_COPILOT:
        return GitHubCopilotProvider(config)
    return AnthropicProvider(config)
