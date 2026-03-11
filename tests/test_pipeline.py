"""Integration test for the full pipeline with mocked GitHub and LLM."""

from __future__ import annotations

import pytest

from traverser.models.repo_models import FileNode, Language, RepoInfo


@pytest.fixture
def mock_repo(python_file: FileNode, js_file: FileNode) -> RepoInfo:
    return RepoInfo(
        owner="testorg",
        repo_name="myproject",
        full_name="testorg/myproject",
        description="Integration test repo",
        default_branch="main",
        url="https://github.com/testorg/myproject",
        clone_url="https://github.com/testorg/myproject.git",
        files=[python_file, js_file],
    )


_MOCK_DOC = """\
## Overview
This module does things.

## Architecture Role
Central piece.

## Public API
`Foo.bar()` — does stuff.

## Internal Logic
Loop over items.

## Dependencies
- `os`: File operations.

## Used By
`main.py` uses this.

## Common Issues & Gotchas
None known.

## Best Practices
Follows SOLID.

## Debugging Guide
Check logs.

## Testing Considerations
Unit test all branches.
"""


class TestPipelineDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_files(
        self, tmp_path, mock_repo, mocker
    ) -> None:
        """Dry-run must analyse files but skip LLM calls and disk output."""
        from traverser.config import Config
        from traverser.pipeline import Pipeline

        config = Config(
            openai_api_key="sk-test",
            output_dir=tmp_path / "output",
            cache_enabled=False,
        )
        pipeline = Pipeline(config)

        # Mock the GitHub fetcher so no real HTTP call is made
        mocker.patch.object(pipeline._fetcher, "fetch", return_value=mock_repo)

        kb = await pipeline.run(
            "https://github.com/testorg/myproject", dry_run=True
        )

        assert kb.owner == "testorg"
        assert kb.repo_name == "myproject"
        # No LLM docs generated in dry-run
        assert len(kb.file_docs) == 0
        # Output directory should not have been created
        assert not (tmp_path / "output" / "testorg_myproject").exists()


class TestPipelineFullRun:
    @pytest.mark.asyncio
    async def test_full_run_with_mocked_llm(
        self, tmp_path, mock_repo, mocker
    ) -> None:
        """Full pipeline run with mocked fetcher + mocked LLM."""
        from traverser.config import Config
        from traverser.pipeline import Pipeline

        config = Config(
            openai_api_key="sk-test",
            output_dir=tmp_path / "output",
            cache_enabled=False,
        )
        pipeline = Pipeline(config)

        mocker.patch.object(pipeline._fetcher, "fetch", return_value=mock_repo)
        mocker.patch.object(
            pipeline._generator._provider, "complete", new=_mock_complete
        )

        kb = await pipeline.run("https://github.com/testorg/myproject")

        assert kb.owner == "testorg"
        # All analysable files should be documented
        assert len(kb.file_docs) == len(mock_repo.analysable_files)
        # Project-level artefacts should be present
        assert kb.architecture_overview
        assert kb.mind_map_mermaid
        assert kb.debugging_guide
        # Output files should exist
        root = tmp_path / "output" / "testorg_myproject"
        assert root.exists()
        assert (root / "00_README.md").exists()
        assert (root / "01_ARCHITECTURE.md").exists()

    @pytest.mark.asyncio
    async def test_failing_file_does_not_break_pipeline(
        self, tmp_path, mocker
    ) -> None:
        """One bad file should be skipped gracefully."""
        from traverser.config import Config
        from traverser.pipeline import Pipeline
        from traverser.models.repo_models import FileNode, Language

        bad_py = FileNode(
            path="src/broken.py",
            name="broken.py",
            language=Language.PYTHON,
            size_bytes=20,
            content="def broken(\n  syntax error!!!!",
            sha="bad",
        )
        good_py = FileNode(
            path="src/good.py",
            name="good.py",
            language=Language.PYTHON,
            size_bytes=300,
            content=(
                "import os\nimport sys\n\nMY_CONST = 42\n\n"
                "def hello():\n    \"\"\"Say hello.\"\"\"\n    return 'hello'\n\n"
                "def goodbye():\n    \"\"\"Say goodbye.\"\"\"\n    return 'goodbye'\n\n"
                "def main():\n    print(hello())\n    print(goodbye())\n"
            ),
            sha="good",
        )
        repo = RepoInfo(
            owner="testorg",
            repo_name="mixed",
            full_name="testorg/mixed",
            description="",
            default_branch="main",
            url="https://github.com/testorg/mixed",
            clone_url="https://github.com/testorg/mixed.git",
            files=[bad_py, good_py],
        )

        config = Config(
            openai_api_key="sk-test",
            output_dir=tmp_path / "output",
            cache_enabled=False,
        )
        pipeline = Pipeline(config)
        mocker.patch.object(pipeline._fetcher, "fetch", return_value=repo)
        mocker.patch.object(
            pipeline._generator._provider, "complete", new=_mock_complete
        )

        kb = await pipeline.run("https://github.com/testorg/mixed")
        # Good file should still be documented even though bad file analysis failed
        assert "src/good.py" in kb.file_docs


async def _mock_complete(prompt: str) -> str:
    """Mock LLM that returns a valid documentation response."""
    return _MOCK_DOC
