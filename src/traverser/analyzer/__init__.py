"""Analyzer package — static code analysis."""

from traverser.analyzer.base_analyzer import BaseAnalyzer
from traverser.analyzer.generic_analyzer import GenericAnalyzer
from traverser.analyzer.javascript_analyzer import JavascriptAnalyzer
from traverser.analyzer.php_analyzer import PhpAnalyzer
from traverser.analyzer.python_analyzer import PythonAnalyzer
from traverser.analyzer.relationship_mapper import RelationshipMapper
from traverser.models.repo_models import FileNode, Language


def get_analyzer(file: FileNode) -> BaseAnalyzer:
    """Return the best analyzer for the given file's language."""
    match file.language:
        case Language.PYTHON:
            return PythonAnalyzer()
        case Language.PHP:
            return PhpAnalyzer()
        case Language.JAVASCRIPT | Language.TYPESCRIPT | Language.VUE:
            return JavascriptAnalyzer()
        case _:
            return GenericAnalyzer()


__all__ = [
    "BaseAnalyzer",
    "PythonAnalyzer",
    "JavascriptAnalyzer",
    "PhpAnalyzer",
    "GenericAnalyzer",
    "RelationshipMapper",
    "get_analyzer",
]
