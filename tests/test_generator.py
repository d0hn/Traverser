"""Tests for the doc generator — uses mocked LLM to avoid real API calls."""

from __future__ import annotations

import pytest

from traverser.generator.doc_generator import (
    _build_file_tree,
    _parse_file_doc,
    _parse_glossary,
    _split_batch_response,
)
from traverser.generator.prompts import (
    build_architecture_prompt,
    build_batch_brief_doc_prompt,
    build_file_doc_prompt,
    build_mindmap_prompt,
)
from traverser.models.doc_models import ProjectRelationships
from traverser.models.repo_models import FileNode, Language


class TestPromptBuilders:
    def test_file_doc_prompt_contains_path(self, python_file: FileNode) -> None:
        prompt = build_file_doc_prompt(
            path=python_file.path,
            language="python",
            line_count=python_file.line_count,
            content=python_file.content[:500],
            static_analysis="Classes: Foo | Functions: bar",
            imports_from=[],
            imported_by=["src/main.py"],
        )
        assert python_file.path in prompt
        assert "python" in prompt
        assert "src/main.py" in prompt

    def test_architecture_prompt_contains_repo_name(self, sample_repo) -> None:
        prompt = build_architecture_prompt(
            repo_name="testorg/myproject",
            description="A test project",
            languages=["python", "javascript"],
            total_files=10,
            analysed_files=8,
            hub_files=["src/core.py"],
            entry_points=["src/main.py"],
            circular_deps=[],
            file_summaries="### src/main.py\nEntry point.",
        )
        assert "testorg/myproject" in prompt
        assert "python" in prompt
        assert "src/core.py" in prompt

    def test_mindmap_prompt_contains_file_tree(self, sample_repo) -> None:
        tree = _build_file_tree(sample_repo)
        prompt = build_mindmap_prompt("testorg/myproject", tree)
        assert "testorg/myproject" in prompt
        assert "processor" in tree or "client" in tree

    def test_file_doc_prompt_with_no_importers(self, python_file: FileNode) -> None:
        prompt = build_file_doc_prompt(
            path=python_file.path,
            language="python",
            line_count=10,
            content="# empty",
            static_analysis="",
            imports_from=[],
            imported_by=[],
        )
        assert "leaf module" in prompt or "entry point" in prompt


class TestParseFileDoc:
    _MOCK_RESPONSE = """\
## Overview
This module processes data.

## Architecture Role
This is a core processing module used throughout the system.

## Public API
`DataProcessor.process(data: str) -> str` — Processes data.

## Internal Logic
Uses a validate → transform pipeline.

## Dependencies
- `os`: Standard library for OS interactions.

## Used By
`main.py` imports `DataProcessor` to process user input.

## Common Issues & Gotchas
- Empty string input raises `ValueError`.

## Best Practices
Follows single-responsibility principle.

## Debugging Guide
1. Check that `config` is initialised correctly.
2. Inspect `self._cache` for stale entries.

## Testing Considerations
Test with empty string, whitespace, and very long strings.
"""

    def test_parses_all_sections(self, python_file: FileNode) -> None:
        doc = _parse_file_doc(self._MOCK_RESPONSE, python_file, "gpt-4o")
        assert "processes data" in doc.overview
        assert "core processing module" in doc.architecture_role
        assert "DataProcessor" in doc.public_api
        assert "validate" in doc.internal_logic
        assert "os" in doc.dependencies_explanation
        assert "main.py" in doc.dependents_explanation
        assert "ValueError" in doc.common_issues
        assert "single-responsibility" in doc.best_practices
        assert "config" in doc.debugging_guide
        assert "empty string" in doc.testing_considerations

    def test_stores_sha_and_model(self, python_file: FileNode) -> None:
        doc = _parse_file_doc(self._MOCK_RESPONSE, python_file, "gpt-4o")
        assert doc.sha == python_file.sha
        assert doc.model_used == "gpt-4o"
        assert doc.path == python_file.path

    def test_handles_empty_response(self, python_file: FileNode) -> None:
        doc = _parse_file_doc("", python_file, "gpt-4o")
        assert doc.overview == ""
        assert doc.path == python_file.path


