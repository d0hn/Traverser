# Traverser 🔍

> **AI-powered documentation generator** — point it at any GitHub repository and it produces a rich, Language-Model-ready knowledge base that lets AI agents answer detailed debugging questions about the codebase.


## Why Traverser?

Onboarding to an unfamiliar codebase is slow. Reading raw source files gives you 
syntax but not intent — you don't know why decisions were made, how modules 
connect, or where to start when something breaks. Traverser solves this by 
turning any GitHub repository into a structured knowledge base: rich markdown 
docs that explain purpose, architecture, and debugging paths — ready to feed 
into NotebookLM, GitHub Copilot, or any AI tool. What used to take days of 
exploration takes minutes.

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
# edit .env — set OPENAI_API_KEY, ANTHROPIC_API_KEY, or use --provider github-copilot
# Traverser auto-detects the provider from whichever key is present; no LLM_PROVIDER needed

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

# Generate GitHub Copilot workspace instructions only (minimal cost)
traverser generate https://github.com/owner/repo --copilot-only

# Reduce output file count for LM tools (NotebookLM) (hard 300-file limit) — no LLM calls
traverser compact output/owner_repo
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
    ├── src__app__main.md        ← FULL-tier per-file documentation
    ├── BRIEF_BUNDLE_01.md       ← up to 100 BRIEF docs bundled together
    ├── BRIEF_BUNDLE_02.md       ← (created by `compact` or automatically on write)
    └── ...
```

> **NotebookLM has a 300-source limit.** Large repos can easily exceed this with one file per source.
> Run `traverser compact output/owner_repo` to bundle BRIEF-tier docs and stay well under the limit.

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
  --copilot-only        Generate only SUMMARY.md + .github/copilot-instructions.md (minimal cost)
```

> **Provider auto-detection:** If `LLM_PROVIDER` is not set in `.env`, Traverser
> automatically picks the provider whose API key is present. Set only
> `ANTHROPIC_API_KEY` and it will use Anthropic without any extra configuration.

**LLM Providers:**

