"""Generic analyzer for languages without a dedicated analyzer (Java, Go, Rust, etc.)."""

from __future__ import annotations

import re

from traverser.analyzer.base_analyzer import BaseAnalyzer
from traverser.models.doc_models import ClassInfo, FileAnalysis, FunctionInfo, ImportInfo
from traverser.models.repo_models import FileNode, Language

# ── Language-specific patterns ────────────────────────────────────────────────

_PATTERNS: dict[str, dict[str, str]] = {
    "java": {
        "import": r"^import\s+(?:static\s+)?(?P<module>[\w.]+)(?:\.\*)?;",
        "class": r"(?:public|private|protected|abstract|final)?\s+class\s+(?P<name>\w+)",
        "function": r"(?:public|private|protected|static|final|abstract|synchronized)(?:\s+\w+)*\s+(?P<name>\w+)\s*\(",
    },
    "go": {
        "import": r'"(?P<module>[^"]+)"',
        "class": r"type\s+(?P<name>\w+)\s+struct",
        "function": r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(?P<name>\w+)\s*\(",
    },
    "rust": {
        "import": r"use\s+(?P<module>[\w:]+)(?:::\{[^}]*\}|::\*)?;",
        "class": r"(?:pub\s+)?(?:struct|enum|trait)\s+(?P<name>\w+)",
        "function": r"(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\(",
    },
    "csharp": {
        "import": r"using\s+(?:static\s+)?(?P<module>[\w.]+);",
        "class": r"(?:public|private|protected|internal|abstract|sealed)?\s+class\s+(?P<name>\w+)",
        "function": r"(?:public|private|protected|internal|static|override|virtual|async)(?:\s+\w+)+\s+(?P<name>\w+)\s*\(",
    },
    "ruby": {
        "import": r"require(?:_relative)?\s+['\"](?P<module>[^'\"]+)['\"]",
        "class": r"class\s+(?P<name>\w+)(?:\s*<\s*\w+)?",
        "function": r"def\s+(?P<name>\w+)",
    },
    "php": {
        "import": r"(?:use|require|include)(?:_once)?\s+['\"]?(?P<module>[\w/\\]+)['\"]?",
        "class": r"class\s+(?P<name>\w+)",
        "function": r"(?:public|private|protected|static)?\s+function\s+(?P<name>\w+)",
    },
    "kotlin": {
        "import": r"^import\s+(?P<module>[\w.]+)(?:\.\*)?",
        "class": r"(?:data\s+)?class\s+(?P<name>\w+)",
        "function": r"(?:suspend\s+)?fun\s+(?P<name>\w+)\s*\(",
    },
    "swift": {
        "import": r"^import\s+(?P<module>\w+)",
        "class": r"(?:class|struct|enum|protocol)\s+(?P<name>\w+)",
        "function": r"(?:override\s+)?func\s+(?P<name>\w+)\s*\(",
    },
    "scala": {
        "import": r"^import\s+(?P<module>[\w.]+)(?:\.\{[^}]*\}|\._)?",
        "class": r"(?:case\s+)?class\s+(?P<name>\w+)",
        "function": r"def\s+(?P<name>\w+)\s*\(",
    },
}

# Default fallback patterns for unknown languages
_DEFAULT_PATTERNS: dict[str, str] = {
    "import": r'(?:import|require|include|use)\s+[\'"]?(?P<module>[\w./\\-]+)[\'"]?',
    "class": r"class\s+(?P<name>\w+)",
    "function": r"(?:function|def|func|fn|sub|method)\s+(?P<name>\w+)",
}


class GenericAnalyzer(BaseAnalyzer):
    """Regex-based analyzer for languages without a dedicated analyzer."""

    def analyze(self, file: FileNode) -> FileAnalysis:
        return self._safe_analyze(file, lambda: self._do_analyze(file))

    def _do_analyze(self, file: FileNode) -> FileAnalysis:
        language = file.language.value
        patterns = _PATTERNS.get(language, _DEFAULT_PATTERNS)

        content = file.content

        imports = self._extract(content, patterns["import"], self._to_import)
        classes = self._extract(content, patterns["class"], self._to_class)
        functions = self._extract(content, patterns["function"], self._to_function)

        return FileAnalysis(
            path=file.path,
            language=language,
            imports=imports,
            classes=classes,
            functions=functions,
            line_count=file.line_count,
        )

    @staticmethod
    def _extract(content: str, pattern: str, converter: "callable") -> list:  # type: ignore[type-arg]
        results = []
        seen: set[str] = set()
        try:
            for m in re.finditer(pattern, content, re.MULTILINE):
                key = m.group(0)
                if key not in seen:
                    seen.add(key)
                    item = converter(m)
                    if item:
                        results.append(item)
        except re.error:
            pass
        return results

    @staticmethod
    def _to_import(m: re.Match) -> ImportInfo | None:
        try:
            return ImportInfo(module=m.group("module"), is_relative=False)
        except IndexError:
            return None

    @staticmethod
    def _to_class(m: re.Match) -> ClassInfo | None:
        try:
            return ClassInfo(name=m.group("name"))
        except IndexError:
            return None

    @staticmethod
    def _to_function(m: re.Match) -> FunctionInfo | None:
        try:
            return FunctionInfo(name=m.group("name"))
        except IndexError:
            return None