class TestParseGlossary:
    def test_parses_definition_list(self) -> None:
        raw = """\
**DataProcessor** (`src/processor.py`)
: Handles data transformation. Depends on: Config.

**Config** (`src/config.py`)
: Application configuration loader.
"""
        result = _parse_glossary(raw)
        assert "DataProcessor" in result
        assert "Config" in result
        assert "Handles data transformation" in result["DataProcessor"]

    def test_empty_input_returns_empty_dict(self) -> None:
        assert _parse_glossary("") == {}


class TestDocGeneratorWithMockedLLM:
    @pytest.mark.asyncio
    async def test_generate_file_doc_uses_cache(
        self, python_file: FileNode, tmp_path, mocker
    ) -> None:
        """Ensure the same file SHA returns the cached result without calling the LLM."""
        from traverser.config import Config
        from traverser.generator.doc_generator import DocGenerator

        config = Config(
            openai_api_key="sk-test",
            cache_dir=str(tmp_path / "cache"),
            cache_enabled=True,
        )
        with DocGenerator(config) as generator:
            # Mock the LLM provider
            mock_complete = mocker.AsyncMock(return_value=TestParseFileDoc._MOCK_RESPONSE)
            generator._provider.complete = mock_complete  # type: ignore[assignment]

            rels = ProjectRelationships()
            from traverser.analyzer.python_analyzer import PythonAnalyzer

            analysis = PythonAnalyzer().analyze(python_file)

            # First call — should hit the LLM
            await generator.generate_file_doc(python_file, analysis, rels)
            assert mock_complete.call_count == 1

            # Second call — should use cache, NOT call the LLM again
            await generator.generate_file_doc(python_file, analysis, rels)
            assert mock_complete.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_different_sha_triggers_new_call(
        self, python_file: FileNode, tmp_path, mocker
    ) -> None:
        from traverser.config import Config
        from traverser.generator.doc_generator import DocGenerator

        config = Config(
            openai_api_key="sk-test",
            cache_dir=str(tmp_path / "cache2"),
            cache_enabled=True,
        )
        with DocGenerator(config) as generator:
            mock_complete = mocker.AsyncMock(return_value=TestParseFileDoc._MOCK_RESPONSE)
            generator._provider.complete = mock_complete  # type: ignore[assignment]

            from traverser.analyzer.python_analyzer import PythonAnalyzer
            from traverser.models.doc_models import ProjectRelationships

            rels = ProjectRelationships()
            analysis = PythonAnalyzer().analyze(python_file)

            # Call with original sha
            await generator.generate_file_doc(python_file, analysis, rels)

            # Call with modified sha (simulates file change)
            changed_file = python_file.model_copy(update={"sha": "new-sha-xyz"})
            await generator.generate_file_doc(changed_file, analysis, rels)

            assert mock_complete.call_count == 2


class TestSplitBatchResponse:
    """Tests for _split_batch_response — parsing batched LLM output."""

    def test_splits_on_file_doc_markers(self) -> None:
        raw = (
            "---FILE_DOC: src/a.py---\n\n## Overview\nFile A overview.\n\n"
            "---FILE_DOC: src/b.py---\n\n## Overview\nFile B overview.\n"
        )
        sections = _split_batch_response(raw, ["src/a.py", "src/b.py"])
        assert len(sections) == 2
        assert "File A overview" in sections[0]
        assert "File B overview" in sections[1]

    def test_single_file_fallback_returns_whole_response(self) -> None:
        raw = "## Overview\nSingle file doc.\n\n## Public API\n`foo()` — does stuff."
        sections = _split_batch_response(raw, ["src/only.py"])
        assert len(sections) == 1
        assert "Single file doc" in sections[0]

    def test_pads_missing_sections_with_empty_strings(self) -> None:
        raw = "---FILE_DOC: src/a.py---\n\n## Overview\nOnly one file returned."
        sections = _split_batch_response(raw, ["src/a.py", "src/b.py", "src/c.py"])
        assert len(sections) == 3
        assert "Only one file returned" in sections[0]
        # Missing sections should be empty strings
        assert sections[1] == ""
        assert sections[2] == ""

    def test_overview_heading_fallback(self) -> None:
        """When markers are missing, falls back to splitting on ## Overview."""
        raw = (
            "## Overview\nFirst file.\n\n## Public API\n`a()`\n\n"
            "## Overview\nSecond file.\n\n## Public API\n`b()`\n"
        )
        sections = _split_batch_response(raw, ["src/a.py", "src/b.py"])
        assert len(sections) == 2
        assert "First file" in sections[0]
        assert "Second file" in sections[1]

    def test_empty_response_returns_empty_sections(self) -> None:
        sections = _split_batch_response("", ["src/a.py", "src/b.py"])
        assert len(sections) == 2


