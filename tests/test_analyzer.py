"""Tests for static analyzers and relationship mapper."""

from __future__ import annotations

import pytest

from traverser.analyzer.javascript_analyzer import JavascriptAnalyzer
from traverser.analyzer.php_analyzer import PhpAnalyzer
from traverser.analyzer.python_analyzer import PythonAnalyzer
from traverser.analyzer.relationship_mapper import RelationshipMapper
from traverser.models.doc_models import FileAnalysis
from traverser.models.repo_models import FileNode, Language


class TestPythonAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = PythonAnalyzer()

    def test_extracts_imports(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        modules = result.imported_modules
        assert "os" in modules
        assert "sys" in modules
        assert "pathlib" in modules

    def test_extracts_relative_imports(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        relative = [i for i in result.imports if i.is_relative]
        assert len(relative) >= 2  # .utils and ..config

    def test_extracts_classes(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        names = result.all_class_names
        assert "BaseProcessor" in names
        assert "DataProcessor" in names

    def test_class_parent(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        data_proc = next(c for c in result.classes if c.name == "DataProcessor")
        assert "BaseProcessor" in data_proc.parent_classes

    def test_extracts_methods(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        base = next(c for c in result.classes if c.name == "BaseProcessor")
        method_names = [m.name for m in base.methods]
        assert "process" in method_names
        assert "validate" in method_names

    def test_extracts_standalone_functions(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        names = result.all_function_names
        assert "standalone_function" in names
        assert "async_helper" in names

    def test_async_function_flag(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        async_fn = next(f for f in result.functions if f.name == "async_helper")
        assert async_fn.is_async is True

    def test_extracts_constants(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        assert "MY_CONSTANT" in result.constants or "DEBUG_FLAG" in result.constants

    def test_line_count(self, python_file: FileNode) -> None:
        result = self.analyzer.analyze(python_file)
        assert result.line_count > 0

    def test_syntax_error_returns_error_field(self) -> None:
        bad_file = FileNode(
            path="bad.py",
            name="bad.py",
            language=Language.PYTHON,
            size_bytes=10,
            content="def broken(\n  syntax error here",
            sha="bad",
        )
        result = self.analyzer.analyze(bad_file)
        assert result.error is not None

    def test_empty_file(self) -> None:
        empty = FileNode(
            path="empty.py",
            name="empty.py",
            language=Language.PYTHON,
            size_bytes=0,
            content="",
            sha="empty",
        )
        result = self.analyzer.analyze(empty)
        assert result.error is None
        assert result.classes == []
        assert result.functions == []


class TestJavascriptAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = JavascriptAnalyzer()

    def test_extracts_es_imports(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        modules = result.imported_modules
        assert "react" in modules
        assert "axios" in modules
        assert "./utils" in modules

    def test_extracts_require(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        modules = result.imported_modules
        assert "./config" in modules

    def test_extracts_exports(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        assert "ApiClient" in result.exports or "createClient" in result.exports

    def test_extracts_class(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        names = [c.name for c in result.classes]
        assert "ApiClient" in names

    def test_extracts_functions(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        names = result.all_function_names
        assert "createClient" in names

    def test_extracts_constants(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        assert "API_BASE_URL" in result.constants

    def test_typescript_file(self, ts_file: FileNode) -> None:
        result = self.analyzer.analyze(ts_file)
        assert "UserServiceImpl" in [c.name for c in result.classes]
        modules = result.imported_modules
        assert "@angular/core" in modules

    def test_relative_import_flag(self, js_file: FileNode) -> None:
        result = self.analyzer.analyze(js_file)
        relative = [i for i in result.imports if i.is_relative]
        assert any(i.module == "./utils" for i in relative)


class TestRelationshipMapper:
    def _make_analysis(self, path: str, modules: list[str]) -> FileAnalysis:
        from traverser.models.doc_models import ImportInfo

        return FileAnalysis(
            path=path,
            language="python",
            imports=[ImportInfo(module=m) for m in modules],
        )

    def test_builds_imports_from(self) -> None:
        analyses = {
            "src/main.py": self._make_analysis("src/main.py", ["src/config"]),
            "src/config.py": self._make_analysis("src/config.py", []),
        }
        mapper = RelationshipMapper()
        result = mapper.build(analyses)
        # src/main.py → src/config.py
        assert "src/config.py" in result.imports_from.get("src/main.py", [])

    def test_builds_imported_by(self) -> None:
        analyses = {
            "src/main.py": self._make_analysis("src/main.py", ["src/config"]),
            "src/config.py": self._make_analysis("src/config.py", []),
        }
        mapper = RelationshipMapper()
        result = mapper.build(analyses)
        assert "src/main.py" in result.imported_by.get("src/config.py", [])

    def test_entry_points_have_no_importers(self) -> None:
        analyses = {
            "src/main.py": self._make_analysis("src/main.py", ["src/utils"]),
            "src/utils.py": self._make_analysis("src/utils.py", []),
        }
        mapper = RelationshipMapper()
        result = mapper.build(analyses)
        assert "src/main.py" in result.entry_points

    def test_no_self_loops(self) -> None:
        analyses = {
            "src/self_import.py": self._make_analysis("src/self_import.py", ["src/self_import"]),
        }
        mapper = RelationshipMapper()
        result = mapper.build(analyses)
        assert "src/self_import.py" not in result.imports_from.get("src/self_import.py", [])

    def test_mermaid_output_is_valid(self) -> None:
        analyses = {
            "src/main.py": self._make_analysis("src/main.py", ["src/utils"]),
            "src/utils.py": self._make_analysis("src/utils.py", []),
        }
        mapper = RelationshipMapper()
        rels = mapper.build(analyses)
        mermaid = mapper.to_mermaid(rels, analyses)
        assert "graph TD" in mermaid


# ── NestJS / React / Vue / Next.js JavaScript analyzer tests ─────────────────


class TestJavascriptAnalyzerNestJS:
    """Verify NestJS decorator and class-method extraction."""

    def setup_method(self) -> None:
        self.analyzer = JavascriptAnalyzer()

    def test_detects_nestjs_imports(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        modules = result.imported_modules
        assert "@nestjs/common" in modules

    def test_extracts_service_class(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        names = result.all_class_names
        assert "UsersService" in names
        assert "UsersController" in names

    def test_service_class_has_injectable_decorator(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        service = next(c for c in result.classes if c.name == "UsersService")
        assert "Injectable" in service.decorators

    def test_controller_class_has_decorators(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        ctrl = next(c for c in result.classes if c.name == "UsersController")
        assert "Controller" in ctrl.decorators
        assert "UseGuards" in ctrl.decorators

    def test_controller_has_constructor(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        ctrl = next(c for c in result.classes if c.name == "UsersController")
        method_names = [m.name for m in ctrl.methods]
        assert "constructor" in method_names

    def test_service_methods_extracted(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        svc = next(c for c in result.classes if c.name == "UsersService")
        method_names = [m.name for m in svc.methods]
        assert "findAll" in method_names
        assert "create" in method_names

    def test_exported_constants(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        assert "API_VERSION" in result.constants

    def test_line_count(self, nestjs_file: FileNode) -> None:
        result = self.analyzer.analyze(nestjs_file)
        assert result.line_count > 0

    def test_malformed_does_not_raise(self) -> None:
        bad = FileNode(
            path="bad.ts",
            name="bad.ts",
            language=Language.TYPESCRIPT,
            size_bytes=5,
            content="@@@###$$$",
            sha="bad",
        )
        result = JavascriptAnalyzer().analyze(bad)
        assert isinstance(result, FileAnalysis)

    def test_empty_file_does_not_raise(self) -> None:
        empty = FileNode(
            path="empty.ts",
            name="empty.ts",
            language=Language.TYPESCRIPT,
            size_bytes=0,
            content="",
            sha="empty",
        )
        result = JavascriptAnalyzer().analyze(empty)
        assert result.error is None
        assert result.classes == []
        assert result.functions == []


class TestJavascriptAnalyzerReact:
    """Verify React component + hook + Next.js directive detection."""

    def setup_method(self) -> None:
        self.analyzer = JavascriptAnalyzer()

    def test_detects_react_imports(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        assert "react" in result.imported_modules

    def test_detects_nextjs_imports(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        assert "next/navigation" in result.imported_modules

    def test_detects_use_client_directive(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        assert "USE_CLIENT" in result.constants

    def test_extracts_react_component_function(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        user_profile = next(
            (f for f in result.functions if f.name == "UserProfile"), None
        )
        assert user_profile is not None
        assert "@ReactComponent" in user_profile.decorators

    def test_extracts_arrow_component(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        profile_card = next(
            (f for f in result.functions if f.name == "ProfileCard"), None
        )
        assert profile_card is not None
        assert "@ReactComponent" in profile_card.decorators

    def test_detects_react_hooks(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        assert "REACT_HOOK_useState" in result.constants
        assert "REACT_HOOK_useEffect" in result.constants
        assert "REACT_HOOK_useCallback" in result.constants

    def test_extracts_constants(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        assert "AVATAR_SIZE" in result.constants

    def test_relative_imports(self, react_file: FileNode) -> None:
        result = self.analyzer.analyze(react_file)
        relative = [i for i in result.imports if i.is_relative]
        assert any(i.module == "./Avatar" for i in relative)


class TestJavascriptAnalyzerVue:
    """Verify Vue SFC <script setup> + Composition API extraction."""

    def setup_method(self) -> None:
        self.analyzer = JavascriptAnalyzer()

    def test_extracts_vue_imports(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        modules = result.imported_modules
        assert "vue" in modules

    def test_detects_define_props(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        assert "HAS_DEFINE_PROPS" in result.constants

    def test_detects_define_emits(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        assert "HAS_DEFINE_EMITS" in result.constants

    def test_detects_define_expose(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        assert "HAS_DEFINE_EXPOSE" in result.constants

    def test_detects_vue_composable_usages(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        # useRouter and useAuthStore should be detected
        assert "VUE_USE_ROUTER" in result.constants
        assert "VUE_USE_AUTHSTORE" in result.constants

    def test_extracts_vue_constants(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        assert "MAX_COUNT" in result.constants

    def test_extracts_functions_from_script_block(self, vue_file: FileNode) -> None:
        result = self.analyzer.analyze(vue_file)
        fn_names = result.all_function_names
        assert "increment" in fn_names or "reset" in fn_names

    def test_empty_vue_file_does_not_raise(self) -> None:
        empty = FileNode(
            path="empty.vue",
            name="empty.vue",
            language=Language.VUE,
            size_bytes=0,
            content="<template><div/></template>",
            sha="vue_empty",
        )
        result = self.analyzer.analyze(empty)
        assert result.error is None


# ── PHP / Laravel analyzer tests ─────────────────────────────────────────────


class TestPhpAnalyzer:
    """Verify PHP and Laravel pattern extraction."""

    def setup_method(self) -> None:
        self.analyzer = PhpAnalyzer()

    # ── Imports ───────────────────────────────────────────────────────────────

    def test_extracts_use_imports(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        modules = result.imported_modules
        assert "App/Models/User" in modules
        assert "App/Models/Post" in modules

    def test_extracts_grouped_use(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        modules = result.imported_modules
        # use Illuminate\Http\{Request, Response} → two entries
        assert any("Request" in m for m in modules)
        assert any("Response" in m for m in modules)

    # ── Classes ───────────────────────────────────────────────────────────────

    def test_extracts_classes(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        names = result.all_class_names
        assert "UserController" in names
        assert "User" in names

    def test_controller_parent_class(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        ctrl = next(c for c in result.classes if c.name == "UserController")
        assert "Controller" in ctrl.parent_classes

    def test_controller_gets_laravel_role_decorator(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        ctrl = next(c for c in result.classes if c.name == "UserController")
        assert any("Controller" in d for d in ctrl.decorators)

    def test_eloquent_model_gets_role_decorator(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        user = next(c for c in result.classes if c.name == "User")
        assert any("EloquentModel" in d for d in user.decorators)

    # ── Methods ───────────────────────────────────────────────────────────────

    def test_extracts_controller_methods(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        ctrl = next(c for c in result.classes if c.name == "UserController")
        method_names = [m.name for m in ctrl.methods]
        assert "index" in method_names
        assert "store" in method_names
        assert "destroy" in method_names

    def test_constructor_has_decorator(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        ctrl = next(c for c in result.classes if c.name == "UserController")
        ctor = next((m for m in ctrl.methods if m.name == "__construct"), None)
        assert ctor is not None
        assert "@Constructor" in ctor.decorators

    def test_scope_method_tagged(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        user = next(c for c in result.classes if c.name == "User")
        scope_method = next((m for m in user.methods if m.name == "scopeActive"), None)
        assert scope_method is not None
        assert any("Scope:Active" in d for d in scope_method.decorators)

    def test_accessor_method_tagged(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        user = next(c for c in result.classes if c.name == "User")
        accessor = next(
            (m for m in user.methods if m.name == "getFullNameAttribute"), None
        )
        assert accessor is not None
        assert any("Accessor" in d for d in accessor.decorators)

    def test_mutator_method_tagged(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        user = next(c for c in result.classes if c.name == "User")
        mutator = next(
            (m for m in user.methods if m.name == "setPasswordAttribute"), None
        )
        assert mutator is not None
        assert any("Mutator" in d for d in mutator.decorators)

    def test_eloquent_relationship_in_method_decorator(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        user = next(c for c in result.classes if c.name == "User")
        posts_method = next((m for m in user.methods if m.name == "posts"), None)
        assert posts_method is not None
        assert any("hasMany" in d for d in posts_method.decorators)

    # ── Constants ─────────────────────────────────────────────────────────────

    def test_extracts_define_constants(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        assert "MAX_USERS" in result.constants

    def test_extracts_class_constants(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        assert "STATUS_ACTIVE" in result.constants

    # ── Routes ────────────────────────────────────────────────────────────────

    def test_extracts_routes_in_exports(self, php_routes_file: FileNode) -> None:
        result = self.analyzer.analyze(php_routes_file)
        assert any("ROUTE:GET:/users" in e for e in result.exports)
        assert any("ROUTE:POST:/users" in e for e in result.exports)
        assert any("ROUTE:DELETE:/users/{id}" in e for e in result.exports)

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_line_count(self, php_file: FileNode) -> None:
        result = self.analyzer.analyze(php_file)
        assert result.line_count > 0

    def test_malformed_input_does_not_raise(self) -> None:
        bad = FileNode(
            path="bad.php",
            name="bad.php",
            language=Language.PHP,
            size_bytes=5,
            content="@@@###$$$",
            sha="bad",
        )
        result = self.analyzer.analyze(bad)
        assert isinstance(result, FileAnalysis)

    def test_empty_file_does_not_raise(self) -> None:
        empty = FileNode(
            path="empty.php",
            name="empty.php",
            language=Language.PHP,
            size_bytes=0,
            content="",
            sha="empty",
        )
        result = self.analyzer.analyze(empty)
        assert result.error is None
        assert result.classes == []
        assert result.functions == []
