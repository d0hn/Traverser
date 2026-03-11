"""PHP static analyzer — regex-based, with deep Laravel/Eloquent awareness."""

from __future__ import annotations

import re
import logging

from traverser.analyzer.base_analyzer import BaseAnalyzer
from traverser.models.doc_models import ClassInfo, FileAnalysis, FunctionInfo, ImportInfo
from traverser.models.repo_models import FileNode

logger = logging.getLogger(__name__)

# ── Namespace / use ───────────────────────────────────────────────────────────

_NAMESPACE_RE = re.compile(r"^namespace\s+([\w\\]+)\s*;", re.MULTILINE)

# use App\Models\User;  |  use App\Models\User as Alias;
# Excludes `use function` and `use const` trait-method imports
_USE_SIMPLE_RE = re.compile(
    r"^use\s+(?!function\s|const\s)([\w\\]+(?:\\[\w]+)*)(?:\s+as\s+(\w+))?\s*;",
    re.MULTILINE,
)

# use App\Models\{User, Role, Permission as P};
_USE_GROUP_RE = re.compile(r"^use\s+([\w\\]+)\\\{([^}]+)\}\s*;", re.MULTILINE)

# ── Class-like structures ─────────────────────────────────────────────────────

# abstract class Foo extends Bar implements A, B {
_CLASS_RE = re.compile(
    r"^(?:(?:abstract|final|readonly)\s+)*"
    r"(?P<kind>class|interface|trait|enum)\s+"
    r"(?P<name>\w+)"
    r"(?:\s+extends\s+(?P<parent>[\w\\]+))?"
    r"(?:\s+implements\s+(?P<interfaces>[\w\\,\s]+?))?(?=\s*[\{;])",
    re.MULTILINE,
)

# ── Methods (used inside extracted class bodies) ──────────────────────────────

# [visibility] [static] [abstract] [final] function name(params): ?ReturnType
_METHOD_RE = re.compile(
    r"^[ \t]*(?:(?:public|protected|private)\s+)?"
    r"(?:static\s+)?(?:abstract\s+)?(?:final\s+)?"
    r"function\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*:\s*\??(?P<return_type>[\w\\|]+))?",
    re.MULTILINE,
)

# ── Standalone (top-level) functions ─────────────────────────────────────────

# ^function means no leading whitespace → top-level only (PSR-2/PSR-12 compliance assumed)
_STANDALONE_FN_RE = re.compile(
    r"^function\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*:\s*\??(?P<return_type>[\w\\|]+))?",
    re.MULTILINE,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_CONST_RE = re.compile(
    r"^[ \t]*(?:(?:public|protected|private)\s+)?const\s+([A-Z][A-Z0-9_]*)\s*=",
    re.MULTILINE,
)
_DEFINE_RE = re.compile(r"^\s*define\s*\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]", re.MULTILINE)

# ── Laravel-specific patterns ─────────────────────────────────────────────────

# Route::get('/path', ...)  Route::resource('photos', PhotoController::class)
_ROUTE_RE = re.compile(
    r"Route::(?P<method>get|post|put|patch|delete|any|resource|apiResource|view|redirect|match)\s*"
    r"\(\s*['\"](?P<uri>[^'\"]+)['\"]",
    re.MULTILINE,
)

# $this->hasMany(Post::class, ...)
_RELATIONSHIP_RE = re.compile(
    r"return\s+\$this->"
    r"(?P<rel>hasMany|hasOne|belongsTo|belongsToMany|morphTo|morphMany|morphOne"
    r"|hasManyThrough|hasOneThrough|morphToMany|morphedByMany)\s*"
    r"\(\s*(?P<related>[\w\\]+)::class",
    re.MULTILINE,
)

# protected $signature = 'emails:send {user} {--queue}';
_ARTISAN_SIG_RE = re.compile(
    r"(?:public|protected)?\s*\$signature\s*=\s*['\"]([^'\"]+)['\"]"
)

# Eloquent scope methods: public function scopeActive($query)
_SCOPE_METHOD_RE = re.compile(r"^[ \t]*.*function\s+scope([A-Z]\w+)\s*\(", re.MULTILINE)

# Laravel service container bindings
_BINDING_RE = re.compile(
    r"\$this->app->(?P<type>bind|singleton|instance|scoped)\s*\(",
    re.MULTILINE,
)

# ── Laravel class role detection ──────────────────────────────────────────────

# Map parent class / interface short-name → human-readable Laravel role
_LARAVEL_ROLES: dict[str, str] = {
    "Model": "EloquentModel",
    "Authenticatable": "AuthModel",
    "Controller": "Controller",
    "BaseController": "Controller",
    "InvokableController": "Controller",
    "FormRequest": "FormRequest",
    "Request": "FormRequest",
    "Middleware": "Middleware",
    "Job": "Job",
    "ShouldQueue": "Job",
    "Event": "Event",
    "Listener": "Listener",
    "ShouldHandleEventsAfterCommit": "Listener",
    "Notification": "Notification",
    "Mailable": "Mailable",
    "Policy": "Policy",
    "ServiceProvider": "ServiceProvider",
    "Command": "ArtisanCommand",
    "Resource": "ApiResource",
    "JsonResource": "ApiResource",
    "ResourceCollection": "ApiResource",
    "Seeder": "Seeder",
    "Migration": "Migration",
    "Factory": "Factory",
    "Rule": "ValidationRule",
    "ImplicitRule": "ValidationRule",
    "Channel": "BroadcastChannel",
    "Observer": "ModelObserver",
    "Scope": "QueryScope",
    "Enum": "BackedEnum",
}

_TEST_FILE_INDICATORS = ("Test.php", "test.php", "tests/", "Tests/", "Spec.php")


# ── Module-level helpers ──────────────────────────────────────────────────────


def _is_test_file(path: str) -> bool:
    return any(i in path for i in _TEST_FILE_INDICATORS)


def _strip_php_comments(content: str) -> str:
    """Remove PHP block, docblock, single-line // and # comments."""
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"(?m)[ \t]*//[^\n]*", "", content)
    content = re.sub(r"(?m)[ \t]*#[^\n]*", "", content)
    return content