class TestBatchBriefPromptBuilder:
    """Tests for build_batch_brief_doc_prompt."""

    def test_includes_all_file_paths(self) -> None:
        blocks = [
            {
                "path": "src/utils.py",
                "language": "python",
                "line_count": 30,
                "content": "def helper(): pass",
                "static_analysis": "Functions: helper",
                "imports_from": ["os"],
                "imported_by": ["src/main.py"],
            },
            {
                "path": "src/constants.ts",
                "language": "typescript",
                "line_count": 15,
                "content": "export const FOO = 1;",
                "static_analysis": "Constants: FOO",
                "imports_from": [],
                "imported_by": ["src/app.ts"],
            },
        ]
        prompt = build_batch_brief_doc_prompt(blocks)
        assert "src/utils.py" in prompt
        assert "src/constants.ts" in prompt
        assert "def helper(): pass" in prompt
        assert "export const FOO = 1;" in prompt
        assert "---FILE_DOC:" in prompt

    def test_file_count_in_prompt(self) -> None:
        blocks = [
            {
                "path": f"src/file{i}.py",
                "language": "python",
                "line_count": 20,
                "content": f"x = {i}",
                "static_analysis": "",
                "imports_from": [],
                "imported_by": [],
            }
            for i in range(3)
        ]
        prompt = build_batch_brief_doc_prompt(blocks)
        assert "3 files" in prompt


