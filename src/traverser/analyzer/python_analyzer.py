"""Python static analyzer using the built-in `ast` module."""

from __future__ import annotations

import ast
import logging
from typing import Any

from traverser.analyzer.base_analyzer import BaseAnalyzer
from traverser.models.doc_models import ClassInfo, FileAnalysis, FunctionInfo, ImportInfo
from traverser.models.repo_models import FileNode

logger = logging.getLogger(__name__)

_TEST_FILE_INDICATORS = ("test_", "_test", "tests/", "/tests/", "spec_", "_spec")
_TEST_FUNC_PREFIXES = ("test_", "Test")


def _is_test_file(path: str) -> bool:
    return any(indicator in path for indicator in _TEST_FILE_INDICATORS)


class PythonAnalyzer(BaseAnalyzer):
    """Analyzes Python source files using the `ast` module."""

    def analyze(self, file: FileNode) -> FileAnalysis:
        return self._safe_analyze(file, lambda: self._do_analyze(file))

    def _do_analyze(self, file: FileNode) -> FileAnalysis:
        tree = ast.parse(file.content, filename=file.path)
        visitor = _PythonVisitor()
        visitor.visit(tree)

        return FileAnalysis(
            path=file.path,
            language="python",
            imports=visitor.imports,
            exports=visitor.exports,
            classes=visitor.classes,
            functions=visitor.functions,
            constants=visitor.constants,
            has_tests=_is_test_file(file.path) or bool(visitor.test_functions),
            line_count=file.line_count,
        )


class _PythonVisitor(ast.NodeVisitor):
    """Collects structured metadata while walking the AST."""

    def __init__(self) -> None:
        self.imports: list[ImportInfo] = []
        self.exports: list[str] = []
        self.classes: list[ClassInfo] = []
        self.functions: list[FunctionInfo] = []
        self.constants: list[str] = []
        self.test_functions: list[str] = []
        self._current_class: ClassInfo | None = None

    # ── Imports ───────────────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                ImportInfo(
                    module=alias.name,
                    alias=alias.asname,
                    is_relative=False,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        symbols = [a.name for a in node.names if a.name != "*"]
        self.imports.append(
            ImportInfo(
                module=module,
                symbols=symbols,
                is_relative=(node.level or 0) > 0,
                alias=None,
            )
        )

    # ── Classes ───────────────────────────────────────────────────────────────

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [_unparse_expr(b) for b in node.bases]
        decorators = [_unparse_expr(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node)

        class_info = ClassInfo(
            name=node.name,
            parent_classes=bases,
            docstring=docstring,
            decorators=decorators,
            line_number=node.lineno,
        )

        # Collect attributes from __init__ assignments
        for item in ast.walk(node):
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Attribute):
                if isinstance(item.target.value, ast.Name) and item.target.value.id == "self":
                    class_info.attributes.append(item.target.attr)

        # Collect methods
        prev_class = self._current_class
        self._current_class = class_info

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = self._build_function_info(item, is_method=True)
                class_info.methods.append(method)

        self._current_class = prev_class
        self.classes.append(class_info)
        # Don't call generic_visit so we don't double-count nested functions
        self.exports.append(node.name)

    # ── Functions ─────────────────────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._current_class is None:
            func = self._build_function_info(node, is_method=False)
            self.functions.append(func)
            if any(node.name.startswith(p) for p in _TEST_FUNC_PREFIXES):
                self.test_functions.append(node.name)
            self.exports.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self._current_class is None:
            func = self._build_function_info(node, is_method=False)
            self.functions.append(func)
            if any(node.name.startswith(p) for p in _TEST_FUNC_PREFIXES):
                self.test_functions.append(node.name)
            self.exports.append(node.name)
        self.generic_visit(node)

    # ── Constants ─────────────────────────────────────────────────────────────

    def visit_Assign(self, node: ast.Assign) -> None:
        # Only module-level assignments with UPPER_CASE names
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                self.constants.append(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.target.id.isupper():
            self.constants.append(node.target.id)
        self.generic_visit(node)

    # ── __all__ (explicit exports) ────────────────────────────────────────────

    def visit_Module(self, node: ast.Module) -> None:
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(item.value, (ast.List, ast.Tuple)):
                            for elt in item.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    self.exports.append(elt.value)
        self.generic_visit(node)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_function_info(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_method: bool,
    ) -> FunctionInfo:
        args = node.args
        params: list[str] = []

        for arg in args.args:
            annotation = _unparse_expr(arg.annotation) if arg.annotation else None
            params.append(f"{arg.arg}: {annotation}" if annotation else arg.arg)

        if args.vararg:
            params.append(f"*{args.vararg.arg}")
        for kwarg in args.kwonlyargs:
            params.append(kwarg.arg)
        if args.kwarg:
            params.append(f"**{args.kwarg.arg}")

        return_type = _unparse_expr(node.returns) if node.returns else None
        decorators = [_unparse_expr(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node)

        return FunctionInfo(
            name=node.name,
            parameters=params,
            return_type=return_type,
            docstring=docstring,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_method=is_method,
            decorators=decorators,
            line_number=node.lineno,
        )


def _unparse_expr(node: Any) -> str:
    """Convert an AST expression node back to a string (best-effort)."""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "..."
