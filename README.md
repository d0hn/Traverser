# Traverser 🔍

> **AI-powered documentation generator** — point it at any GitHub repository and it produces a rich, Language-Model-ready knowledge base that lets AI agents answer detailed debugging questions about the codebase.

---

## What it does

1. **Fetches** every source file from a GitHub repository (public or private).
2. **Analyses** the code statically — extracting imports, classes, functions, and cross-file relationships.
3. **Documents** each file via an LLM — purpose, public API, internal logic, debugging guide, and more.
4. **Generates** project-level artifacts: architecture overview, mind map, relationship graph, debugging guide, a compact `SUMMARY.md`, and a `.github/copilot-instructions.md` for GitHub Copilot.
5. **Writes** clean Markdown output ready to be uploaded as a NotebookLM source collection or consumed by GitHub Copilot.
6. **Shows** live terminal progress (fetching, analysis, generation, and writing) so long runs stay transparent.

---

## Quick start

```bash
# 1. Install (uv recommended)
pip install uv
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# edit .env — set LLM_PROVIDER (github-copilot needs no API key;
#              openai/anthropic need OPENAI_API_KEY or ANTHROPIC_API_KEY)

# 3. Run
traverser generate https://github.com/owner/repo

# Private repo
traverser generate https://github.com/owner/private-repo --token ghp_xxx

# Use Anthropic instead of OpenAI
traverser generate https://github.com/owner/repo --provider anthropic --model claude-3-5-sonnet-20241022

# Use GitHub Copilot CLI (free with your Copilot subscription, no API key needed)
brew install copilot-cli  # one-time setup
traverser generate https://github.com/owner/repo --provider github-copilot

# Copilot CLI with a specific model
traverser generate https://github.com/owner/repo --provider github-copilot --model claude-opus-4.6

# Analyse only a subdirectory (great for large monorepos)
traverser generate https://github.com/owner/repo --focus src/services

# Re-process only files changed since the last run (fast incremental update)
traverser generate https://github.com/owner/repo --update

# Generate GitHub Copilot workspace instructions only
traverser copilot https://github.com/owner/repo
```

---

## Output structure

```
output/owner_repo/
├── SUMMARY.md              ← start here — compact top-level entry point (Copilot @workspace)
├── 00_README.md            ← meta-document with stats and document index
├── 01_ARCHITECTURE.md      ← system overview + Mermaid architecture diagram
├── 02_MIND_MAP.md          ← Mermaid mindmap of the project structure
├── 03_RELATIONSHIPS.md     ← file dependency graph (Mermaid)
├── 04_DEBUGGING_GUIDE.md   ← common error patterns & investigation steps
├── 05_GLOSSARY.md          ← key terms and concepts
├── 06_TESTS.md             ← aggregate test-suite overview (if tests exist)
├── FILE_INDEX.md           ← all documented files with tier badges (🟢/🟡/⚪)
├── .github/
│   └── copilot-instructions.md  ← GitHub Copilot workspace context (<600 words)
├── .traverser-state.json   ← SHA state for --update diff mode
└── files/
    ├── src__app__main.md   ← per-file documentation
    └── ...
```

Upload the entire `output/owner_repo/` folder to NotebookLM as a source collection.  
Copy `.github/copilot-instructions.md` to your repository root to give GitHub Copilot automatic workspace context.

---

## CLI reference

### `traverser generate` — full knowledge base

```
traverser generate URL [OPTIONS]
  URL                   GitHub repository URL (public or private)
  --token/-t TEXT       GitHub PAT (overrides GITHUB_TOKEN env var)
  --output-dir/-o PATH  Where to write output (default: ./output)
  --provider TEXT       LLM provider: openai | anthropic | github-copilot
  --model/-m TEXT       LLM model name (e.g. gpt-4o, claude-3-5-sonnet-20241022)
  --no-cache            Disable LLM response caching
  --dry-run             Fetch and analyse only — no LLM calls, no output files
  --focus/-F TEXT       Restrict analysis to a subpath prefix (e.g. src/services)
  --update/-u           Re-process only files whose SHA changed since last run
```

**LLM Providers:**

| Provider | Cost | Setup | Models |
|----------|------|-------|--------|
| `openai` | Pay-per-token | `OPENAI_API_KEY` env var | gpt-4o, gpt-5, o3, etc. |
| `anthropic` | Pay-per-token | `ANTHROPIC_API_KEY` env var | claude-3-5-sonnet, claude-3-opus, etc. |
| `github-copilot` | Included with Copilot subscription | `brew install copilot-cli` | claude-sonnet-4.6, gpt-5.2, claude-opus-4.6, o3, etc. |

### `traverser copilot` — GitHub Copilot instructions only

```
traverser copilot URL [OPTIONS]
  URL                   GitHub repository URL
  --token/-t TEXT       GitHub PAT
  --output-dir/-o PATH  Where to write output (default: ./output)
  --provider TEXT       LLM provider: openai | anthropic | github-copilot
  --model/-m TEXT       LLM model name
  --focus/-F TEXT       Restrict analysis to a subpath prefix
```

Generates `SUMMARY.md` and `.github/copilot-instructions.md`. Copy the instructions file to your repo root — GitHub Copilot picks it up automatically on every conversation in that workspace.

### `traverser context` — GitHub Copilot CLI context

