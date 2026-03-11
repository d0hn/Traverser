"""JavaScript / TypeScript static analyzer — framework-aware.

Supports: React, Next.js (Pages + App Router), Vue 3 (SFC + Composition API),
NestJS, Angular, and plain JavaScript / TypeScript.
"""

from __future__ import annotations

import re
import logging

from traverser.analyzer.base_analyzer import BaseAnalyzer
from traverser.models.doc_models import ClassInfo, FileAnalysis, FunctionInfo, ImportInfo
from traverser.models.repo_models import FileNode

logger = logging.getLogger(__name__)

# ── Import patterns ───────────────────────────────────────────────────────────

# import { A, B } from 'module' | import Default from 'module' | import * as NS
_IMPORT_RE = re.compile(
    r"""^[ \t]*import\s+
    (?:
        (?P<namespace>\*\s+as\s+\w+)|                      # * as NS
        (?P<default_and_named>\w+\s*,?\s*(?:\{[^}]*\})?)|  # Default, { A, B }
        (?P<named_only>\{[^}]*\})|                          # { A, B }
        (?P<default_only>\w+)                               # DefaultOnly
    )?
    \s*from\s+['"](?P<module>[^'"]+)['"]
    |^[ \t]*import\s+['"](?P<side_effect>[^'"]+)['"]        # side-effect import
    """,
    re.MULTILINE | re.VERBOSE,
)

# const x = require('module') or require('module')
_REQUIRE_RE = re.compile(r"""require\(\s*['"](?P<module>[^'"]+)['"]\s*\)""")

# Dynamic import: import('module')
_DYNAMIC_IMPORT_RE = re.compile(r"""(?<!\w)import\(\s*['"](?P<module>[^'"]+)['"]\s*\)""")

# ── Export patterns ───────────────────────────────────────────────────────────

# export { A, B }  or  export { A as default, B as C }
_NAMED_EXPORT_RE = re.compile(r"""^[ \t]*export\s+\{(?P<names>[^}]*)\}""", re.MULTILINE)

# export class / function / const / let / var / type / interface / enum
_EXPORT_DECL_RE = re.compile(
    r"""^[ \t]*export\s+(?:default\s+)?(?:abstract\s+)?"""
    r"""(?:class|function\*?|const|let|var|type|interface|enum|async\s+function)\s+"""
    r"""(?P<name>\w+)""",
    re.MULTILINE,
)

# ── Class patterns ────────────────────────────────────────────────────────────

# class Foo [extends Bar] {
_CLASS_RE = re.compile(
    r"""^[ \t]*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)"""
    r"""(?:\s+extends\s+(?P<base>[\w.]+))?""",
    re.MULTILINE,
)

# ── Function patterns ─────────────────────────────────────────────────────────

# function foo(...) or async function foo(...)
_FUNCTION_RE = re.compile(
    r"""^[ \t]*(?:export\s+)?(?:default\s+)?(?P<async>async\s+)?"""
    r"""function\*?\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)""",
    re.MULTILINE,
)

# const/let/var foo = (params) => or const foo = async (params) =>
_ARROW_RE = re.compile(
    r"""^[ \t]*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*"""
    r"""(?P<async>async\s+)?(?:\([^)]*\)|\w+)\s*=>""",
    re.MULTILINE,
)

# ── Class method patterns (for NestJS / Angular method extraction) ─────────────

# Methods require at least one qualifier keyword (ensures it's a definition)
_CLASS_METHOD_RE = re.compile(
    r"""^[ \t]*(?P<quals>(?:public|private|protected|override|abstract|readonly|static|async)\s+)+"""
    r"""(?:async\s+)?(?:get\s+|set\s+)?"""
    r"""(?P<name>[a-zA-Z_$#][\w$]*)\s*"""
    r"""(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)"""
    r"""(?:\s*:\s*(?P<return_type>[^\n{;]+?))?""",
    re.MULTILINE,
)

# Constructors (NestJS DI — important even without a qualifier prefix)
_CONSTRUCTOR_RE = re.compile(
    r"""^[ \t]*constructor\s*\((?P<params>[^)]*)\)""",
    re.MULTILINE,
)

