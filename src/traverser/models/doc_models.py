"""Documentation and analysis data models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocTier(str, Enum):
    """How much LLM analysis a file receives."""

    FULL = "full"    # 10-section deep-dive — services, hub files, entry points
    BRIEF = "brief"  # 3-section summary — small utilities and helpers
    SKIP = "skip"    # no LLM — test files, type declarations, build configs


class ImportInfo(BaseModel):
    """A single import statement extracted from a file."""

    module: str
    symbols: list[str] = Field(default_factory=list)
    is_relative: bool = False
    alias: str | None = None
    level: int = Field(default=0, description="Number of leading dots in a Python relative import")


class FunctionInfo(BaseModel):
    """A function or method extracted from a file."""

    name: str
    parameters: list[str] = Field(default_factory=list)
    return_type: str | None = None
    docstring: str | None = None
    is_async: bool = False
    is_method: bool = False
    decorators: list[str] = Field(default_factory=list)
    line_number: int | None = None


class ClassInfo(BaseModel):
    """A class definition extracted from a file."""

    name: str
    parent_classes: list[str] = Field(default_factory=list)
    methods: list[FunctionInfo] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    docstring: str | None = None
    decorators: list[str] = Field(default_factory=list)
    line_number: int | None = None


class FileAnalysis(BaseModel):
    """Results of static analysis on a single file."""

    path: str
    language: str
    imports: list[ImportInfo] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    classes: list[ClassInfo] = Field(default_factory=list)
    functions: list[FunctionInfo] = Field(default_factory=list)
    constants: list[str] = Field(default_factory=list)
    has_tests: bool = False
    line_count: int = 0
    error: str | None = None  # Set when analysis raised an exception

    @property
    def imported_modules(self) -> list[str]:
        return [imp.module for imp in self.imports]

    @property
    def all_class_names(self) -> list[str]:
        return [c.name for c in self.classes]

    @property
    def all_function_names(self) -> list[str]:
        return [f.name for f in self.functions]

    def to_summary_text(self) -> str:
        """Compact text summary used in prompts."""
        parts = [f"Language: {self.language}", f"Lines: {self.line_count}"]
        if self.imports:
            parts.append(f"Imports: {', '.join(self.imported_modules[:20])}")
        if self.classes:
            parts.append(f"Classes: {', '.join(self.all_class_names)}")
        if self.functions:
            parts.append(f"Functions: {', '.join(self.all_function_names[:20])}")
        if self.constants:
            parts.append(f"Constants: {', '.join(self.constants[:10])}")
        if self.has_tests:
            parts.append("Has tests: yes")
        return " | ".join(parts)


class FileDocumentation(BaseModel):
    """AI-generated documentation for a single file."""

    path: str
    language: str
    sha: str  # blob SHA — used for cache invalidation
    model_used: str
    doc_tier: DocTier = DocTier.FULL

    # Core sections (raw markdown text)
    overview: str = ""
    architecture_role: str = ""
    public_api: str = ""
    internal_logic: str = ""
    dependencies_explanation: str = ""
    dependents_explanation: str = ""
    common_issues: str = ""
    best_practices: str = ""
    debugging_guide: str = ""
    testing_considerations: str = ""

    # Structured metadata
    related_files: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)


class ProjectRelationships(BaseModel):
    """Dependency graph between repository files."""

    # file_path → list of file_paths it imports (resolved to repo paths)
    imports_from: dict[str, list[str]] = Field(default_factory=dict)
    # file_path → list of file_paths that import it
    imported_by: dict[str, list[str]] = Field(default_factory=dict)
    # Files imported by many others — key architectural nodes
    hub_files: list[str] = Field(default_factory=list)
    # Files that are not imported by anything — entry points / scripts
    entry_points: list[str] = Field(default_factory=list)
    # Detected circular dependency chains
    circular_deps: list[list[str]] = Field(default_factory=list)


class KnowledgeBase(BaseModel):
    """Complete knowledge base for a repository."""

    owner: str
    repo_name: str
    generated_at: str
    model_used: str
    tests_overview: str = ""

    # File-level artefacts
    file_analyses: dict[str, FileAnalysis] = Field(default_factory=dict)
    file_docs: dict[str, FileDocumentation] = Field(default_factory=dict)
    relationships: ProjectRelationships = Field(default_factory=ProjectRelationships)

    # Focus / update-mode metadata
    focus_path: str | None = None  # Set when --focus was used

    # Project-level artefacts
    architecture_overview: str = ""
    mind_map_mermaid: str = ""
    relationship_graph_mermaid: str = ""
    debugging_guide: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)
    summary: str = ""               # Compact repo summary for SUMMARY.md / Copilot
    copilot_instructions: str = ""  # Compact .github/copilot-instructions.md content

    @property
    def stats(self) -> dict[str, int]:
        return {
            "total_files": len(self.file_analyses),
            "documented_files": len(self.file_docs),
            "total_classes": sum(len(a.classes) for a in self.file_analyses.values()),
            "total_functions": sum(len(a.functions) for a in self.file_analyses.values()),
        }