```
traverser context URL [OPTIONS]
  URL                   GitHub repository URL
  --token/-t TEXT       GitHub PAT
  --output/-o PATH      Write context to file (optional; stdout by default)
  --focus/-F TEXT       Restrict analysis to a subpath prefix
```

Generates a compact, unified context file for **GitHub Copilot CLI** (`gh copilot`). Output can be piped directly to Copilot CLI commands:

```bash
# Query your codebase with Copilot CLI (no expensive LLM API calls)
gh copilot suggest "how do I implement authentication" \
  < <(traverser context https://github.com/owner/repo)

# Or save context to a file for reuse
traverser context https://github.com/owner/repo -o context.md
gh copilot suggest "question" < context.md
```

### `traverser analyse` — static analysis only (no LLM)

```
traverser analyse URL [OPTIONS]
  URL                   GitHub repository URL
  --token/-t TEXT       GitHub PAT
  --format/-f TEXT      Output format: table | json (default: table)
  --focus/-F TEXT       Restrict analysis to a subpath prefix
```

### `traverser config` — show resolved configuration

```
traverser config
```

---

## GitHub Copilot CLI integration

### As an LLM provider (recommended)

Use the new standalone GitHub Copilot CLI (`copilot` binary) as your LLM provider — generates full documentation using your Copilot subscription, no separate API keys needed:

```bash
# One-time setup
brew install copilot-cli       # or: curl -fsSL https://gh.io/copilot-install | bash
copilot                        # first run: authenticate with your GitHub account

# Generate full documentation
traverser generate https://github.com/owner/repo --provider github-copilot

# Pick a specific model
traverser generate https://github.com/owner/repo --provider github-copilot --model claude-opus-4.6

# Available models: claude-sonnet-4.6, claude-opus-4.6, gpt-5.2, o3, gpt-4o, and more
```

### Lightweight context for ad-hoc queries

Use `traverser context` to generate a compact context file for ad-hoc Copilot CLI questions — no LLM calls, instant:

```bash
copilot -p "explain the authentication flow" < <(traverser context https://github.com/owner/repo)
```

---

## NotebookLM integration

For richer analysis using NotebookLM:

```bash
# Generate full knowledge base
traverser generate https://github.com/owner/repo

# Upload output/owner_repo/ folder to NotebookLM as a source collection
# NotebookLM will use that context for all follow-up questions
```

---

## Doc tier system (cost optimisation)

Traverser uses a 3-tier documentation strategy to reduce LLM costs by ~50–55%:

| Tier | Badge | Criteria | LLM sections |
|------|-------|----------|-------------|
| **FULL** | 🟢 | Hub files, entry points, files > 100 lines, ≥2 classes or ≥6 functions | 10-section deep-dive |
| **BRIEF** | 🟡 | Small utilities, helpers, constants | 3-section summary |
| **SKIP** | ⚪ | Test files, `.d.ts` declarations, build config files | Static analysis only |

---

## Architecture

```
traverser/
├── cli.py              ← Typer CLI — 4 commands: generate, analyse, config, copilot
├── config.py           ← Pydantic-settings configuration
├── pipeline.py         ← Main async orchestrator (focus filter, update/diff mode)
├── fetcher/
│   └── github_fetcher.py   ← GitHub API + snapshot cache (SHA-gated)
├── analyzer/
│   ├── base_analyzer.py
│   ├── python_analyzer.py      ← AST-based analysis
│   ├── javascript_analyzer.py  ← Regex-based JS/TS/Vue/React/NestJS
│   ├── php_analyzer.py         ← Regex-based PHP/Laravel
│   ├── generic_analyzer.py
│   └── relationship_mapper.py
├── generator/
│   ├── llm_provider.py         ← OpenAI / Anthropic abstraction
│   ├── prompts.py              ← 9 prompt templates (incl. SUMMARY + COPILOT)
│   └── doc_generator.py        ← Cached LLM generation + doc tier logic
├── models/
│   ├── repo_models.py
│   └── doc_models.py           ← DocTier enum, KnowledgeBase with summary/copilot fields
└── output/
    └── writer.py               ← Markdown writer (SUMMARY.md + .github/copilot-instructions.md)
```

---

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

---

## Design decisions

| Decision | Rationale |
|----------|-----------|
| Python static AST analysis | No external tools needed; covers most patterns |
| Regex analysis for JS/TS/PHP | Avoids Node.js/PHP runtime dependency |
| diskcache keyed by blob SHA | Same commit = zero LLM calls on re-run |
| `--update` diff mode | `.traverser-state.json` stores SHAs; only changed files hit LLM |
| `--focus` subpath filter | Makes large monorepos tractable; applied post-fetch pre-analysis |
| `SUMMARY.md` as top-level | Compact < 1,500 word entry point; ideal for Copilot `@workspace` |
| `.github/copilot-instructions.md` | GitHub Copilot auto-loads this for workspace-level context |
| 3-tier doc system (FULL/BRIEF/SKIP) | ~50–55% token reduction on typical repos |
| Markdown output | Works with NotebookLM, Obsidian, GitHub, Copilot, etc. |
| Async pipeline | Parallel LLM calls with semaphore — 5–10× faster |
| Pluggable LLM provider | OpenAI today, Anthropic/Ollama tomorrow |