class TestBriefBatchGeneration:
    """Tests for DocGenerator.generate_brief_batch with mocked LLM."""

    @pytest.mark.asyncio
    async def test_batch_returns_docs_for_all_files(
        self, python_file: FileNode, js_file: FileNode, tmp_path, mocker
    ) -> None:
        from traverser.config import Config
        from traverser.generator.doc_generator import DocGenerator
        from traverser.models.doc_models import DocTier, ProjectRelationships
        from traverser.analyzer.python_analyzer import PythonAnalyzer
        from traverser.analyzer.javascript_analyzer import JavascriptAnalyzer

        config = Config(
            openai_api_key="sk-test",
            cache_dir=str(tmp_path / "cache"),
            cache_enabled=True,
        )
        with DocGenerator(config) as generator:
            # Mock returns a properly separated batch response
            batch_response = (
                "---FILE_DOC: src/processor.py---\n\n"
                "## Overview\nProcesses data.\n\n"
                "## Public API\n`DataProcessor.process()` — transforms data.\n\n"
                "## Common Issues & Gotchas\nNone identified.\n\n"
                "---FILE_DOC: src/api/client.js---\n\n"
                "## Overview\nAPI client wrapper.\n\n"
                "## Public API\n`ApiClient.get()` — fetches data.\n\n"
                "## Common Issues & Gotchas\nTimeout not configurable.\n"
            )
            mock_complete = mocker.AsyncMock(return_value=batch_response)
            generator._provider.complete = mock_complete  # type: ignore[assignment]

            rels = ProjectRelationships()
            analyses = {
                python_file.path: PythonAnalyzer().analyze(python_file),
                js_file.path: JavascriptAnalyzer().analyze(js_file),
            }

            results = await generator.generate_brief_batch(
                [python_file, js_file], analyses, rels
            )

            assert len(results) == 2
            assert python_file.path in results
            assert js_file.path in results
            assert results[python_file.path].doc_tier == DocTier.BRIEF
            assert "Processes data" in results[python_file.path].overview
            assert "API client" in results[js_file.path].overview
            # Only ONE LLM call for both files
            assert mock_complete.call_count == 1

    @pytest.mark.asyncio
    async def test_batch_uses_cache_per_file(
        self, python_file: FileNode, js_file: FileNode, tmp_path, mocker
    ) -> None:
        from traverser.config import Config
        from traverser.generator.doc_generator import DocGenerator
        from traverser.models.doc_models import ProjectRelationships
        from traverser.analyzer.python_analyzer import PythonAnalyzer
        from traverser.analyzer.javascript_analyzer import JavascriptAnalyzer

        config = Config(
            openai_api_key="sk-test",
            cache_dir=str(tmp_path / "cache"),
            cache_enabled=True,
        )
        batch_response = (
            "---FILE_DOC: src/processor.py---\n\n"
            "## Overview\nProcesses data.\n\n"
            "---FILE_DOC: src/api/client.js---\n\n"
            "## Overview\nAPI client.\n"
        )
        with DocGenerator(config) as generator:
            mock_complete = mocker.AsyncMock(return_value=batch_response)
            generator._provider.complete = mock_complete  # type: ignore[assignment]

            rels = ProjectRelationships()
            analyses = {
                python_file.path: PythonAnalyzer().analyze(python_file),
                js_file.path: JavascriptAnalyzer().analyze(js_file),
            }

            # First call — should call LLM
            await generator.generate_brief_batch([python_file, js_file], analyses, rels)
            assert mock_complete.call_count == 1

            # Second call — should use cache, no LLM call
            await generator.generate_brief_batch([python_file, js_file], analyses, rels)
            assert mock_complete.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_empty_file_list_returns_empty(self, tmp_path, mocker) -> None:
        from traverser.config import Config
        from traverser.generator.doc_generator import DocGenerator
        from traverser.models.doc_models import ProjectRelationships

        config = Config(openai_api_key="sk-test", cache_enabled=False)
        with DocGenerator(config) as generator:
            results = await generator.generate_brief_batch([], {}, ProjectRelationships())
            assert results == {}


# ── Config auto-detection tests ───────────────────────────────────────────────


class TestConfigProviderAutoDetect:
    """Verify that Config auto-selects the provider from available API keys."""

    def test_openai_key_keeps_openai_provider(self) -> None:
        from traverser.config import Config, LLMProvider

        cfg = Config(openai_api_key="sk-test")
        assert cfg.llm_provider == LLMProvider.OPENAI

    def test_anthropic_key_only_switches_provider(self) -> None:
        from traverser.config import Config, LLMProvider

        cfg = Config(openai_api_key=None, anthropic_api_key="sk-ant-test")
        assert cfg.llm_provider == LLMProvider.ANTHROPIC

    def test_anthropic_key_switches_default_model(self) -> None:
        from traverser.config import Config, LLMProvider

        cfg = Config(openai_api_key=None, anthropic_api_key="sk-ant-test")
        assert cfg.llm_provider == LLMProvider.ANTHROPIC
        # Model should switch away from the OpenAI default
        assert "gpt" not in cfg.llm_model

    def test_explicit_provider_not_overridden(self) -> None:
        from traverser.config import Config, LLMProvider

        # If user explicitly sets provider to anthropic, it should not be changed
        cfg = Config(llm_provider=LLMProvider.ANTHROPIC, anthropic_api_key="sk-ant-test")
        assert cfg.llm_provider == LLMProvider.ANTHROPIC

    def test_no_keys_keeps_openai_default(self) -> None:
        from traverser.config import Config, LLMProvider

        cfg = Config(openai_api_key=None, anthropic_api_key=None)
        # Falls through without raising; require_api_key() will error later
        assert cfg.llm_provider == LLMProvider.OPENAI

    def test_github_copilot_provider_unchanged(self) -> None:
        from traverser.config import Config, LLMProvider

        cfg = Config(llm_provider=LLMProvider.GITHUB_COPILOT)
        assert cfg.llm_provider == LLMProvider.GITHUB_COPILOT
