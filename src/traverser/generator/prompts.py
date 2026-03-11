"""Prompt templates for all LLM calls made by Traverser."""

from __future__ import annotations

from string import Template

# ─────────────────────────────────────────────────────────────────────────────
# FILE DOCUMENTATION
# ─────────────────────────────────────────────────────────────────────────────

FILE_DOC_PROMPT = Template(
    """\
You are a highly precise technical documentation specialist. Your documentation \
will be consumed by AI agents that assist developers in debugging and understanding \
the codebase. Accuracy, specificity, and completeness are critical.

═══════════════════════════════════════════════════════════
FILE DETAILS
═══════════════════════════════════════════════════════════
Path      : $path
Language  : $language
Lines     : $line_count

═══════════════════════════════════════════════════════════
STATIC ANALYSIS
═══════════════════════════════════════════════════════════
$static_analysis

═══════════════════════════════════════════════════════════
RELATIONSHIP CONTEXT
═══════════════════════════════════════════════════════════
This file imports from  : $imports_from
This file is imported by: $imported_by

═══════════════════════════════════════════════════════════
FILE CONTENT
═══════════════════════════════════════════════════════════
```$language
$content
```

═══════════════════════════════════════════════════════════
TASK
═══════════════════════════════════════════════════════════
Write comprehensive documentation for this file. Use actual names from the code \
(classes, functions, variables, constants). Avoid generic statements. \
Be specific enough that an AI agent could answer: \
"I'm seeing error X in file Z — what should I check?"

Output exactly the following sections in Markdown:

## Overview
2–4 sentences. What is this file's purpose? Why does it exist?

## Architecture Role
3–5 sentences. Where does this file fit in the system? \
Is it a core module, utility, entry point, data layer, etc.? \
What would break if this file were removed?

## Public API
List every exported symbol (class, function, constant, type). \
For each: describe parameters, return values, and side effects. \
Use code-style formatting for signatures.

## Internal Logic
Explain the key algorithms, design patterns, and non-obvious implementation \
decisions. Include any important state management, caching, or concurrency patterns.

## Dependencies
For each import, explain what it provides and why this file needs it. \
Note any version-sensitive dependencies or known compatibility issues.

## Used By
Explain how the importing files ($imported_by) use this file. \
What functionality do they rely on?

## Common Issues & Gotchas
List specific bugs or misunderstandings that are likely given this code. \
Include edge cases, null/undefined handling, type coercion, race conditions, etc.

## Best Practices Demonstrated
What design patterns or coding standards does this file exemplify? \
What should other files in the project emulate?

## Debugging Guide
Step-by-step: if something is broken in or because of this file, \
what are the first three things to check? What log statements or breakpoints \
are most useful? What are the typical failure signatures?

## Testing Considerations
What are the critical test cases for this file? \
What is hard to test and why? Are there any existing test gaps?
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# BRIEF FILE DOCUMENTATION (3 sections — utilities and helpers)
# ─────────────────────────────────────────────────────────────────────────────

BRIEF_FILE_DOC_PROMPT = Template(
    """\
You are a concise technical documentation writer. \
Document this utility/helper file briefly and precisely.

Path     : $path
Language : $language
Lines    : $line_count

Static analysis: $static_analysis
Imports from  : $imports_from
Imported by   : $imported_by

```$language
$content
```

Write ONLY the following three sections in Markdown. Be direct — no padding.

## Overview
One or two sentences: what is this file’s single responsibility?

## Public API
List every exported symbol (function, class, constant, type). \
For each: one-line description and signature. Use code formatting.

## Common Issues & Gotchas
Up to three specific edge cases, pitfalls, or non-obvious behaviours. \
If there are none, write “None identified.”
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# BATCH BRIEF FILE DOCUMENTATION (multiple small files in one LLM call)
# ─────────────────────────────────────────────────────────────────────────────

BATCH_BRIEF_DOC_PROMPT = Template(
    """\
You are a concise technical documentation writer. \
Document each of the following utility/helper files briefly and precisely.

$file_blocks

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════
For EACH file above, produce documentation using this EXACT format \
(repeat for every file, including the separator):

---FILE_DOC: <exact file path>---

## Overview
One or two sentences: what is this file's single responsibility?

## Public API
List every exported symbol (function, class, constant, type). \
For each: one-line description and signature. Use code formatting.

## Common Issues & Gotchas
Up to three specific edge cases, pitfalls, or non-obvious behaviours. \
If there are none, write "None identified."

CRITICAL: You MUST include the ---FILE_DOC: path--- separator before \
EACH file's documentation. Include ALL $file_count files.
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# TESTS OVERVIEW (one aggregate doc instead of one page per test file)
# ─────────────────────────────────────────────────────────────────────────────

TESTS_OVERVIEW_PROMPT = Template(
    """\
You are documenting the test suite of a software project for an AI debugging assistant. \
The goal is to give a developer a fast map of what is and isn’t tested.

Project  : $repo_name
Test files: $test_count

$test_file_summaries

Write the following sections in Markdown:

## Test Coverage Summary
Which modules, services, and features are covered by tests? \
Be specific — name the modules.

## Test File Index
For each test file: one line in the format `path \u2014 what it tests`.

## Testing Patterns & Frameworks
What test runner, assertion library, and mock patterns are used across the suite?

## Likely Coverage Gaps
Based on which source files exist vs which test files exist, \
what is probably not tested? List specific files or modules.
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# ARCHITECTURE OVERVIEW
# ─────────────────────────────────────────────────────────────────────────────

ARCHITECTURE_PROMPT = Template(
    """\
You are a senior software architect writing for an AI agent knowledge base. \
Based on the information below, produce a comprehensive architecture document \
for the project. Use actual file names, class names, and function names from \
the codebase — never use placeholders.

═══════════════════════════════════════════════════════════
PROJECT
═══════════════════════════════════════════════════════════
Name        : $repo_name
Description : $description
Languages   : $languages
Total files : $total_files
Analysed    : $analysed_files

═══════════════════════════════════════════════════════════
HUB FILES (most imported)
═══════════════════════════════════════════════════════════
$hub_files

═══════════════════════════════════════════════════════════
ENTRY POINTS (imported by nothing)
═══════════════════════════════════════════════════════════
$entry_points

═══════════════════════════════════════════════════════════
CIRCULAR DEPENDENCIES
═══════════════════════════════════════════════════════════
$circular_deps

═══════════════════════════════════════════════════════════
FILE SUMMARIES (path → one-line purpose)
═══════════════════════════════════════════════════════════
$file_summaries

Write the following sections in Markdown:

## System Overview
What does this project do? Who uses it and how?

## Architecture Pattern
Identify the architectural pattern(s): layered, MVC, event-driven, microservices, \
hexagonal, etc. Justify your classification with specific file evidence.

## Key Components
For each major module/package, describe its responsibility and the key files within it.

## Data Flow
Trace the path of a typical request or operation from entry point to output. \
Reference actual file and function names.

## Entry Points
How is the system started/invoked? List all entry points with their purpose.

## Configuration
How is the system configured? What environment variables, config files, or \
flags are involved?

## External Dependencies
List the key third-party libraries and explain why they are used. \
Note any that are version-sensitive.

## Architecture Mermaid Diagram
Produce a high-level Mermaid `graph TD` diagram showing the main components \
and their relationships. Limit to 15–20 nodes for readability. Example:

```mermaid
graph TD
    CLI[CLI: cli.py] --> Pipeline[pipeline.py]
    Pipeline --> Fetcher[fetcher/github_fetcher.py]
    Pipeline --> Analyzer[analyzer/]
    Pipeline --> Generator[generator/doc_generator.py]
```
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# MIND MAP
# ─────────────────────────────────────────────────────────────────────────────

MINDMAP_PROMPT = Template(
    """\
You are producing a Mermaid mindmap for a software project knowledge base.

PROJECT: $repo_name
FILE TREE (indented):
$file_tree

Generate a Mermaid `mindmap` that shows the logical structure of the project. \
Group files by their functional area, NOT by directory (unless directory = function). \
Use concise labels (max 5 words). Include all major areas of the codebase.

Output ONLY the Mermaid mindmap block, nothing else.

Example format:
```mermaid
mindmap
  root(($repo_name))
    Core
      Config
      Models
    API Layer
      Routes
      Controllers
    Data Layer
      Database
      Repositories
    Utilities
      Logger
      Helpers
```
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# DEBUGGING GUIDE
# ─────────────────────────────────────────────────────────────────────────────

DEBUGGING_GUIDE_PROMPT = Template(
    """\
You are a debugging expert writing a comprehensive debugging guide for an \
AI agent knowledge base. The guide will be used when a developer says: \
"I'm seeing error X in file Y — help me debug it."

PROJECT: $repo_name

ARCHITECTURE SUMMARY:
$architecture_summary

FILE DOCUMENTATION SUMMARIES:
$file_summaries

HUB FILES (changes here have wide impact):
$hub_files

KNOWN CIRCULAR DEPENDENCIES:
$circular_deps

Write a detailed debugging guide with the following sections:

## How to Use This Guide
Brief instructions for an AI agent or developer.

## Common Error Categories
For each category (e.g., import errors, runtime exceptions, data validation, \
network/API failures, configuration issues): describe symptoms, likely causes, \
and the first three files to inspect.

## Critical File Checklist
For each hub file: what breaks when it has a bug, and what are the top \
3 things to verify.

## Data Flow Debugging
How to trace data through the system step-by-step. Which files to add \
logging/breakpoints at.

## Configuration & Environment Issues
Common misconfiguration problems and how to diagnose them.

## Dependency & Integration Issues
Common issues with third-party libraries used in this project.

## Performance Debugging
Where to look for bottlenecks based on the codebase structure.

## Testing Coverage Gaps
Based on the code structure, which areas are most likely under-tested.
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# GLOSSARY
# ─────────────────────────────────────────────────────────────────────────────

GLOSSARY_PROMPT = Template(
    """\
You are building a glossary for a software project knowledge base.

PROJECT: $repo_name

KEY SYMBOLS FOUND IN THE CODEBASE:
$symbols

Based on the symbols above, write a concise glossary. For each term:
- Give a one-sentence definition specific to this project.
- Note which file it is defined in (if known).
- Note which other terms it depends on (if any).

Format as a Markdown definition list:

**TermName** (`path/to/file.py`)
: Definition sentence. Depends on: OtherTerm, AnotherTerm.
"""
)


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY (compact top-level entry point — optimised for Copilot @workspace)
# ─────────────────────────────────────────────────────────────────────────────

SUMMARY_PROMPT = Template(
    """\
You are writing a compact repository summary that will serve as the PRIMARY \
entry point for an AI agent reading this codebase for the first time.

PROJECT     : $repo_name
DESCRIPTION : $description
LANGUAGES   : $languages
TOTAL FILES : $total_files
FOCUS PATH  : $focus_path

ARCHITECTURE SUMMARY (first 2000 chars):
$architecture_summary

HUB FILES (most imported):
$hub_files

ENTRY POINTS:
$entry_points

FILE SUMMARIES (path → one-line purpose):
$file_summaries

Write a SUMMARY document with the following sections. Keep it under 1500 words total. \
Use specific names (files, classes, functions) — never generic descriptions.

## What Is This?
2–3 sentences. What does this project do, who uses it, and what is the primary \
technology stack?

## Where to Start
List the 3–5 most important files a new developer should read first. \
For each: name, one-sentence purpose, and what it teaches.

## Architecture in 30 Seconds
The key layers/modules and how they connect. \
One Mermaid graph showing only the 6–8 most important connections. \
Example:
```mermaid
graph LR
    CLI --> Pipeline
    Pipeline --> Fetcher & Analyzer & Generator & Writer
```

## Key Concepts
Bullet list of 5–8 domain-specific concepts a developer must understand. \
One sentence each. Use names from the actual codebase.

## Navigation Guide
A table mapping "If you want to…" → "Look in…" with 8–12 rows.
Format:
| I want to… | Look in |
|------------|---------|
| Understand data models | `models/doc_models.py` |

## Common Entry Patterns
2–3 typical use cases / request flows with the files involved.
"""
)

# ─────────────────────────────────────────────────────────────────────────────
# COPILOT INSTRUCTIONS (.github/copilot-instructions.md)
# ─────────────────────────────────────────────────────────────────────────────

COPILOT_INSTRUCTIONS_PROMPT = Template(
    """\
You are writing the contents of `.github/copilot-instructions.md` for a \
GitHub Copilot workspace. This file is automatically loaded by GitHub Copilot \
to give it context about the repository. It must be concise (under 600 words), \
accurate, and contain the information Copilot needs to give great code suggestions.

PROJECT     : $repo_name
DESCRIPTION : $description
LANGUAGES   : $languages
TOTAL FILES : $total_files
FOCUS PATH  : $focus_path

ARCHITECTURE SUMMARY:
$architecture_summary

HUB FILES:
$hub_files

ENTRY POINTS:
$entry_points

FILE SUMMARIES:
$file_summaries

Write the Copilot instructions file. Do NOT use section headers with numbers. \
Use these exact H2 headings:

## Project Overview
2 sentences max. What this repo does and the primary tech stack.

## Architecture
3–5 bullet points covering the main layers/modules. \
Each bullet: module name + one-sentence role. Reference real filenames.

## Key Files
Bullet list of 5–8 critical files with one-line descriptions.
Format: `path/file.py` — what it does.

## Conventions
Bullet list of coding conventions, patterns, and rules specific to this project \
that Copilot should follow when suggesting code.

## Don't
Bullet list of 3–5 anti-patterns or mistakes to avoid in this codebase.
"""
)


def build_summary_prompt(
    repo_name: str,
    description: str,
    languages: list[str],
    total_files: int,
    focus_path: str | None,
    architecture_summary: str,
    hub_files: list[str],
    entry_points: list[str],
    file_summaries: str,
) -> str:
    return SUMMARY_PROMPT.substitute(
        repo_name=repo_name,
        description=description or "No description provided.",
        languages=", ".join(languages) or "unknown",
        total_files=total_files,
        focus_path=focus_path or "full repository",
        architecture_summary=architecture_summary[:2000],
        hub_files="\n".join(f"  - {f}" for f in hub_files[:15]) or "  (none detected)",
        entry_points="\n".join(f"  - {f}" for f in entry_points[:10]) or "  (none detected)",
        file_summaries=file_summaries,
    )


def build_copilot_instructions_prompt(
    repo_name: str,
    description: str,
    languages: list[str],
    total_files: int,
    focus_path: str | None,
    architecture_summary: str,
    hub_files: list[str],
    entry_points: list[str],
    file_summaries: str,
) -> str:
    return COPILOT_INSTRUCTIONS_PROMPT.substitute(
        repo_name=repo_name,
        description=description or "No description provided.",
        languages=", ".join(languages) or "unknown",
        total_files=total_files,
        focus_path=focus_path or "full repository",
        architecture_summary=architecture_summary[:1500],
        hub_files="\n".join(f"  - {f}" for f in hub_files[:10]) or "  (none detected)",
        entry_points="\n".join(f"  - {f}" for f in entry_points[:8]) or "  (none detected)",
        file_summaries=file_summaries,
    )


def build_file_doc_prompt(
    path: str,
    language: str,
    line_count: int,
    content: str,
    static_analysis: str,
    imports_from: list[str],
    imported_by: list[str],
) -> str:
    return FILE_DOC_PROMPT.substitute(
        path=path,
        language=language,
        line_count=line_count,
        content=content,
        static_analysis=static_analysis,
        imports_from=", ".join(imports_from) if imports_from else "nothing (leaf module)",
        imported_by=", ".join(imported_by) if imported_by else "nothing (entry point or unused)",
    )


def build_brief_file_doc_prompt(
    path: str,
    language: str,
    line_count: int,
    content: str,
    static_analysis: str,
    imports_from: list[str],
    imported_by: list[str],
) -> str:
    return BRIEF_FILE_DOC_PROMPT.substitute(
        path=path,
        language=language,
        line_count=line_count,
        content=content,
        static_analysis=static_analysis,
        imports_from=", ".join(imports_from) if imports_from else "nothing",
        imported_by=", ".join(imported_by) if imported_by else "nothing",
    )


def build_batch_brief_doc_prompt(
    file_blocks_data: list[dict[str, object]],
) -> str:
    """Build a single prompt to document multiple BRIEF files at once.

    *file_blocks_data* is a list of dicts, each with keys:
      path, language, line_count, content, static_analysis, imports_from, imported_by
    """
    blocks: list[str] = []
    for fb in file_blocks_data:
        imports_from = ", ".join(fb["imports_from"]) if fb["imports_from"] else "nothing"  # type: ignore[arg-type]
        imported_by = ", ".join(fb["imported_by"]) if fb["imported_by"] else "nothing"  # type: ignore[arg-type]
        blocks.append(
            f"═══════════════════════════════════════════════════════════\n"
            f"FILE: {fb['path']}\n"
            f"Language: {fb['language']} | {fb['line_count']} lines\n"
            f"Static analysis: {fb['static_analysis']}\n"
            f"Imports from: {imports_from}\n"
            f"Imported by: {imported_by}\n"
            f"═══════════════════════════════════════════════════════════\n"
            f"```{fb['language']}\n{fb['content']}\n```"
        )
    return BATCH_BRIEF_DOC_PROMPT.substitute(
        file_blocks="\n\n".join(blocks),
        file_count=len(file_blocks_data),
    )


def build_tests_overview_prompt(repo_name: str, test_file_summaries: str, test_count: int) -> str:
    return TESTS_OVERVIEW_PROMPT.substitute(
        repo_name=repo_name,
        test_count=test_count,
        test_file_summaries=test_file_summaries,
    )


def build_architecture_prompt(
    repo_name: str,
    description: str,
    languages: list[str],
    total_files: int,
    analysed_files: int,
    hub_files: list[str],
    entry_points: list[str],
    circular_deps: list[list[str]],
    file_summaries: str,
) -> str:
    return ARCHITECTURE_PROMPT.substitute(
        repo_name=repo_name,
        description=description or "No description provided.",
        languages=", ".join(languages) or "unknown",
        total_files=total_files,
        analysed_files=analysed_files,
        hub_files="\n".join(f"  - {f}" for f in hub_files) or "  (none detected)",
        entry_points="\n".join(f"  - {f}" for f in entry_points) or "  (none detected)",
        circular_deps=(
            "\n".join(f"  - {' → '.join(c)}" for c in circular_deps)
            if circular_deps
            else "  (none detected)"
        ),
        file_summaries=file_summaries,
    )


def build_mindmap_prompt(repo_name: str, file_tree: str) -> str:
    return MINDMAP_PROMPT.substitute(repo_name=repo_name, file_tree=file_tree)


def build_debugging_guide_prompt(
    repo_name: str,
    architecture_summary: str,
    file_summaries: str,
    hub_files: list[str],
    circular_deps: list[list[str]],
) -> str:
    return DEBUGGING_GUIDE_PROMPT.substitute(
        repo_name=repo_name,
        architecture_summary=architecture_summary,
        file_summaries=file_summaries,
        hub_files="\n".join(f"  - {f}" for f in hub_files) or "  (none detected)",
        circular_deps=(
            "\n".join(f"  - {' → '.join(c)}" for c in circular_deps)
            if circular_deps
            else "  (none detected)"
        ),
    )


def build_glossary_prompt(repo_name: str, symbols: str) -> str:
    return GLOSSARY_PROMPT.substitute(repo_name=repo_name, symbols=symbols)
