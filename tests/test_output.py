"""Tests for the output writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from traverser.models.doc_models import (
    FileAnalysis,
    FileDocumentation,
    KnowledgeBase,
    ProjectRelationships,
)
from traverser.output.writer import OutputWriter, _safe_filename


class TestSafeFilename:
    def test_simple_path(self) -> None:
        assert _safe_filename("src/main.py") == "src_main_py.md"

    def test_deep_path(self) -> None:
        result = _safe_filename("a/b/c/file.ts")
        assert result.endswith(".md")
        assert "/" not in result
        assert "." not in result[:-3]  # No dots except the final .md

    def test_dashes_preserved_or_replaced(self) -> None:
        name = _safe_filename("src/my-module.py")
        assert name.endswith(".md")


class TestOutputWriter:
    @pytest.fixture
    def minimal_kb(self) -> KnowledgeBase:
        analysis = FileAnalysis(
            path="src/main.py",
            language="python",
            line_count=50,
        )
        doc = FileDocumentation(
            path="src/main.py",
            language="python",
            sha="abc",
            model_used="gpt-4o",
            overview="Entry point for the application.",
            architecture_role="Main entry point.",
            public_api="No public API.",
            internal_logic="Starts the server.",
            dependencies_explanation="Uses config module.",
            dependents_explanation="Not imported by anything.",
            common_issues="May fail if env vars missing.",
            best_practices="Uses dependency injection.",
            debugging_guide="Check env vars first.",
            testing_considerations="Integration test recommended.",
        )
        return KnowledgeBase(
            owner="testorg",
            repo_name="myproject",
            generated_at="2024-01-01 00:00 UTC",
            model_used="gpt-4o",
            file_analyses={"src/main.py": analysis},
            file_docs={"src/main.py": doc},
            relationships=ProjectRelationships(
                imports_from={"src/main.py": []},
                imported_by={"src/main.py": []},
                hub_files=[],
                entry_points=["src/main.py"],
            ),
            architecture_overview="## System Overview\nA test project.",
            mind_map_mermaid="```mermaid\nmindmap\n  root((myproject))\n```",
            relationship_graph_mermaid="graph TD\n  note[no deps]",
            debugging_guide="## Debugging\nCheck env vars.",
            glossary={"Config": "Application configuration."},
        )

    def test_creates_root_directory(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        assert root.exists()
        assert root.is_dir()

    def test_creates_all_required_files(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)

        expected = [
            "00_README.md",
            "01_ARCHITECTURE.md",
            "02_MIND_MAP.md",
            "03_RELATIONSHIPS.md",
            "04_DEBUGGING_GUIDE.md",
            "05_GLOSSARY.md",
        ]
        for filename in expected:
            assert (root / filename).exists(), f"{filename} was not created"

    def test_creates_files_directory(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        assert (root / "files").is_dir()

    def test_file_doc_contains_overview(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        doc_file = root / "files" / _safe_filename("src/main.py")
        assert doc_file.exists()
        content = doc_file.read_text()
        assert "Entry point for the application." in content

    def test_readme_contains_stats(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        readme = (root / "00_README.md").read_text()
        assert "testorg/myproject" in readme
        assert "gpt-4o" in readme

    def test_glossary_contains_terms(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        glossary = (root / "05_GLOSSARY.md").read_text()
        assert "Config" in glossary
        assert "Application configuration." in glossary

    def test_root_dir_name_is_slugified(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        assert root.name == "testorg_myproject"

    def test_file_index_created(self, tmp_path: Path, minimal_kb: KnowledgeBase) -> None:
        writer = OutputWriter(tmp_path)
        root = writer.write(minimal_kb)
        index = root / "FILE_INDEX.md"
        assert index.exists()
        content = index.read_text()
        assert "src/main.py" in content
