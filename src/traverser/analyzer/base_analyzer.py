"""Base class for all static file analyzers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from traverser.models.doc_models import FileAnalysis
from traverser.models.repo_models import FileNode


class BaseAnalyzer(ABC):
    """Analyze a source file and return structured metadata."""

    @abstractmethod
    def analyze(self, file: FileNode) -> FileAnalysis:
        """Parse *file* and return a FileAnalysis.

        Implementations must never raise — errors should be stored in
        FileAnalysis.error and an otherwise-empty object returned.
        """
        ...

    @staticmethod
    def _safe_analyze(file: FileNode, fn: "callable[[], FileAnalysis]") -> FileAnalysis:
        """Run *fn* and catch all exceptions, storing them in FileAnalysis.error."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return FileAnalysis(
                path=file.path,
                language=file.language.value,
                line_count=file.line_count,
                error=str(exc),
            )
