"""Tests for the doc generator — uses mocked LLM to avoid real API calls."""

from __future__ import annotations

import pytest

from traverser.generator.doc_generator import (
    _build_file_tree,
    _parse_file_doc,
    _parse_glossary,
)
from traverser.generator.prompts import (
    build_architecture_prompt,
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