def _extract_body(content: str, start: int) -> tuple[str, int]:
    """
    Find the first { ... } block from content[start:], respecting nesting.

    Handles single/double-quoted strings to avoid false-positive brace matches.
    Returns (body_with_braces, start_offset_in_content) or ("", -1).
    """
    i = start
    n = len(content)
    # Advance to opening brace (bail out at `;` — abstract/interface declaration)
    while i < n and content[i] not in ("{", ";"):
        i += 1
    if i >= n or content[i] == ";":
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
            if ch in ('"', "'"):
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


def _detect_laravel_role(parent: str | None, interfaces_str: str | None) -> str | None:
    """Return a Laravel role tag if parent/interfaces hint at one, else None."""
    candidates: list[str] = []
    if parent:
        candidates.append(parent.split("\\")[-1])
    if interfaces_str:
        for token in re.split(r"[\s,]+", interfaces_str):
            short = token.strip().split("\\")[-1]
            if short:
                candidates.append(short)
    for c in candidates:
        if c in _LARAVEL_ROLES:
            return _LARAVEL_ROLES[c]
    return None


def _clean_php_params(raw: str) -> list[str]:
    """
    Convert PHP parameter list to a clean list of parameter names.

    Input:  "Type $param1, ?OtherType $param2 = null, ...$rest"
    Output: ["param1", "param2", "rest"]
    """
    params: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        # Drop type hints — last token is the variable name
        tokens = part.split()
        var = tokens[-1] if tokens else part
        # Strip leading $ and ... (variadic)
        var = var.lstrip(".$").rstrip()
        # Drop default value noise: "$param = null" → "$param"
        var = var.split("=")[0].strip().lstrip("$").rstrip()
        if var:
            params.append(var)
    return params


# ── Analyzer class ────────────────────────────────────────────────────────────


