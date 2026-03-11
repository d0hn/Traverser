# AGENT_INSTRUCTIONS.md — Traverser Project

> This document is the primary reference for any AI agent or human contributor
> working on the **Traverser** project. Read it entirely before making changes.
> It covers: current state, design decisions, performance expectations,
> contribution standards, and detailed how-to guides for common tasks.

---

## Table of Contents

1. [Project Purpose](#1-project-purpose)
2. [Current State (v0.1.0)](#2-current-state-v010)
3. [Architecture Map](#3-architecture-map)
4. [Core Concepts and Invariants](#4-core-concepts-and-invariants)
5. [Performance Characteristics](#5-performance-characteristics)
6. [Standards for All Contributions](#6-standards-for-all-contributions)
7. [How to Write a New Analyzer](#7-how-to-write-a-new-analyzer)
8. [How to Write Tests](#8-how-to-write-tests)
9. [How to Add a New LLM Provider](#9-how-to-add-a-new-llm-provider)
10. [How to Add a New Prompt / Document Type](#10-how-to-add-a-new-prompt--document-type)
11. [How to Add a New CLI Command](#11-how-to-add-a-new-cli-command)
12. [Backlog and Roadmap](#12-backlog-and-roadmap)
13. [Known Limitations](#13-known-limitations)
14. [Glossary of Internal Terms](#14-glossary-of-internal-terms)

---

## 1. Project Purpose

Traverser converts any GitHub repository (public or private) into a
**NotebookLM-ready knowledge base**: a set of structured Markdown documents
that describe every file, the relationships between them, the architecture,
common debugging paths, and a full glossary.

The primary consumer of the output is an **AI agent** (in NotebookLM or
similar) that answers developer questions of the form:

> *"I'm seeing a `KeyError` in `services/user.py` — what's the likely cause,
> and which other files should I check?"*

The output must therefore be:
- **Specific** — actual class/function names, not generic descriptions.
- **Navigable** — every document cross-links related files.
- **Diagnostic** — each file doc includes a dedicated debugging guide.
- **Deterministic on re-run** — same file SHA → same output (via SHA-keyed cache).

---

## 2. Current State (v0.1.0)

### ✅ Implemented and tested

| Component | File(s) | Status |
|-----------|---------|--------|
| Data models | `models/repo_models.py`, `models/doc_models.py` | Complete |
| Config / env loading | `config.py` | Complete |
| GitHub fetcher | `fetcher/github_fetcher.py` | Complete |
| Python AST analyzer | `analyzer/python_analyzer.py` | Complete |
| JavaScript/TypeScript analyzer | `analyzer/javascript_analyzer.py` | Complete |
| PHP Laravel analyzer | `analyzer/php_analyzer.py` | Complete |
| Generic regex analyzer | `analyzer/generic_analyzer.py` | Complete |
| Relationship mapper (NetworkX) | `analyzer/relationship_mapper.py` | Complete |
| LLM providers (OpenAI, Anthropic) | `generator/llm_provider.py` | Complete |
| Prompt templates (9 types) | `generator/prompts.py` | Complete |
| Doc generator with diskcache | `generator/doc_generator.py` | Complete |
| 3-tier doc system (FULL/BRIEF/SKIP) | `generator/doc_generator.py` | Complete |
| Output writer (9 Markdown docs + copilot) | `output/writer.py` | Complete |
| Pipeline orchestrator | `pipeline.py` | Complete |
| CLI (generate / analyse / config / copilot) | `cli.py` | Complete |
| Repository snapshot cache | `fetcher/github_fetcher.py` | Complete |
| Test suite | `tests/` | 115 tests, 78% coverage |

### ❌ Not yet implemented (see Roadmap §12)

- Multi-repo / monorepo support
- Go, Rust, Java dedicated analyzers
- GitHub Actions / CI integration
- Web UI
- Ollama / local LLM support
- Confluence / Notion export adapters
- Token budget enforcement / chunking for very large files

---

## 3. Architecture Map

```
traverser generate <URL>
        │
        ▼
 pipeline.Pipeline.run()              ← async orchestrator
        │
   ┌────┴──────────────────────────────────────────────────────────────┐
   │  Step 1: fetcher.GithubFetcher.fetch()                           │
   │    • Authenticates with PyGithub                                 │
   │    • Walks git tree (recursive), filters skipped dirs + binaries │
  │    • Emits progress callbacks: on_tree(total), on_file()         │
   │    • Returns RepoInfo with list[FileNode]                        │
   └────┬──────────────────────────────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────────────────────────────┐
   │  Step 2: analyzer.get_analyzer(file).analyze(file)  [per file]  │
   │    • PythonAnalyzer   → ast.parse() → classes, functions, imports│
   │    • JavascriptAnalyzer → regex     → same shape                 │
   │    • GenericAnalyzer  → regex       → same shape                 │
   │    → dict[path, FileAnalysis]                                    │
   └────┬──────────────────────────────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────────────────────────────┐
   │  Step 3: analyzer.RelationshipMapper.build(analyses)             │
   │    • Resolves imports → file paths using module-to-path heuristic│
   │    • Builds NetworkX DiGraph                                     │
   │    • Identifies hub files, entry points, circular deps           │
   │    → ProjectRelationships                                        │
   └────┬──────────────────────────────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────────────────────────────┐
   │  Step 4: generator.DocGenerator.*  [async, concurrent]           │
   │    • generate_file_doc()      × N (semaphore-limited, 3-tier)   │
   │    • generate_tests_overview() (one aggregate doc, no per-file) │
   │    • generate_architecture()                                     │
   │    • generate_mind_map()                                         │
   │    • generate_debugging_guide()                                  │
   │    • generate_glossary()                                         │
   │    • generate_summary()       → SUMMARY.md                      │
   │    • generate_copilot_instructions() → .github/ instructions    │
   │    • generate_glossary()                                         │
   │    All calls: check diskcache → LLM → store in diskcache         │
   │    → dict[path, FileDocumentation] + 4 project-level strings     │
   └────┬──────────────────────────────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────────────────────────────┐
   │  Step 5: output.OutputWriter.write(KnowledgeBase)                │
   │    • SUMMARY.md (top-level entry point for Copilot @workspace)  │
   │    • 00_README.md … 05_GLOSSARY.md  [06_TESTS.md if tests exist]│
   │    • FILE_INDEX.md  (tier badges: 🟢/🟡/⚪)                      │
   │    • files/<safe_name>.md  × N                                   │
   │    • .github/copilot-instructions.md  (compact, < 600 words)    │
   │    • .traverser-state.json  (SHA map for --update diff mode)    │
   └──────────────────────────────────────────────────────────────────┘
```

### Data flow through models

```
GitHub API
  └─► FileNode (path, content, sha, language, size_bytes)
        └─► FileAnalysis (imports, classes, functions, constants, has_tests)
              ├─► ProjectRelationships (imports_from, imported_by, hub_files, …)
              └─► FileDocumentation (overview, public_api, debugging_guide, …)
                    └─► KnowledgeBase (all file analyses + docs + project artefacts)
                          └─► OutputWriter → Markdown files on disk
```

---

## 4. Core Concepts and Invariants

These rules must **never** be broken. Any contribution that violates them must
be rejected.

### 4.1 Analyzers never raise

Every `BaseAnalyzer.analyze()` implementation must catch all exceptions and
return a `FileAnalysis` with `error` populated rather than raising. This is
enforced by `BaseAnalyzer._safe_analyze()`. The pipeline must continue if one
file's analysis fails.

**Why:** A single malformed file (e.g., Python 2 syntax) must not abort
documentation of an entire repository.

### 4.2 Cache keys are content-addressed

File documentation cache keys are `f"file_doc:{VERSION}:{sha}:{model}"`.
The `sha` is the **git blob SHA** of the file, not a timestamp.

**Why:** Re-running on the same commit must return identical results without
hitting the LLM. Upgrading a model invalidates the cache automatically.
When prompt templates change significantly, increment `_CACHE_VERSION` in
`generator/doc_generator.py`.

### 4.3 All LLM calls go through `DocGenerator._call_llm()`

Never call `self._provider.complete()` directly from outside `DocGenerator`.
All LLM interactions must go through `_call_llm()` so that caching and
semaphore rate-limiting are applied uniformly.

### 4.4 Models are Pydantic v2, immutable by default

All data classes extend `pydantic.BaseModel`. Use `.model_copy(update={...})`
to derive modified instances. Do not add `model_config = ConfigDict(frozen=False)`
without a documented reason.

### 4.5 The pipeline is the only place that coordinates I/O

`Pipeline` orchestrates GitHub I/O, LLM I/O, and file I/O. Individual
components (analyzers, generators, writers) must be stateless or hold only
configuration — they must never initiate network connections or write files
on their own.

Progress rendering is also owned by `Pipeline`: `GithubFetcher` may emit
signals (`on_tree`, `on_file`), but only `Pipeline` should render Rich
progress bars/spinners.

### 4.6 Output is always Markdown

All generated artefacts are written as UTF-8 Markdown. No HTML, no PDF,
no JSON output files. The goal is maximum compatibility with NotebookLM,
Obsidian, GitHub rendering, and similar tools.

### 4.7 Prompts live only in `generator/prompts.py`

No prompt strings anywhere else. Prompts are Python `string.Template`
objects. Build functions (`build_*_prompt`) are the public interface —
they perform all string substitution and are the only things imported by
`DocGenerator`.

---

## 5. Performance Characteristics

### Expected timing (gpt-4o, 5 concurrent requests)

| Repo size | Files | Approximate time |
|-----------|-------|-----------------|
| Small     | ~20   | 1–2 min         |
| Medium    | ~100  | 5–10 min        |
| Large     | ~500  | 20–40 min       |
| Very large| ~2000 | 90–180 min      |

On a second run against the same commit with cache enabled, all times drop to
**< 30 seconds** (only project-level artefacts may regenerate if summaries differ).

### Token estimates per file (gpt-4o)

- Prompt: ~1,500–4,000 tokens (depends on file size and analysis)
- Response: ~800–1,500 tokens
- Per-file cost: ~$0.01–0.03 at gpt-4o pricing (as of 2026)

### Bottlenecks (in order)

1. **LLM rate limits** — mitigated by `asyncio.Semaphore(max_concurrent)`.
   Increase `MAX_CONCURRENT_LLM_REQUESTS` carefully; most providers throttle
   at 5–10 RPM on default tiers.
2. **GitHub API rate limit** — 5,000 req/hr authenticated, 60/hr unauthenticated.
   Always use a token. The recursive tree fetch uses 1 req; each file fetch
   uses 1 req. A 500-file repo uses ~501 GitHub API requests.
3. **Large files** — content is truncated at `max_content_chars` (default 80,000).
   Files over `max_file_size_kb` (default 500 KB) are skipped entirely.

---

## 6. Standards for All Contributions

### 6.1 Code style

- **Formatter/linter**: `ruff` (config in `pyproject.toml`). Run `ruff check src/ tests/` before committing.
- **Type checker**: `mypy --strict`. Run `mypy src/` before committing.
- **Line length**: 100 characters.
- **Import order**: standard library → third-party → first-party (enforced by `ruff`).
- **All new modules** must begin with `from __future__ import annotations`.
- **All public functions and classes** must have a one-line docstring minimum.

### 6.2 Test coverage requirements

- Every new public function/method must have at least one unit test.
- New analyzers need: happy path, edge case (empty file), error case (malformed input).
- Coverage for any new file must be ≥ 80%.
- Run `pytest --cov` before submitting and confirm no regressions.

### 6.3 Error handling rules

- Functions that call external services (GitHub, LLM) must use `tenacity` retry.
- Functions that parse untrusted input must wrap in try/except and return a
  safe degraded result, not raise.
- User-facing errors (CLI) must print a human-readable message with `[red]Error:[/red]`
  and exit with a non-zero code via `raise typer.Exit(1)`.
- Internal errors go to `logger.error()` or `logger.warning()`, never `print()`.

### 6.4 Naming conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Module-level private regex | `_ALL_CAPS_RE` | `_IMPORT_RE` |
| Module-level private constants | `_ALL_CAPS` | `_CACHE_VERSION` |
| Public constants (exported) | `ALL_CAPS` | `ANALYSABLE_LANGUAGES` |
| Analyzer classes | `<Language>Analyzer` | `PythonAnalyzer` |
| Private helper functions | `_snake_case` | `_strip_comments` |
| Cache key builders | `_<thing>_cache_key` | `_file_doc_cache_key` |

### 6.5 Git commit messages

Use the Conventional Commits format:

```
feat(analyzer): add Go analyzer with function and type extraction
fix(fetcher): handle repositories with no default branch set
test(analyzer): add edge cases for empty TypeScript files
refactor(prompts): split FILE_DOC_PROMPT into file and project sections
docs(instructions): update roadmap after completing Go analyzer
```

---

## 7. How to Write a New Analyzer

This is the most common contribution task. Follow every step.

### Step 1 — Decide which base to use

| Situation | Base to use |
|-----------|-------------|
| Language has a Python-native AST parser | Write a dedicated class extending `BaseAnalyzer` |
| Language uses C-family syntax (curly braces) | Extend `GenericAnalyzer` or write dedicated regex |
| Language is niche with < 5% codebase share | Add patterns to `_PATTERNS` dict in `generic_analyzer.py` |

### Step 2 — Create the analyzer file

Create `src/traverser/analyzer/<language>_analyzer.py`. Use this template:

```python
"""<Language> static analyzer — <brief description of approach>."""

from __future__ import annotations

import logging

from traverser.analyzer.base_analyzer import BaseAnalyzer
from traverser.models.doc_models import ClassInfo, FileAnalysis, FunctionInfo, ImportInfo
from traverser.models.repo_models import FileNode

logger = logging.getLogger(__name__)

# ── Module-level compiled patterns ────────────────────────────────────────────
# Compile ALL regex at module level — never inside functions.
# Use re.MULTILINE for patterns that match line starts/ends.
# Use verbose mode (re.VERBOSE) for complex patterns.
_IMPORT_RE = ...
_CLASS_RE = ...
_FUNCTION_RE = ...


class <Language>Analyzer(BaseAnalyzer):
    """Analyzes <Language> source files using <approach>."""

    def analyze(self, file: FileNode) -> FileAnalysis:
        # ALWAYS delegate to _safe_analyze — never call _do_analyze directly.
        return self._safe_analyze(file, lambda: self._do_analyze(file))

    def _do_analyze(self, file: FileNode) -> FileAnalysis:
        # 1. Pre-process content if needed (strip comments, etc.)
        # 2. Extract each category independently
        # 3. Return FileAnalysis — never raise here
        return FileAnalysis(
            path=file.path,
            language=file.language.value,
            imports=self._extract_imports(file.content),
            classes=self._extract_classes(file.content),
            functions=self._extract_functions(file.content),
            constants=self._extract_constants(file.content),
            has_tests=self._is_test_file(file.path),
            line_count=file.line_count,
        )

    @staticmethod
    def _extract_imports(content: str) -> list[ImportInfo]:
        # Return ImportInfo(module=..., symbols=[...], is_relative=...)
        ...

    @staticmethod
    def _extract_classes(content: str) -> list[ClassInfo]:
        # Return ClassInfo(name=..., parent_classes=[...])
        ...

    @staticmethod
    def _extract_functions(content: str) -> list[FunctionInfo]:
        # Return FunctionInfo(name=..., parameters=[...], is_async=...)
        ...

    @staticmethod
    def _extract_constants(content: str) -> list[str]:
        # Return list of constant names (UPPER_CASE convention or language equivalent)
        ...

    @staticmethod
    def _is_test_file(path: str) -> bool:
        ...
```

### Step 3 — Register the language detection

In `models/repo_models.py`, add your file extensions to `EXTENSION_TO_LANGUAGE`
and add the language to `ANALYSABLE_LANGUAGES` if it should trigger LLM docs.

```python
# In EXTENSION_TO_LANGUAGE:
".go": Language.GO,

# In ANALYSABLE_LANGUAGES:
Language.GO,
```

If the language enum value doesn't exist yet, add it to the `Language` enum first.

### Step 4 — Register the analyzer in `__init__.py`

In `analyzer/__init__.py`, add your analyzer to the `get_analyzer()` match:

```python
from traverser.analyzer.go_analyzer import GoAnalyzer

def get_analyzer(file: FileNode) -> BaseAnalyzer:
    match file.language:
        case Language.PYTHON:
            return PythonAnalyzer()
        case Language.GO:                    # ← add this
            return GoAnalyzer()
        case Language.JAVASCRIPT | Language.TYPESCRIPT:
            return JavascriptAnalyzer()
        case _:
            return GenericAnalyzer()
```

### Step 5 — What to extract (priority order)

Always attempt to extract, in this order. Stop if a category is too hard to
reliably extract with your approach — an empty list is better than wrong data.

1. **Imports** — module name + symbols. Mark `is_relative=True` for relative imports.
2. **Class definitions** — name + parent class names.
3. **Function/method definitions** — name + async flag. Parameters optional.
4. **Constants** — language-idiomatic uppercase names.
5. **Exports** — if the language has an explicit export mechanism.

### Step 6 — Verify against `FileAnalysis.to_summary_text()`

This method is used in LLM prompts. Run it against a real file from the
target language and read the output. It must produce a sensible one-line
summary. If it produces garbage, fix your extraction logic before proceeding.

### Step 7 — What NOT to do

- ❌ Do not extract line-by-line content (leave that for the LLM).
- ❌ Do not attempt to resolve types or evaluate expressions.
- ❌ Do not spawn subprocesses to use a language's native parser.
- ❌ Do not store state between `analyze()` calls — analyzers must be stateless.
- ❌ Do not use `re.match()` for patterns that need to match within a file
  (use `re.finditer()` with `re.MULTILINE`).

---

## 8. How to Write Tests

### 8.1 Where tests live

```
tests/
├── conftest.py          ← Shared fixtures (FileNode samples, RepoInfo)
├── test_analyzer.py     ← Unit tests for all analyzers + RelationshipMapper
├── test_fetcher.py      ← Unit tests for GitHub URL parsing + file filters
├── test_generator.py    ← Unit tests for prompt builders + LLM response parsing
├── test_output.py       ← Unit tests for OutputWriter
└── test_pipeline.py     ← Integration tests (mocked GitHub + LLM)
```

### 8.2 Fixture conventions

Add reusable file content as **module-level string constants** in `conftest.py`,
not as inline strings inside test methods. Name them `<LANGUAGE>_SAMPLE`.

Add `FileNode` fixtures as pytest fixtures returning a `FileNode`:

```python
# In conftest.py
GO_SAMPLE = '''\
package main

import (
    "fmt"
    "os"
)

type Server struct {
    Port int
}

func (s *Server) Start() error {
    fmt.Println("Starting on port", s.Port)
    return nil
}

func main() {
    s := &Server{Port: 8080}
    s.Start()
}
'''

@pytest.fixture
def go_file() -> FileNode:
    return FileNode(
        path="cmd/server/main.go",
        name="main.go",
        language=Language.GO,
        size_bytes=len(GO_SAMPLE),
        content=GO_SAMPLE,
        sha="gosha123",
    )
```

### 8.3 Required test cases for every new analyzer

For a language called `<Lang>` with analyzer `<Lang>Analyzer`, you must write:

```python
class Test<Lang>Analyzer:
    def setup_method(self) -> None:
        self.analyzer = <Lang>Analyzer()

    # 1. Imports
    def test_extracts_imports(self, <lang>_file: FileNode) -> None:
        result = self.analyzer.analyze(<lang>_file)
        assert "<known_module>" in result.imported_modules

    # 2. Classes
    def test_extracts_classes(self, <lang>_file: FileNode) -> None:
        result = self.analyzer.analyze(<lang>_file)
        assert "<KnownClass>" in result.all_class_names

    # 3. Functions
    def test_extracts_functions(self, <lang>_file: FileNode) -> None:
        result = self.analyzer.analyze(<lang>_file)
        assert "<known_function>" in result.all_function_names

    # 4. Line count is non-zero
    def test_line_count(self, <lang>_file: FileNode) -> None:
        result = self.analyzer.analyze(<lang>_file)
        assert result.line_count > 0

    # 5. Error handling — malformed input must not raise
    def test_malformed_input_does_not_raise(self) -> None:
        bad = FileNode(
            path="bad.<ext>",
            name="bad.<ext>",
            language=Language.<LANG>,
            size_bytes=5,
            content="@@@###$$$",  # deliberately invalid
            sha="bad",
        )
        result = self.analyzer.analyze(bad)
        # Must not raise; may return empty or set error field
        assert isinstance(result, FileAnalysis)

    # 6. Empty file
    def test_empty_file_does_not_raise(self) -> None:
        empty = FileNode(
            path="empty.<ext>",
            name="empty.<ext>",
            language=Language.<LANG>,
            size_bytes=0,
            content="",
            sha="empty",
        )
        result = self.analyzer.analyze(empty)
        assert result.error is None
        assert result.classes == []
        assert result.functions == []
```

### 8.4 Integration test requirements

For any new pipeline feature:

1. Use `mocker.patch.object(pipeline._fetcher, "fetch", return_value=mock_repo)` to
   avoid real GitHub calls.
  - If you assert arguments, remember `Pipeline` now calls `fetch()` with
    progress kwargs (`on_tree`, `on_file`).
2. Use `mocker.patch.object(pipeline._generator._provider, "complete", new=async_mock)`
   to avoid real LLM calls.
3. Verify the happy path produces the expected output **files on disk**
   (use `tmp_path` fixture — never write to real directories in tests).
4. Verify at least one failure scenario (e.g., one file malformed) does not abort the pipeline.

### 8.5 What NOT to test

- ❌ Do not test that `str.split()` or stdlib functions work.
- ❌ Do not test Pydantic model validation — Pydantic is already tested.
- ❌ Do not mock the `FileNode` or `FileAnalysis` constructors — use real instances.
- ❌ Do not write tests that make real network calls (GitHub, LLM APIs).
- ❌ Do not use `time.sleep()` in tests.

### 8.6 Async tests

Mark async tests with `@pytest.mark.asyncio`. The `asyncio_mode = "auto"` setting
in `pyproject.toml` makes this automatic — you still need the decorator as
explicit documentation. Use `pytest-mock`'s `mocker.AsyncMock` for async mocks.

---

## 9. How to Add a New LLM Provider

1. Create a class in `generator/llm_provider.py` implementing:
   - `async def complete(self, prompt: str) -> str`
   - `@property def model_name(self) -> str`

2. Apply `@retry` with `tenacity` for the provider's specific exception types.

3. Add an enum value to `LLMProvider` in `config.py`.

4. Add an `api_key` field for the new provider to `Config` in `config.py`.

5. Update `require_api_key()` and `active_api_key` in `Config` to handle
   the new provider.

6. Update `build_provider()` in `llm_provider.py` to return the new class.

7. Update `show_config()` in `cli.py` to display the new key.

8. Add `[provider]_api_key=` to `.env.example`.

9. Write a unit test in `test_generator.py` that mocks the new provider's
   HTTP client and verifies `complete()` returns the expected string.

---

## 10. How to Add a New Prompt / Document Type

Every new LLM-generated document requires changes in three places:

### 10.1 Add the prompt template to `generator/prompts.py`

```python
NEW_DOC_PROMPT = Template("""\
You are ...

PROJECT: $repo_name
...

Write the following sections:
## Section One
...
""")

def build_new_doc_prompt(repo_name: str, ...) -> str:
    return NEW_DOC_PROMPT.substitute(repo_name=repo_name, ...)
```

Rules for prompt templates:
- Use `string.Template` with `$variable` placeholders (not f-strings).
- Open with a **role statement**: *"You are a [role] writing for [audience]."*
- Provide **structured input** sections with clear labels.
- Specify **exact output sections** using `## H2 headings`.
- Use actual names from the codebase in examples — never `Foo.bar()`.
- Keep prompts under ~3,000 tokens for the system portion.

### 10.2 Add a generator method to `DocGenerator`

```python
async def generate_new_doc(self, repo_info: RepoInfo, ...) -> str:
    content = _build_content_for_new_doc(...)
    prompt = build_new_doc_prompt(repo_name=repo_info.display_name, ...)
    cache_key = _project_cache_key(
        "new_doc",
        repo_info.full_name,
        self._provider.model_name,
        _content_hash(content),
    )
    return await self._call_llm(prompt, cache_key)
```

### 10.3 Add the field to `KnowledgeBase` and wire it in the pipeline

In `models/doc_models.py`:
```python
class KnowledgeBase(BaseModel):
    ...
    new_doc_content: str = ""     # ← add field
```

In `pipeline.py` (Step 4):
```python
new_doc = await self._with_spinner(
    "Generating new doc...",
    self._generator.generate_new_doc(repo_info, ...),
)
```

In `pipeline.py` (Step 5, KnowledgeBase construction):
```python
kb = KnowledgeBase(
    ...
    new_doc_content=new_doc,
)
```

### 10.4 Write the output file in `OutputWriter`

```python
def _write_new_doc(self, root: Path, kb: KnowledgeBase) -> None:
    content = f"""\
# New Doc Title — {kb.owner}/{kb.repo_name}

{kb.new_doc_content}

---
*Generated by Traverser — {kb.generated_at}*
"""
    (root / "06_NEW_DOC.md").write_text(content, encoding="utf-8")
```

Call it from `OutputWriter.write()`.

### 10.5 Update `00_README.md` document index

Update `_write_readme()` in `output/writer.py` to include the new file in the
document index table.

---

## 11. How to Add a New CLI Command

1. Add a new function to `cli.py` decorated with `@app.command()`.
2. Use `typer.Argument` for required positional args, `typer.Option` for optional ones.
3. Load `Config` inside the command function (not at module level — config reads `.env`
   at instantiation time).
4. Validate prerequisites eagerly (API keys, paths) before starting expensive work.
5. All progress output goes to `console` (stdout). All error messages to `err_console` (stderr).
6. Use `raise typer.Exit(1)` for user-facing errors.
7. Add `--help` text that includes a one-line example.

---

## 12. Backlog and Roadmap

Items are ordered by estimated impact. Pick the top unstarted item unless
the user specifies otherwise.

### P0 — High value, low risk

- [x] **Diff/update mode** (`--update`): Persists `.traverser-state.json` next
  to output. On re-run, unchanged files (same SHA) skip the LLM call — diskcache
  returns instantly. Changed and new files are fully regenerated.

- [x] **SUMMARY.md + Copilot instructions**: Every run now produces a compact
  `SUMMARY.md` (< 1,500 words) as the top-level entry point, and a
  `.github/copilot-instructions.md` for GitHub Copilot workspace context.

- [x] **`--focus` flag**: Filters the fetched file list to a subpath prefix
  before analysis. Works on both `generate` and `analyse` commands.

- [ ] **Go dedicated analyzer**: Use the `ast` equivalent for Go (parse
  `package`, `import`, `type ... struct`, `func`). Go is widely used and the
  generic regex misses receiver functions.

- [ ] **Java dedicated analyzer**: Regex-based (no JVM dep needed). Extract
  `import`, `class`/`interface`, `public` methods with return types.

- [ ] **Rust dedicated analyzer**: Regex-based. Extract `use`, `struct`/`enum`/`trait`,
  `pub fn`, `impl` blocks.

- [ ] **Token budget enforcement**: Before sending to LLM, count tokens with
  `tiktoken`. If prompt exceeds model's context window, chunk the file and
  make multiple calls, then merge responses.

- [ ] **`--include-patterns` / `--exclude-patterns` CLI flags**: Allow users
  to specify glob patterns to restrict which files are processed. Useful for
  polyglot repos where only one language is relevant.

### P1 — Medium value, moderate complexity

- [ ] **Ollama / local LLM support**: Add `OllamaProvider` in `llm_provider.py`.
  The Ollama API is OpenAI-compatible, so this is minimal work. Add
  `OLLAMA_BASE_URL` to config.

- [ ] **Multiple output formats**: Add an `--output-format json` option that
  writes the full `KnowledgeBase` as JSON (already Pydantic-serialisable with
  `.model_dump_json()`). Useful for programmatic downstream processing.

- [ ] **Mermaid diagram validation**: After generating Mermaid blocks, check
  they are valid syntax (look for balanced brackets, no illegal characters in
  node IDs). Fix common issues before writing to file.

- [ ] **Monorepo support**: Accept multiple sub-paths within a single repo.
  Run analyzers independently per sub-path, then build a cross-subproject
  relationship map.

- [ ] **GitHub Actions workflow template**: Generate a `.github/workflows/traverse.yml`
  file that re-runs Traverser on push to main, commits updated docs to a
  `docs/ai-knowledge-base` branch.

### P2 — Lower priority / nice to have

- [ ] **Confluence export adapter**: Write `output/confluence_writer.py` that
  converts the KnowledgeBase Markdown into Confluence REST API calls.

- [ ] **Obsidian vault mode**: Rewrite cross-file links as `[[wiki-links]]`.

- [ ] **Web UI** (FastAPI + HTMX): Simple form where a user pastes a GitHub URL,
  sees a progress stream, and downloads a ZIP of the knowledge base.

- [ ] **Diff-aware change summary**: Given two runs (two SHAs), generate a
  "What changed?" document highlighting the files that changed and summarising
  the diffs in plain English.

---

## 13. Known Limitations

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| Python 2 syntax causes AST parse errors | `error` field set; file skipped in analysis but still LLM-documented using raw content | None needed — gracefully degraded |
| JS/TS regex misses destructuring imports | Some symbols not captured in `ImportInfo.symbols` | LLM still sees raw content; relationship resolution unaffected |
| Relationship resolution is heuristic only | Some import→file mappings not resolved (especially dynamic imports, re-exports) | LLM sees relationship context anyway; graph is conservative |
| Files > `max_content_chars` are truncated | End of large files not analysed by LLM | Increase `MAX_CONTENT_CHARS` env var; or split the file |
| Binary files silently skipped | Documented in README | By design |
| `diskcache` on NFS/network shares | SQLite may lock | Use `CACHE_DIR` pointing to local disk |
| No support for GitLab, Bitbucket | Only GitHub URLs parsed | Extend `parse_github_url()` and add provider-specific fetcher |
| Circular dependency detection limited to chains ≤ 5 | Long chains not reported | Change `len(cycle) <= 5` guard in `relationship_mapper.py` |

---

## 14. Glossary of Internal Terms

**`BaseAnalyzer`**
: Abstract base class in `analyzer/base_analyzer.py`. All language analyzers
  extend this. Provides `_safe_analyze()` to wrap implementations in error
  handling. Never instantiated directly.

**`DocGenerator`**
: The class in `generator/doc_generator.py` responsible for all LLM interactions.
  Owns the `diskcache.Cache`, the `asyncio.Semaphore`, and the LLM provider.
  Must be used as a context manager to ensure the cache is closed.

**`FileAnalysis`**
: Output of static analysis. Contains structured metadata extracted without LLM
  involvement: imports, classes, functions, constants. Lives in `models/doc_models.py`.

**`FileDocumentation`**
: Output of LLM documentation for one file. Contains ten structured Markdown
  sections. Lives in `models/doc_models.py`. Keyed by `sha` for cache invalidation.

**`FileNode`**
: A single file fetched from GitHub. Contains `path`, `content`, `sha`, `language`,
  `size_bytes`. Lives in `models/repo_models.py`. Immutable.

**`get_analyzer(file)`**
: Factory function in `analyzer/__init__.py`. Returns the correct `BaseAnalyzer`
  subclass for the file's language. This is the only place language→analyzer
  dispatch happens.

**Hub file**
: A file with high in-degree in the dependency graph (imported by many others).
  Identified by `RelationshipMapper`. Changes to hub files have wide blast radius.

**`KnowledgeBase`**
: Top-level aggregate model in `models/doc_models.py`. Contains everything:
  all `FileAnalysis`, all `FileDocumentation`, `ProjectRelationships`, and
  all project-level artefacts. Serialisable to JSON via `.model_dump_json()`.

**`LLMProvider`** (protocol)
: Any class with `async complete(prompt: str) -> str` and `model_name: str`.
  Currently `OpenAIProvider` and `AnthropicProvider`.

**`Pipeline`**
: The async orchestrator in `pipeline.py`. The only class that coordinates
  all components. Entry point is `pipeline.run(url)`.

**`ProjectRelationships`**
: The resolved dependency graph. Built by `RelationshipMapper.build()`.
  Contains `imports_from`, `imported_by`, `hub_files`, `entry_points`,
  `circular_deps`.

**`RepoInfo`**
: Repository metadata + list of all `FileNode`s. Returned by `GithubFetcher.fetch()`.

**`_safe_filename(path)`**
: Converts a repo-relative path to a flat, filesystem-safe filename for output.
  E.g., `src/api/client.py` → `src_api_client_py.md`.

**SHA-keyed cache**
: Cache strategy where the key includes the git blob SHA. Guarantees that
  re-running against the same commit returns cached results. Changing the
  file or upgrading the model invalidates automatically.