# ── Framework-specific patterns ───────────────────────────────────────────────

# Next.js "use client" / "use server" directive (App Router)
_USE_DIRECTIVE_RE = re.compile(
    r"""^[ \t]*['"]use\s+(?P<directive>client|server)['"]\s*;?""", re.MULTILINE
)

# Next.js App Router HTTP route handlers: export async function GET(request)
_NEXTJS_HANDLER_RE = re.compile(
    r"""^[ \t]*export\s+(?:async\s+)?function\s+(?P<name>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*\(""",
    re.MULTILINE,
)

# Vue Composition API macros
_VUE_DEFINE_PROPS_RE = re.compile(r"\bdefineProps\s*[<(]", re.MULTILINE)
_VUE_DEFINE_EMITS_RE = re.compile(r"\bdefineEmits\s*[<(]", re.MULTILINE)
_VUE_DEFINE_EXPOSE_RE = re.compile(r"\bdefineExpose\s*\(", re.MULTILINE)
_VUE_DEFINE_COMPONENT_RE = re.compile(r"\bdefineComponent\s*\(", re.MULTILINE)
_VUE_USE_RE = re.compile(r"\buse([A-Z]\w+)\s*\(", re.MULTILINE)  # useRouter, useStore…

# React hooks usage
_REACT_HOOKS_RE = re.compile(
    r"\b(useState|useEffect|useContext|useReducer|useCallback|useMemo|useRef"
    r"|useLayoutEffect|useImperativeHandle|useTransition|useId|useDeferredValue"
    r"|useSyncExternalStore|useActionState|useFormStatus)\s*\(",
    re.MULTILINE,
)

# Vue SFC <script> block extractor
_VUE_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)

# ── Framework-identifier sets ─────────────────────────────────────────────────

_REACT_MODULES: frozenset[str] = frozenset(
    {"react", "react-dom", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"}
)
_NEXTJS_MODULES: frozenset[str] = frozenset(
    {
        "next", "next/navigation", "next/headers", "next/image",
        "next/link", "next/router", "next/server", "next/cache",
        "next/font/google", "next/font/local",
    }
)
_VUE_MODULES: frozenset[str] = frozenset(
    {"vue", "@vue/composition-api", "nuxt", "#app", "@nuxtjs/composition-api", "pinia"}
)
_NESTJS_MODULES: frozenset[str] = frozenset(
    {
        "@nestjs/common", "@nestjs/core", "@nestjs/microservices",
        "@nestjs/websockets", "@nestjs/graphql", "@nestjs/typeorm",
        "@nestjs/jwt", "@nestjs/passport", "@nestjs/config",
        "@nestjs/swagger", "@nestjs/testing", "@nestjs/event-emitter",
        "@nestjs/schedule", "@nestjs/throttler",
    }
)
_ANGULAR_MODULES: frozenset[str] = frozenset(
    {
        "@angular/core", "@angular/common", "@angular/forms", "@angular/router",
        "@angular/http", "@angular/common/http", "@angular/platform-browser",
        "@angular/platform-browser-dynamic",
    }
)

# Class-level decorators that mark framework components
_FRAMEWORK_CLASS_DECORATORS: frozenset[str] = frozenset(
    {
        # NestJS
        "Controller", "Injectable", "Module", "Guard", "Interceptor", "Pipe",
        "ExceptionFilter", "Resolver", "WebSocketGateway", "EventsGateway",
        # Angular
        "Component", "Directive", "NgModule",
    }
)

# HTTP-method decorators that define NestJS routes
_HTTP_METHOD_DECORATORS: frozenset[str] = frozenset(
    {
        "Get", "Post", "Put", "Delete", "Patch", "Head", "Options", "All",
        "MessagePattern", "EventPattern", "SubscribeMessage", "GrpcMethod",
    }
)

_METHOD_KEYWORDS: frozenset[str] = frozenset(
    {
        "if", "for", "while", "switch", "catch", "super", "return",
        "typeof", "instanceof", "new", "delete", "void", "await", "throw",
        "case", "default", "import", "export", "from", "class", "extends",
        "implements", "interface", "type", "enum", "namespace", "module",
        "declare", "const", "let", "var", "function", "require",
    }
)

_TEST_FILE_INDICATORS = (
    ".test.", ".spec.", "__tests__", "test/", "tests/", "spec/", "specs/",
)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _is_test_file(path: str) -> bool:
    return any(i in path for i in _TEST_FILE_INDICATORS)


def _strip_comments(content: str) -> str:
    """Remove single-line (//) and block (/* */) JS/TS comments."""
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"(?m)[ \t]*//[^\n]*", "", content)
    return content