class PhpAnalyzer(BaseAnalyzer):
    """Analyzes PHP source files with Laravel / Eloquent pattern awareness."""

    def analyze(self, file: FileNode) -> FileAnalysis:
        return self._safe_analyze(file, lambda: self._do_analyze(file))

    def _do_analyze(self, file: FileNode) -> FileAnalysis:
        content = _strip_php_comments(file.content)

        imports = self._extract_imports(content)
        classes = self._extract_classes(content)
        functions = self._extract_standalone_functions(content)
        constants = self._extract_constants(content)
        exports = self._build_exports(content, classes)

        return FileAnalysis(
            path=file.path,
            language=file.language.value,
            imports=imports,
            exports=exports,
            classes=classes,
            functions=functions,
            constants=constants,
            has_tests=_is_test_file(file.path),
            line_count=file.line_count,
        )

    # ── Imports ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_imports(content: str) -> list[ImportInfo]:
        imports: list[ImportInfo] = []
        seen: set[str] = set()

        # use App\Models\User;  or  use App\Models\User as Alias;
        for m in _USE_SIMPLE_RE.finditer(content):
            fqn = m.group(1)
            alias = m.group(2)
            module = fqn.replace("\\", "/")
            if module in seen:
                continue
            seen.add(module)
            short = fqn.split("\\")[-1]
            imports.append(
                ImportInfo(
                    module=module,
                    symbols=[alias or short],
                    is_relative=False,
                    alias=alias,
                )
            )

        # use App\Models\{User, Role as R};
        for m in _USE_GROUP_RE.finditer(content):
            base_ns = m.group(1).replace("\\", "/")
            for part in m.group(2).split(","):
                part = part.strip()
                if not part:
                    continue
                if " as " in part:
                    sym, alias = [x.strip() for x in part.split(" as ", 1)]
                else:
                    sym, alias = part, None
                module = f"{base_ns}/{sym}"
                if module in seen:
                    continue
                seen.add(module)
                imports.append(
                    ImportInfo(
                        module=module,
                        symbols=[alias or sym],
                        is_relative=False,
                        alias=alias,
                    )
                )

        return imports

    # ── Classes ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_classes(content: str) -> list[ClassInfo]:
        classes: list[ClassInfo] = []

        for m in _CLASS_RE.finditer(content):
            name = m.group("name")
            parent = m.group("parent")
            interfaces_str = m.group("interfaces")
            kind = m.group("kind")  # class | interface | trait | enum

            # Build parent_classes list (strip namespace prefix for readability)
            parents: list[str] = []
            if parent:
                parents.append(parent.split("\\")[-1])
            if interfaces_str:
                for tok in re.split(r"[\s,]+", interfaces_str):
                    short = tok.strip().split("\\")[-1]
                    if short:
                        parents.append(short)

            # Laravel role as a decorator tag for richer LLM context
            role = _detect_laravel_role(parent, interfaces_str)
            decorators: list[str] = []
            if role:
                decorators.append(f"@{role}")
            if kind != "class":
                decorators.append(f"@{kind}")  # mark interface / trait / enum

            # Extract class body and derive methods
            body, _ = _extract_body(content, m.start())
            methods = PhpAnalyzer._extract_methods(body, name) if body else []

            classes.append(
                ClassInfo(
                    name=name,
                    parent_classes=parents,
                    decorators=decorators,
                    methods=methods,
                )
            )

        return classes

    @staticmethod
    def _extract_methods(body: str, class_name: str) -> list[FunctionInfo]:
        """Extract method definitions from a PHP class body string."""
        methods: list[FunctionInfo] = []
        seen: set[str] = set()

        for m in _METHOD_RE.finditer(body):
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)

            params = _clean_php_params(m.group("params") or "")
            return_type = m.group("return_type")

            # Tag well-known Laravel method patterns
            decorators: list[str] = []
            if name == "__construct":
                decorators.append("@Constructor")
            elif name.startswith("scope") and len(name) > 5 and name[5].isupper():
                decorators.append(f"@Scope:{name[5:]}")
            elif name.startswith("get") and name.endswith("Attribute"):
                decorators.append(f"@Accessor:{name[3:-9]}")
            elif name.startswith("set") and name.endswith("Attribute"):
                decorators.append(f"@Mutator:{name[3:-9]}")

            # Detect Eloquent relationship inside method body (next ~300 chars)
            snippet_start = m.end()
            snippet = body[snippet_start : snippet_start + 300]
            rel_m = _RELATIONSHIP_RE.search(snippet)
            if rel_m:
                related = rel_m.group("related").split("\\")[-1]
                decorators.append(f"@{rel_m.group('rel')}:{related}")

            methods.append(
                FunctionInfo(
                    name=name,
                    parameters=params,
                    return_type=return_type,
                    is_method=True,
                    decorators=decorators,
                )
            )

        return methods

    # ── Standalone functions ───────────────────────────────────────────────────

    @staticmethod
    def _extract_standalone_functions(content: str) -> list[FunctionInfo]:
        """Extract top-level PHP functions (not class methods)."""
        functions: list[FunctionInfo] = []
        seen: set[str] = set()

        for m in _STANDALONE_FN_RE.finditer(content):
            name = m.group("name")
            if name in seen:
                continue
            seen.add(name)

            functions.append(
                FunctionInfo(
                    name=name,
                    parameters=_clean_php_params(m.group("params") or ""),
                    return_type=m.group("return_type"),
                    is_method=False,
                )
            )

        return functions

    # ── Constants ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_constants(content: str) -> list[str]:
        constants = [m.group(1) for m in _CONST_RE.finditer(content)]
        constants += [m.group(1) for m in _DEFINE_RE.finditer(content)]
        return list(dict.fromkeys(constants))

    # ── Exports (public API surface) ──────────────────────────────────────────

    @staticmethod
    def _build_exports(content: str, classes: list[ClassInfo]) -> list[str]:
        """
        Build the public-API export list for a PHP file.

        Includes class names, Laravel route definitions, and Artisan signatures.
        These are stored in FileAnalysis.exports so the LLM and relationship
        mapper can see them.
        """
        exports: list[str] = [cls.name for cls in classes]

        # Laravel routes: ROUTE:GET:/api/users
        for m in _ROUTE_RE.finditer(content):
            exports.append(f"ROUTE:{m.group('method').upper()}:{m.group('uri')}")

        # Artisan command: ARTISAN:emails:send
        for m in _ARTISAN_SIG_RE.finditer(content):
            sig = m.group(1).split(" ")[0]  # drop {arg} {--option} parts
            exports.append(f"ARTISAN:{sig}")

        # Eloquent relationships discovered in this file
        for m in _RELATIONSHIP_RE.finditer(content):
            related = m.group("related").split("\\")[-1]
            exports.append(f"REL:{m.group('rel')}:{related}")

        return list(dict.fromkeys(exports))
