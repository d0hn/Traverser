"""LLM-powered documentation generator with disk-based caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable

import diskcache

from traverser.config import Config
from traverser.generator.llm_provider import OpenAIProvider, AnthropicProvider, build_provider
from traverser.generator.prompts import (
    build_architecture_prompt,
    build_batch_brief_doc_prompt,
    build_brief_file_doc_prompt,
    build_copilot_instructions_prompt,
    build_debugging_guide_prompt,
    build_file_doc_prompt,
    build_glossary_prompt,
    build_mindmap_prompt,
    build_summary_prompt,
    build_tests_overview_prompt,
)
from traverser.models.doc_models import (
    DocTier,
    FileAnalysis,
    FileDocumentation,
    KnowledgeBase,
    ProjectRelationships,
)
from traverser.models.repo_models import FileNode, RepoInfo

logger = logging.getLogger(__name__)

# Increment this when prompts change significantly to bust old caches
_CACHE_VERSION = "v2"

# Maximum number of BRIEF files bundled into a single LLM call
_BRIEF_BATCH_SIZE = 8


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _file_doc_cache_key(sha: str, model: str) -> str:
    return f"file_doc:{_CACHE_VERSION}:{sha}:{model}"


def _project_cache_key(kind: str, repo_full: str, model: str, content_hash: str) -> str:
    return f"{kind}:{_CACHE_VERSION}:{repo_full}:{model}:{content_hash}"


class DocGenerator:
    """Generates LLM-powered documentation for a repository."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._provider = build_provider(config)
        self._semaphore = asyncio.Semaphore(config.max_concurrent_llm_requests)
        self._cache: diskcache.Cache | None = None
        if config.cache_enabled:
            cache_dir = Path(config.cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache = diskcache.Cache(str(cache_dir))
            logger.info("Disk cache enabled at %s", cache_dir)

    def close(self) -> None:
        """Close the disk cache (frees sqlite connection)."""
        if self._cache is not None:
            self._cache.close()
            self._cache = None

    def __enter__(self) -> "DocGenerator":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _cache_get(self, key: str) -> str | None:
        if self._cache is None:
            return None
        return self._cache.get(key)  # type: ignore[return-value]

    def _cache_set(self, key: str, value: str) -> None:
        if self._cache is not None:
            self._cache.set(key, value, expire=60 * 60 * 24 * 30)  # 30 days

    async def _call_llm(self, prompt: str, cache_key: str) -> str:
        """Call the LLM with caching and rate-limit semaphore."""
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("Cache hit: %s", cache_key)
            return cached

        async with self._semaphore:
            result = await self._provider.complete(prompt)

        self._cache_set(cache_key, result)
        return result

    # ── File documentation ────────────────────────────────────────────────────

    async def generate_file_doc(
        self,
        file: FileNode,
        analysis: FileAnalysis,
        relationships: ProjectRelationships,
    ) -> FileDocumentation:
        """Generate documentation for a single file, respecting its DocTier."""
        hub_set = set(relationships.hub_files)
        entry_set = set(relationships.entry_points)
        tier = _classify_doc_tier(file, analysis, hub_set, entry_set)

        if tier == DocTier.SKIP:
            return _build_skip_doc(file, analysis)

        # Truncate content if needed to avoid huge contexts
        content = file.content[: self.config.max_content_chars]
        if len(file.content) > self.config.max_content_chars:
            content += f"\n\n... [truncated — showing first {self.config.max_content_chars} chars of {len(file.content)}]"

        imports_from = relationships.imports_from.get(file.path, [])
        imported_by = relationships.imported_by.get(file.path, [])
        static = analysis.to_summary_text()

        if tier == DocTier.BRIEF:
            cache_key = _file_doc_cache_key(file.sha + ":brief", self._provider.model_name)
            prompt = build_brief_file_doc_prompt(
                path=file.path,
                language=file.language.value,
                line_count=file.line_count,
                content=content,
                static_analysis=static,
                imports_from=imports_from,
                imported_by=imported_by,
            )
        else:  # FULL
            cache_key = _file_doc_cache_key(file.sha, self._provider.model_name)
            prompt = build_file_doc_prompt(
                path=file.path,
                language=file.language.value,
                line_count=file.line_count,
                content=content,
                static_analysis=static,
                imports_from=imports_from,
                imported_by=imported_by,
            )

        raw = await self._call_llm(prompt, cache_key)
        return _parse_file_doc(raw, file, self._provider.model_name, tier)

    # ── Batch BRIEF documentation ─────────────────────────────────────────────

    async def generate_brief_batch(
        self,
        files: list[FileNode],
        analyses: dict[str, FileAnalysis],
        relationships: ProjectRelationships,
        on_batch_done: Callable[[int], None] | None = None,
    ) -> dict[str, FileDocumentation]:
        """Generate BRIEF docs for many files using batched LLM calls.

        Files whose results are already cached are returned instantly.
        Uncached files are grouped into batches of *_BRIEF_BATCH_SIZE* and
        each batch is sent as a single LLM call.  Per-file sections of the
        response are cached individually so future runs still benefit.
        """
        if not files:
            return {}

        results: dict[str, FileDocumentation] = {}
        uncached: list[FileNode] = []

        # 1. Return cached results immediately
        for file in files:
            cache_key = _file_doc_cache_key(file.sha + ":brief", self._provider.model_name)
            cached = self._cache_get(cache_key)
            if cached is not None:
                results[file.path] = _parse_file_doc(
                    cached, file, self._provider.model_name, DocTier.BRIEF
                )
            else:
                uncached.append(file)

        cached_count = len(files) - len(uncached)
        if on_batch_done and cached_count:
            on_batch_done(cached_count)

        # 2. Batch uncached files into combined LLM calls
        for i in range(0, len(uncached), _BRIEF_BATCH_SIZE):
            batch = uncached[i : i + _BRIEF_BATCH_SIZE]
            file_blocks: list[dict[str, object]] = []
            for file in batch:
                content = file.content[: self.config.max_content_chars]
                analysis = analyses.get(file.path)
                if analysis is None:
                    continue
                file_blocks.append(
                    {
                        "path": file.path,
                        "language": file.language.value,
                        "line_count": file.line_count,
                        "content": content,
                        "static_analysis": analysis.to_summary_text(),
                        "imports_from": relationships.imports_from.get(file.path, []),
                        "imported_by": relationships.imported_by.get(file.path, []),
                    }
                )

            if not file_blocks:
                continue

            prompt = build_batch_brief_doc_prompt(file_blocks)

            async with self._semaphore:
                raw = await self._provider.complete(prompt)

            # 3. Split combined response into per-file sections and cache each
            file_sections = _split_batch_response(raw, [f.path for f in batch])
            for file, section_text in zip(batch, file_sections):
                cache_key = _file_doc_cache_key(
                    file.sha + ":brief", self._provider.model_name
                )
                self._cache_set(cache_key, section_text)
                results[file.path] = _parse_file_doc(
                    section_text, file, self._provider.model_name, DocTier.BRIEF
                )

            if on_batch_done:
                on_batch_done(len(batch))

        return results

    # ── Architecture overview ─────────────────────────────────────────────────

    async def generate_architecture(
        self,
        repo_info: RepoInfo,
        analyses: dict[str, FileAnalysis],
        relationships: ProjectRelationships,
        file_docs: dict[str, FileDocumentation],
    ) -> str:
        summaries = _build_file_summaries(file_docs, max_files=80)
        prompt = build_architecture_prompt(
            repo_name=repo_info.display_name,
            description=repo_info.description or "",
            languages=repo_info.languages_used,
            total_files=len(repo_info.files),
            analysed_files=len(analyses),
            hub_files=relationships.hub_files,
            entry_points=relationships.entry_points,
            circular_deps=relationships.circular_deps,
            file_summaries=summaries,
        )
        cache_key = _project_cache_key(
            "arch", repo_info.full_name, self._provider.model_name, _content_hash(summaries)
        )
        return await self._call_llm(prompt, cache_key)

    # ── Mind map ──────────────────────────────────────────────────────────────

    async def generate_mind_map(self, repo_info: RepoInfo) -> str:
        file_tree = _build_file_tree(repo_info)
        prompt = build_mindmap_prompt(
            repo_name=repo_info.display_name, file_tree=file_tree
        )
        cache_key = _project_cache_key(
            "mindmap",
            repo_info.full_name,
            self._provider.model_name,
            _content_hash(file_tree),
        )
        return await self._call_llm(prompt, cache_key)

    # ── Debugging guide ───────────────────────────────────────────────────────

    async def generate_debugging_guide(
        self,
        repo_info: RepoInfo,
        relationships: ProjectRelationships,
        architecture: str,
        file_docs: dict[str, FileDocumentation],
    ) -> str:
        summaries = _build_file_summaries(file_docs, max_files=60, include_debug=True)
        prompt = build_debugging_guide_prompt(
            repo_name=repo_info.display_name,
            architecture_summary=architecture[:3000],
            file_summaries=summaries,
            hub_files=relationships.hub_files,
            circular_deps=relationships.circular_deps,
        )
        cache_key = _project_cache_key(
            "debug",
            repo_info.full_name,
            self._provider.model_name,
            _content_hash(summaries),
        )
        return await self._call_llm(prompt, cache_key)

    # ── Glossary ──────────────────────────────────────────────────────────────

    async def generate_glossary(
        self,
        repo_info: RepoInfo,
        analyses: dict[str, FileAnalysis],
    ) -> dict[str, str]:
        symbols = _build_symbols_text(analyses)
        prompt = build_glossary_prompt(repo_name=repo_info.display_name, symbols=symbols)
        cache_key = _project_cache_key(
            "glossary",
            repo_info.full_name,
            self._provider.model_name,
            _content_hash(symbols),
        )
        raw = await self._call_llm(prompt, cache_key)
        return _parse_glossary(raw)

    # ── Tests overview ────────────────────────────────────────────────────

    async def generate_tests_overview(
        self,
        repo_info: RepoInfo,
        test_analyses: dict[str, FileAnalysis],
    ) -> str:
        """Generate a single aggregate overview of the entire test suite.

        Replaces individual per-test-file LLM pages with one concise document.
        Returns an empty string when there are no test files.
        """
        if not test_analyses:
            return ""

        summaries = _build_test_file_summaries(test_analyses)
        prompt = build_tests_overview_prompt(
            repo_name=repo_info.display_name,
            test_file_summaries=summaries,
            test_count=len(test_analyses),
        )
        cache_key = _project_cache_key(
            "tests",
            repo_info.full_name,
            self._provider.model_name,
            _content_hash(summaries),
        )
        return await self._call_llm(prompt, cache_key)

    # ── Summary (compact top-level entry point) ───────────────────────────────

    async def generate_summary(
        self,
        repo_info: RepoInfo,
        relationships: ProjectRelationships,
        architecture: str,
        file_docs: dict[str, FileDocumentation],
        focus_path: str | None = None,
    ) -> str:
        """Generate a compact SUMMARY.md optimised for Copilot @workspace context."""
        summaries = _build_file_summaries(file_docs, max_files=60)
        prompt = build_summary_prompt(
            repo_name=repo_info.display_name,
            description=repo_info.description or "",
            languages=repo_info.languages_used,
            total_files=len(repo_info.files),
            focus_path=focus_path,
            architecture_summary=architecture,
            hub_files=relationships.hub_files,
            entry_points=relationships.entry_points,
            file_summaries=summaries,
        )
        cache_key = _project_cache_key(
            "summary",
            repo_info.full_name,
            self._provider.model_name,
            _content_hash(summaries + (focus_path or "")),
        )
        return await self._call_llm(prompt, cache_key)

    # ── Copilot instructions ──────────────────────────────────────────────────

    async def generate_copilot_instructions(
        self,
        repo_info: RepoInfo,
        relationships: ProjectRelationships,
        architecture: str,
        file_docs: dict[str, FileDocumentation],
        focus_path: str | None = None,
    ) -> str:
        """Generate compact .github/copilot-instructions.md content (<600 words)."""
        summaries = _build_file_summaries(file_docs, max_files=30)
        prompt = build_copilot_instructions_prompt(
            repo_name=repo_info.display_name,
            description=repo_info.description or "",
            languages=repo_info.languages_used,
            total_files=len(repo_info.files),
            focus_path=focus_path,
            architecture_summary=architecture,
            hub_files=relationships.hub_files,
            entry_points=relationships.entry_points,
            file_summaries=summaries,
        )
        cache_key = _project_cache_key(
            "copilot",
            repo_info.full_name,
            self._provider.model_name,
            _content_hash(summaries + (focus_path or "")),
        )
        return await self._call_llm(prompt, cache_key)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _split_batch_response(raw: str, expected_paths: list[str]) -> list[str]:
    """Split a batch LLM response into per-file sections.

    Expects the response to use ``---FILE_DOC: <path>---`` markers between
    sections.  Falls back to splitting on repeated ``## Overview`` headings
    when markers are absent, and finally to returning the whole response for
    single-file batches.
    """
    # Primary: split on ---FILE_DOC: <path>--- markers
    marker_re = re.compile(r"---FILE_DOC:\s*.+?\s*---")
    splits = marker_re.split(raw)
    sections = [s.strip() for s in splits[1:]] if len(splits) > 1 else []

    if len(sections) >= len(expected_paths):
        return sections[: len(expected_paths)]

    # If the primary split found at least one section, pad with empty strings
    if sections:
        while len(sections) < len(expected_paths):
            sections.append("")
        return sections[: len(expected_paths)]

    # Fallback for a single file: the whole response IS the doc
    if len(expected_paths) == 1:
        return [raw.strip()]

    # Fallback: try splitting on repeated ## Overview headings
    overview_sections = re.split(r"(?=^## Overview\b)", raw, flags=re.MULTILINE)
    overview_sections = [s.strip() for s in overview_sections if s.strip()]
    if len(overview_sections) >= len(expected_paths):
        return overview_sections[: len(expected_paths)]

    # Last resort: pad whatever we have
    while len(overview_sections) < len(expected_paths):
        overview_sections.append("")
    return overview_sections[: len(expected_paths)]


def _parse_file_doc(raw: str, file: FileNode, model: str, tier: DocTier = DocTier.FULL) -> FileDocumentation:
    """Parse the LLM's free-form markdown response into a FileDocumentation."""
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    def _get(key: str) -> str:
        for k, v in sections.items():
            if key in k:
                return "\n".join(v).strip()
        return ""

    return FileDocumentation(
        path=file.path,
        language=file.language.value,
        sha=file.sha,
        model_used=model,
        doc_tier=tier,
        overview=_get("overview"),
        architecture_role=_get("architecture"),
        public_api=_get("public api"),
        internal_logic=_get("internal logic"),
        dependencies_explanation=_get("dependencies"),
        dependents_explanation=_get("used by"),
        common_issues=_get("common issues"),
        best_practices=_get("best practices"),
        debugging_guide=_get("debugging"),
        testing_considerations=_get("testing"),
    )


def _build_file_summaries(
    file_docs: dict[str, FileDocumentation],
    max_files: int = 80,
    include_debug: bool = False,
) -> str:
    lines: list[str] = []
    for path, doc in list(file_docs.items())[:max_files]:
        summary = f"### {path}\n{doc.overview}"
        if include_debug and doc.debugging_guide:
            summary += f"\nDebugging: {doc.debugging_guide[:200]}"
        lines.append(summary)
    return "\n\n".join(lines)


def _build_file_tree(repo_info: RepoInfo, max_files: int = 200) -> str:
    """Build an indented file tree string."""
    paths = sorted(f.path for f in repo_info.files[:max_files])
    lines: list[str] = []
    for path in paths:
        depth = path.count("/")
        name = PurePosixPath(path).name
        lines.append("  " * depth + f"- {name}")
    return "\n".join(lines)


def _build_symbols_text(analyses: dict[str, FileAnalysis], max_symbols: int = 300) -> str:
    """Build a compact text listing all classes and functions across the project."""
    lines: list[str] = []
    count = 0
    for path, analysis in analyses.items():
        if count >= max_symbols:
            break
        for cls in analysis.classes:
            lines.append(f"{cls.name} (class) in {path}")
            count += 1
        for fn in analysis.functions:
            lines.append(f"{fn.name} (function) in {path}")
            count += 1
    return "\n".join(lines)


def _parse_glossary(raw: str) -> dict[str, str]:
    """Parse a Markdown definition list into a dict."""
    result: dict[str, str] = {}
    current_term: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("**") and "**" in line[2:]:
            # Extract term between **...**
            end = line.index("**", 2)
            current_term = line[2:end]
        elif line.startswith(":") and current_term:
            result[current_term] = line[1:].strip()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Doc tier classification
# ─────────────────────────────────────────────────────────────────────────────

# Build tool and test-runner config filename patterns (exact filename prefix/suffix checks)
_CONFIG_FILE_PATTERNS = (
    "vite.config.",
    "vitest.config.",
    "webpack.config.",
    "webpack.scripts.",
    "jest.config.",
    "rollup.config.",
    "esbuild.config.",
    "babel.config.",
    "postcss.config.",
    "tailwind.config.",
    "eslint.config.",
    ".eslintrc.",
    "prettier.config.",
    "vitest.setup.",
    "jest.setup.",
    "tsconfig",
    "next.config.",
    "nuxt.config.",
    "angular.json",
    ".babelrc",
    ".prettierrc",
    ".stylelintrc",
    "docker-compose",
    "dockerfile",
    "makefile",
    "cmakelists",
    "cargo.toml",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "composer.lock",
    "poetry.lock",
    "gemfile.lock",
    "pipfile.lock",
    "requirements.txt",
    "setup.cfg",
    "pyproject.toml",
    ".editorconfig",
    ".gitignore",
    ".gitattributes",
    ".npmignore",
    ".npmrc",
    ".nvmrc",
    ".env.example",
    ".env.sample",
    "license",
    "changelog",
    "contributing",
    "codeowners",
)

# Files that are entirely auto-generated or boilerplate (no LLM value)
_SKIP_EXACT_NAMES = frozenset({
    "__init__.py",
    "index.js",
    "index.ts",
    "mod.rs",
    "setup.py",
})

# Files under this line count are too trivial for even a BRIEF doc
_TRIVIAL_LINE_THRESHOLD = 10


def _is_config_file(path: str) -> bool:
    """Return True for build-tool / test-runner / lock / boilerplate files."""
    name = PurePosixPath(path).name.lower()
    return any(name.startswith(pat) or name == pat.rstrip(".") for pat in _CONFIG_FILE_PATTERNS)


def _classify_doc_tier(
    file: FileNode,
    analysis: FileAnalysis,
    hub_set: set[str],
    entry_set: set[str],
) -> DocTier:
    """Decide how deeply a file should be documented.

    SKIP  — no LLM call (test files, type declarations, config, trivial files)
    FULL  — 10-section deep-dive (hub files, entry points, complex files)
    BRIEF — 3-section summary (everything else worth documenting)
    """
    name = PurePosixPath(file.path).name.lower()

    # ── SKIP: files where LLM adds little value ───────────────────────────
    if analysis.has_tests:
        return DocTier.SKIP
    if file.path.endswith(".d.ts"):
        return DocTier.SKIP
    if _is_config_file(file.path):
        return DocTier.SKIP
    # Trivial files: too short to warrant any LLM call
    if file.line_count < _TRIVIAL_LINE_THRESHOLD:
        return DocTier.SKIP
    # Barrel / re-export files that just re-export (e.g. __init__.py with no classes/functions)
    if name in _SKIP_EXACT_NAMES and not analysis.classes and len(analysis.functions) <= 1:
        return DocTier.SKIP

    # ── FULL: architecturally important or complex files ──────────────────
    if file.path in hub_set or file.path in entry_set:
        return DocTier.FULL
    if file.line_count > 250:
        return DocTier.FULL
    if len(analysis.classes) >= 2 or len(analysis.functions) >= 8:
        return DocTier.FULL

    # ── BRIEF: everything else (small utilities, helpers, constants) ──────
    return DocTier.BRIEF


def _build_skip_doc(file: FileNode, analysis: FileAnalysis) -> FileDocumentation:
    """Build a static-analysis-only FileDocumentation without any LLM call."""
    if analysis.has_tests:
        kind = "Test file"
    elif file.path.endswith(".d.ts"):
        kind = "TypeScript declaration file"
    else:
        kind = "Config/setup file"

    parts: list[str] = [f"{kind} — {analysis.line_count} lines."]
    if analysis.functions:
        names = ", ".join(f.name for f in analysis.functions[:8])
        parts.append(f"Functions: {names}.")
    if analysis.classes:
        names = ", ".join(c.name for c in analysis.classes[:5])
        parts.append(f"Classes: {names}.")

    public_api_parts: list[str] = []
    if analysis.classes:
        public_api_parts.append(
            "Classes: " + ", ".join(f"`{c.name}`" for c in analysis.classes)
        )
    if analysis.functions:
        public_api_parts.append(
            "Functions: " + ", ".join(f"`{f.name}`" for f in analysis.functions[:15])
        )
    if analysis.constants:
        public_api_parts.append(
            "Constants: " + ", ".join(f"`{c}`" for c in analysis.constants[:10])
        )

    imports_str = (
        ", ".join(f"`{i.module}`" for i in analysis.imports[:10])
        if analysis.imports
        else "_none_"
    )

    return FileDocumentation(
        path=file.path,
        language=file.language.value,
        sha=file.sha,
        model_used="static-analysis-only",
        doc_tier=DocTier.SKIP,
        overview=" ".join(parts),
        public_api="\n\n".join(public_api_parts) if public_api_parts else "_none_",
        dependencies_explanation=f"Imports: {imports_str}",
    )


def _build_test_file_summaries(analyses: dict[str, FileAnalysis], max_files: int = 150) -> str:
    """Build a compact summary of all test files for the tests-overview prompt."""
    lines: list[str] = []
    for path, analysis in list(analyses.items())[:max_files]:
        fn_names = ", ".join(f.name for f in analysis.functions[:6]) if analysis.functions else "—"
        imports = ", ".join(i.module for i in analysis.imports[:5]) if analysis.imports else "—"
        lines.append(
            f"- `{path}` ({analysis.line_count} lines) — tests: {fn_names} | imports: {imports}"
        )
    return "\n".join(lines)