def _extract_vue_script(content: str) -> str:
    """Return the JS/TS source from a Vue SFC <script> block."""
    m = _VUE_SCRIPT_RE.search(content)
    return m.group(1) if m else content


def _extract_body(content: str, start: int) -> tuple[str, int]:
    """
    Extract the first { ... } block from content[start:], respecting nesting.

    Handles single/double/template-literal quoted strings.
    Returns (body_with_braces, start_offset) or ("", -1).
    """
    i = start
    n = len(content)
    while i < n and content[i] != "{":
        i += 1
    if i >= n:
        return "", -1

    body_start = i
    depth = 0
    in_string = False
    string_char = ""

    while i < n:
        ch = content[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == string_char:
                in_string = False
        else:
            if ch in ('"', "'", "`"):
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return content[body_start : i + 1], body_start
        i += 1

    return "", -1


def _decorators_before(content: str, pos: int) -> list[str]:
    """
    Scan backwards from `pos` in `content` to collect decorator names
    immediately preceding a class or function definition.

    Blank lines are skipped; any non-blank non-decorator line stops the scan.
    Returns a list in source order, e.g. ["Injectable", "Controller"].
    """
    before = content[:pos]
    lines = before.split("\n")
    decorators: list[str] = []

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue  # blank lines between decorators are allowed
        if stripped.startswith("@"):
            dm = re.match(r"@([\w.]+)", stripped)
            if dm:
                decorators.insert(0, dm.group(1))
            else:
                break
        else:
            break

    return decorators


def _detect_frameworks(modules: list[str]) -> set[str]:
    """Return a set of framework names inferred from import module names."""
    frameworks: set[str] = set()
    for m in modules:
        if any(m == r or m.startswith(r + "/") for r in _REACT_MODULES):
            frameworks.add("react")
        if any(m == r or m.startswith(r + "/") for r in _NEXTJS_MODULES):
            frameworks.add("next")
        if any(m == r or m.startswith(r + "/") for r in _VUE_MODULES):
            frameworks.add("vue")
        if any(m == r or m.startswith(r + "/") for r in _NESTJS_MODULES):
            frameworks.add("nestjs")
        if any(m == r or m.startswith(r + "/") for r in _ANGULAR_MODULES):
            frameworks.add("angular")
    return frameworks


def _is_react_component(name: str, exports: list[str]) -> bool:
    """Heuristic: PascalCase exported function is likely a React component."""
    return bool(name and name[0].isupper() and name in exports)


class JavascriptAnalyzer(BaseAnalyzer):
    """
    Analyzes JavaScript / TypeScript / Vue SFC source files.

    Framework-aware: detects React components, Next.js App Router handlers,
    Vue 3 Composition API macros, NestJS controller/service decorators,
    and Angular component/injectable decorators.
    """

    def analyze(self, file: FileNode) -> FileAnalysis:
        return self._safe_analyze(file, lambda: self._do_analyze(file))

    def _do_analyze(self, file: FileNode) -> FileAnalysis:  # noqa: C901
        # Vue SFCs: extract the <script> block before doing anything else
        raw = file.content
        if file.path.endswith(".vue"):
            raw = _extract_vue_script(raw)

        content = _strip_comments(raw)
        language = file.language.value

        imports = self._extract_imports(content)
        exports = self._extract_exports(content)
        classes = self._extract_classes(content)
        functions = self._extract_functions(content, exports)
        constants = self._extract_constants(content)

        # ── Framework-specific enrichment ─────────────────────────────────────
        modules = [i.module for i in imports]
        frameworks = _detect_frameworks(modules)

        # Next.js: "use client" / "use server" directives
        for m in _USE_DIRECTIVE_RE.finditer(content):
            directive = m.group("directive").upper()
            constants.append(f"USE_{directive}")

        # Next.js App Router: export async function GET/POST/...
        handler_names: set[str] = set()
        for m in _NEXTJS_HANDLER_RE.finditer(content):
            hname = m.group("name")
            if hname not in handler_names:
                handler_names.add(hname)
                existing_names = {f.name for f in functions}
                if hname not in existing_names:
                    decos = _decorators_before(content, m.start())
                    functions.append(
                        FunctionInfo(
                            name=hname,
                            is_async=True,
                            decorators=["@NextRouteHandler"] + decos,
                        )
                    )

        # Vue: detect defineProps / defineEmits / defineExpose / defineComponent
        if "vue" in frameworks or file.path.endswith(".vue"):
            if _VUE_DEFINE_PROPS_RE.search(content):
                constants.append("HAS_DEFINE_PROPS")
            if _VUE_DEFINE_EMITS_RE.search(content):
                constants.append("HAS_DEFINE_EMITS")
            if _VUE_DEFINE_EXPOSE_RE.search(content):
                constants.append("HAS_DEFINE_EXPOSE")
            if _VUE_DEFINE_COMPONENT_RE.search(content):
                constants.append("HAS_DEFINE_COMPONENT")
            # Collect Vue composable usages: useRouter(), useStore(), etc.
            seen_vue: set[str] = set()
            for vm in _VUE_USE_RE.finditer(content):
                token = f"VUE_USE_{vm.group(1).upper()}"
                if token not in seen_vue:
                    seen_vue.add(token)
                    constants.append(token)

        # React: collect hook usages
        if "react" in frameworks or "next" in frameworks:
            seen_hooks: set[str] = set()
            for hm in _REACT_HOOKS_RE.finditer(content):
                hook = hm.group(1)
                token = f"REACT_HOOK_{hook}"
                if token not in seen_hooks:
                    seen_hooks.add(token)
                    constants.append(token)

        return FileAnalysis(
            path=file.path,
            language=language,
            imports=imports,
            exports=exports,
            classes=classes,
            functions=functions,
            constants=list(dict.fromkeys(constants)),  # deduplicate, preserve order
            has_tests=_is_test_file(file.path),
            line_count=file.line_count,
        )

    # ── Imports ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_imports(content: str) -> list[ImportInfo]:
        imports: list[ImportInfo] = []
        seen: set[str] = set()

        for m in _IMPORT_RE.finditer(content):
            module = m.group("module") or m.group("side_effect")
            if not module or module in seen:
                continue
            seen.add(module)

            symbols: list[str] = []
            raw = m.group("named_only") or m.group("default_and_named") or ""
            brace_m = re.search(r"\{([^}]*)\}", raw)
            if brace_m:
                symbols = [
                    s.strip().split(" as ")[0].strip()
                    for s in brace_m.group(1).split(",")
                    if s.strip()
                ]

            imports.append(
                ImportInfo(
                    module=module,
                    symbols=symbols,
                    is_relative=module.startswith("."),
                )
            )

        # require() calls
        for m in _REQUIRE_RE.finditer(content):
            module = m.group("module")
            if module not in seen:
                seen.add(module)
                imports.append(ImportInfo(module=module, is_relative=module.startswith(".")))

        # Dynamic import() calls
        for m in _DYNAMIC_IMPORT_RE.finditer(content):
            module = m.group("module")
            if module not in seen:
                seen.add(module)
                imports.append(ImportInfo(module=module, is_relative=module.startswith(".")))

        return imports

    # ── Exports ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_exports(content: str) -> list[str]:
        exports: list[str] = []

        # Named exports: export { A, B as C }
        for m in _NAMED_EXPORT_RE.finditer(content):
            for name in m.group("names").split(","):
                clean = name.strip().split(" as ")[-1].strip()
                if clean:
                    exports.append(clean)

        # Declaration exports: export class Foo / export function bar
        for m in _EXPORT_DECL_RE.finditer(content):
            exports.append(m.group("name"))

        seen: set[str] = set()
        result: list[str] = []
        for e in exports:
            if e not in seen:
                seen.add(e)
                result.append(e)
        return result

    # ── Classes ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_classes(content: str) -> list[ClassInfo]:
        classes: list[ClassInfo] = []

        for m in _CLASS_RE.finditer(content):
            name = m.group("name")
            base = m.group("base")

            # Collect decorators immediately before this class definition
            decorators = _decorators_before(content, m.start())
            is_framework_class = any(d in _FRAMEWORK_CLASS_DECORATORS for d in decorators)

            # Extract class body for method introspection
            body, body_offset = _extract_body(content, m.start())
            methods: list[FunctionInfo] = []
            if body:
                methods = JavascriptAnalyzer._extract_class_methods(
                    body=body,
                    body_offset=body_offset,
                    full_content=content,
                    is_framework=is_framework_class,
                )

            classes.append(
                ClassInfo(
                    name=name,
                    parent_classes=[base] if base else [],
                    decorators=decorators,
                    methods=methods,
                )
            )

        return classes

    @staticmethod
    def _extract_class_methods(
        body: str,
        body_offset: int,
        full_content: str,
        *,
        is_framework: bool,  # noqa: ARG004 (reserved for future use)
    ) -> list[FunctionInfo]:
        """Extract method definitions from a class body string."""
        methods: list[FunctionInfo] = []
        seen: set[str] = set()

        # Always extract constructors (critical for DI in NestJS / Angular)
        for m in _CONSTRUCTOR_RE.finditer(body):
            if "constructor" not in seen:
                seen.add("constructor")
                abs_pos = body_offset + m.start()
                params_raw = m.group("params") or ""
                params = [p.strip() for p in params_raw.split(",") if p.strip()]
                methods.append(
                    FunctionInfo(
                        name="constructor",
                        parameters=params,
                        is_method=True,
                        decorators=_decorators_before(full_content, abs_pos),
                    )
                )

        # Extract methods with visibility/async qualifiers
        for m in _CLASS_METHOD_RE.finditer(body):
            name = m.group("name")
            if name in seen or name in _METHOD_KEYWORDS:
                continue
            seen.add(name)

            abs_pos = body_offset + m.start()
            decos = _decorators_before(full_content, abs_pos)

            params_raw = m.group("params") or ""
            params = [p.strip() for p in params_raw.split(",") if p.strip()]

            quals = m.group("quals") or ""
            is_async = "async" in quals

            return_type_raw = m.group("return_type")
            return_type = return_type_raw.strip() if return_type_raw else None

            methods.append(
                FunctionInfo(
                    name=name,
                    parameters=params,
                    return_type=return_type,
                    is_async=is_async,
                    is_method=True,
                    decorators=decos,
                )
            )

        return methods

    # ── Functions ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_functions(content: str, exports: list[str]) -> list[FunctionInfo]:
        functions: list[FunctionInfo] = []
        seen: set[str] = set()

        # Named function declarations
        for m in _FUNCTION_RE.finditer(content):
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)

            decos = _decorators_before(content, m.start())
            params = [p.strip() for p in m.group("params").split(",") if p.strip()]

            # Tag PascalCase exported functions as likely React components
            if _is_react_component(name, exports) and "ReactComponent" not in decos:
                decos = ["@ReactComponent"] + decos

            functions.append(
                FunctionInfo(
                    name=name,
                    parameters=params,
                    is_async=bool(m.group("async")),
                    decorators=decos,
                )
            )

        # Arrow functions (const Foo = () => ...)
        for m in _ARROW_RE.finditer(content):
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)

            decos = _decorators_before(content, m.start())

            # Tag PascalCase exported arrows as likely React components
            if _is_react_component(name, exports) and "ReactComponent" not in decos:
                decos = ["@ReactComponent"] + decos

            functions.append(
                FunctionInfo(
                    name=name,
                    is_async=bool(m.group("async")),
                    decorators=decos,
                )
            )

        return functions

    # ── Constants ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_constants(content: str) -> list[str]:
        """Extract UPPER_CASE constants (framework markers are appended by _do_analyze)."""
        pattern = re.compile(
            r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Z][A-Z0-9_]+)\s*=", re.MULTILINE
        )
        return list(dict.fromkeys(m.group(1) for m in pattern.finditer(content)))