| Provider | Cost | Setup | Details |
|----------|------|-------|---------|
| `openai` | Pay-per-token | `OPENAI_API_KEY` env var | [See supported models →](./MODELS.md#openai) |
| `anthropic` | Pay-per-token | `ANTHROPIC_API_KEY` env var | [See supported models →](./MODELS.md#anthropic) |
| `github-copilot` | Included with Copilot subscription | `brew install copilot-cli` | [See supported models →](./MODELS.md#github-copilot) |

# Supported Models

> Model availability changes frequently. Links to official docs are provided for each provider.

---

## OpenAI

[Full list →](https://platform.openai.com/docs/models)

| Model | Best for |
|-------|----------|
| `gpt-5.4` | Flagship reasoning, complex codebases, agentic tasks |
| `gpt-5.2` | Strong general coding, slightly more affordable than 5.4 |
| `gpt-5` | Previous flagship, still available |
| `gpt-5-mini` | Fast, cost-efficient, good for smaller repos |
| `gpt-5-nano` | Highest speed, lowest cost, bulk processing |
| `gpt-4.1` | Balanced everyday use, 1M token context |
| `o3` | Deep chain-of-thought reasoning |

---

## Anthropic

[Full list →](https://docs.anthropic.com/en/docs/about-claude/models/overview)

| Model | Best for |
|-------|----------|
| `claude-opus-4-6` | Most powerful, deep architecture analysis |
| `claude-sonnet-4-6` | Best balance of quality and speed (recommended) |
| `claude-opus-4-5` | Strong coding and reasoning |
| `claude-sonnet-4-5` | Fast, reliable everyday analysis |
| `claude-haiku-4-5` | Lightweight, high-volume, fast responses |

---

## GitHub Copilot

[Full list →](https://docs.github.com/en/copilot/reference/ai-models/supported-models)

> No API key required — uses your existing Copilot subscription.
> Run `copilot help config` to see the live model list in your CLI.

| Model | Provider | Best for |
|-------|----------|----------|
| `claude-sonnet-4-5` | Anthropic | Default — good all-rounder |
| `claude-opus-4-6` | Anthropic | Deep analysis, complex repos |
| `claude-haiku-4-5` | Anthropic | Fast, lightweight tasks |
| `gpt-5.4` | OpenAI | Heavy agentic/coding tasks |
| `gpt-4.1` | OpenAI | Balanced, free-tier eligible |
| `gpt-5-mini` | OpenAI | Fast and cost-efficient |
| `o3` | OpenAI | Chain-of-thought reasoning |
| `gemini-2.5-pro` | Google | Long context, multimodal |

### `traverser copilot` — **deprecated**, use `generate --copilot-only`

```
traverser copilot URL [OPTIONS]   ← deprecated alias
```

> ⚠️ **Deprecated.** Use `traverser generate URL --copilot-only` instead — it has
> the same effect and keeps everything under one command. The `copilot` sub-command
> is preserved for backward compatibility but will print a deprecation notice.

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

### `traverser compact` — reduce output file count (no LLM calls)

```
traverser compact OUTPUT_DIR [OPTIONS]
  OUTPUT_DIR            Path to an existing traverser output folder (e.g. output/owner_repo)
  --bundle-size INT     BRIEF docs per bundle file (default: 100)
```

Bundles all BRIEF-tier docs into a small number of combined files (`BRIEF_BUNDLE_01.md`, etc.),
and updates `FILE_INDEX.md` links accordingly. No fetch, no analysis, no LLM calls.

**When to use it:**
- You hit NotebookLM's 300-source limit
- You already generated docs and don't want to re-run the full pipeline
- You want a cleaner folder with fewer files to manage

New runs automatically bundle BRIEF docs during write, so `compact` is mainly useful for
already-generated output folders.

```bash
# Bundle BRIEF docs in an existing output folder
traverser compact output/owner_repo

# Custom bundle size (50 docs per file)
traverser compact output/owner_repo --bundle-size 50
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

# NotebookLM has a hard 300-source limit — large repos easily exceed this.
# Bundle BRIEF docs to stay well under the limit (no LLM calls):
traverser compact output/owner_repo

# Upload output/owner_repo/ folder to NotebookLM as a source collection
# NotebookLM will use that context for all follow-up questions
```

> **Tip:** After compacting, a repo with 500 files typically produces ~100 output files:
> ~80 FULL docs + 5 BRIEF bundle files + ~10 top-level docs.

---

## Doc tier system (cost optimisation)

Traverser uses a 3-tier documentation strategy to reduce LLM costs by ~50–55%:

| Tier | Badge | Criteria | LLM sections |
|------|-------|----------|-------------|
| **FULL** | 🟢 | Hub files, entry points, files > 100 lines, ≥2 classes or ≥6 functions | 10-section deep-dive |
| **BRIEF** | 🟡 | Small utilities, helpers, constants | 3-section summary |
| **SKIP** | ⚪ | Test files, `.d.ts` declarations, build config files | Skipped |

---

## Architecture

```
traverser/
├── cli.py              ← Typer CLI — generate (with --copilot-only), analyse, compact, config, copilot (deprecated)
├── config.py           ← Pydantic-settings configuration + auto provider detection
├── pipeline.py         ← Main async orchestrator (focus filter, update/diff mode)
├── fetcher/
│   └── github_fetcher.py   ← git clone --depth=1 (primary) + GitHub API fallback + snapshot cache
├── analyzer/
│   ├── base_analyzer.py
│   ├── python_analyzer.py      ← AST-based analysis
│   ├── javascript_analyzer.py  ← Regex-based JS/TS/Vue/React/NestJS
│   ├── php_analyzer.py         ← Regex-based PHP/Laravel
│   ├── generic_analyzer.py
│   └── relationship_mapper.py  ← Accurate JS/TS + Python relative import resolution
├── generator/
│   ├── llm_provider.py         ← OpenAI / Anthropic abstraction
│   ├── prompts.py              ← 9 prompt templates (incl. SUMMARY + COPILOT)
│   └── doc_generator.py        ← Cached LLM generation + doc tier logic
├── models/
│   ├── repo_models.py
│   └── doc_models.py           ← DocTier enum, ImportInfo (with level), KnowledgeBase
└── output/
    ├── writer.py               ← Markdown writer (SUMMARY.md + .github/copilot-instructions.md)
    └── compactor.py            ← Post-process existing output: bundle BRIEF docs, update FILE_INDEX
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
| BRIEF bundling (`compact`) | Keeps output under NotebookLM's 300-file cap; runs on existing output with no LLM cost |
| Async pipeline | Parallel LLM calls with semaphore — 5–10× faster |
| Pluggable LLM provider | OpenAI today, Anthropic/Ollama tomorrow |
| Auto provider detection | Reads which API key is set; no `LLM_PROVIDER` needed for most setups |
| `git clone --depth=1` fetch | Downloads entire repo in one network request; 10–100× faster than per-blob API calls |
| Level-aware relative import resolution | Correctly resolves `./foo`, `../models/Bar`, and Python `from ..pkg import X` to actual files; eliminates false "not used" reports |
