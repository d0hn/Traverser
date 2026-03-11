"""Main async pipeline — orchestrates fetch → analyse → generate → write."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from traverser.analyzer import RelationshipMapper, get_analyzer
from traverser.config import Config
from traverser.fetcher.github_fetcher import GithubFetcher
from traverser.generator.doc_generator import DocGenerator
from traverser.models.doc_models import DocTier, FileAnalysis, FileDocumentation, KnowledgeBase
from traverser.models.repo_models import FileNode, RepoInfo
from traverser.output.writer import OutputWriter

logger = logging.getLogger(__name__)
console = Console()

# ── Shared progress style ─────────────────────────────────────────────────────

_PROGRESS_COLUMNS = (
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(bar_width=32),
    MofNCompleteColumn(),
    TimeElapsedColumn(),
)


def _make_progress() -> Progress:
    """Return a consistently-styled Progress instance."""
    return Progress(*_PROGRESS_COLUMNS, console=console, transient=False)


# ── State file helpers (for --update diff mode) ───────────────────────────────

_STATE_FILENAME = ".traverser-state.json"


def _load_previous_state(output_root: Path) -> dict[str, str]:
    """Load previous run's {path: sha} map from the state file, if it exists."""
    state_file = output_root / _STATE_FILENAME
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        files = data.get("files", {})
        return {path: info.get("sha", "") for path, info in files.items() if isinstance(info, dict)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read previous state file: %s", exc)
        return {}


def _load_previous_docs(output_root: Path, kb_stub: KnowledgeBase) -> dict[str, FileDocumentation]:
    """Load FileDocumentation objects that were written in the previous run.

    Returns an empty dict when no previous KB is available. The previous KB is
    reconstructed from the state file's metadata — file docs that haven't
    changed (same SHA) will be reused rather than regenerated.
    """
    # We persist file docs as individual Markdown files, not as a JSON KB.
    # Instead, we return an empty dict; the diff logic in _generate_file_docs
    # will skip unchanged files and fall back to re-reading cached LLM results
    # from diskcache (which are keyed by SHA). The net effect is identical —
    # no extra LLM calls — because diskcache already stores results by SHA.
    return {}


def _save_state(output_root: Path, kb: KnowledgeBase) -> None:
    """Persist a compact {path: {sha, doc_tier}} state file for the next --update run."""
    state_file = output_root / _STATE_FILENAME
    files: dict[str, dict[str, str]] = {}
    for path, doc in kb.file_docs.items():
        files[path] = {"sha": doc.sha, "doc_tier": doc.doc_tier.value}
    data = {
        "generated_at": kb.generated_at,
        "model_used": kb.model_used,
        "focus_path": kb.focus_path,
        "files": files,
    }
    state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.debug("State file written to %s", state_file)


class Pipeline:
    """End-to-end documentation pipeline for a GitHub repository."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._fetcher = GithubFetcher(config)
        self._relationship_mapper = RelationshipMapper()
        self._generator = DocGenerator(config)
        self._writer = OutputWriter(config.output_dir)

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(
        self,
        url: str,
        token: str | None = None,
        dry_run: bool = False,
        focus: str | None = None,
        update_mode: bool = False,
    ) -> KnowledgeBase:
        """Run the full pipeline for a GitHub repository URL.

        Args:
            url:         GitHub repository URL.
            token:       GitHub Personal Access Token (optional for public repos).
            dry_run:     Skip LLM generation and file output.
            focus:       Restrict analysis to files under this path prefix.
            update_mode: Re-process only files whose SHA changed since last run.
        """

        # ── 1. Fetch ──────────────────────────────────────────────────────────
        console.rule("[bold blue]Step 1 / 5 — Fetching repository")
        repo_info = await self._fetch_with_progress(url, token)
        source_note = " [dim](cache hit)[/dim]" if repo_info.from_cache else ""
        console.print(
            f"  [green]✓[/green] Fetched [bold]{len(repo_info.files)}[/bold] files "
            f"from [bold]{repo_info.display_name}[/bold]{source_note}"
        )

        # Apply --focus filter
        if focus:
            before = len(repo_info.files)
            repo_info = repo_info.model_copy(
                update={"files": [f for f in repo_info.files if f.path.startswith(focus)]}
            )
            console.print(
                f"  [cyan]⤷ --focus[/cyan] [bold]{focus}[/bold]"
                f" — kept [bold]{len(repo_info.files)}[/bold] / {before} files"
            )
            if not repo_info.files:
                console.print(f"  [yellow]⚠[/yellow] No files match focus prefix '{focus}'.")

        # ── 2. Static analysis ────────────────────────────────────────────────
        console.rule("[bold blue]Step 2 / 5 — Static analysis")
        analyses = self._analyse_all(repo_info)
        analysable = repo_info.analysable_files
        console.print(
            f"  [green]✓[/green] Analysed [bold]{len(analyses)}[/bold] files "
            f"([bold]{len(analysable)}[/bold] analysable)"
        )

        # ── 3. Relationship mapping ───────────────────────────────────────────
        console.rule("[bold blue]Step 3 / 5 — Building relationship graph")
        with console.status("  [cyan]Resolving imports and building dependency graph..."):
            relationships = self._relationship_mapper.build(analyses)
            relationship_graph_mermaid = self._relationship_mapper.to_mermaid(
                relationships, analyses
            )
        console.print(
            f"  [green]✓[/green] Found [bold]{len(relationships.hub_files)}[/bold] hub files, "
            f"[bold]{len(relationships.entry_points)}[/bold] entry points, "
            f"[bold]{len(relationships.circular_deps)}[/bold] circular deps"
        )

        if dry_run:
            console.print("\n[yellow]Dry run — skipping LLM generation and file output.[/yellow]")
            return KnowledgeBase(
                owner=repo_info.owner,
                repo_name=repo_info.repo_name,
                generated_at=_now(),
                model_used="dry-run",
                file_analyses=analyses,
                relationships=relationships,
                relationship_graph_mermaid=relationship_graph_mermaid,
                focus_path=focus,
            )

        # ── Load previous state for --update mode ─────────────────────────────
        previous_shas: dict[str, str] = {}
        if update_mode:
            output_root_preview = self.config.output_dir / f"{repo_info.owner}_{repo_info.repo_name}"
            previous_shas = _load_previous_state(output_root_preview)
            if previous_shas:
                unchanged = sum(
                    1 for f in repo_info.analysable_files
                    if previous_shas.get(f.path) == f.sha
                )
                console.print(
                    f"  [cyan]⤷ --update[/cyan] found [bold]{len(previous_shas)}[/bold] previous docs "
                    f"— [bold]{unchanged}[/bold] files unchanged (will skip LLM)"
                )
            else:
                console.print(
                    "  [yellow]⚠[/yellow] No previous state file found — running full generation"
                )

        # ── 4. LLM documentation ──────────────────────────────────────────────
        console.rule("[bold blue]Step 4 / 5 — Generating LLM documentation")
        file_docs = await self._generate_file_docs(
            repo_info, analyses, relationships, previous_shas=previous_shas
        )

        # Tier breakdown — show what was skipped / briefed / fully documented
        tier_counts = {t: sum(1 for d in file_docs.values() if d.doc_tier == t) for t in DocTier}
        console.print(
            f"  🟢 Full: [bold]{tier_counts[DocTier.FULL]}[/bold]  "
            f"🟡 Brief: [bold]{tier_counts[DocTier.BRIEF]}[/bold]  "
            f"⚪ Skip (no LLM): [bold]{tier_counts[DocTier.SKIP]}[/bold]"
        )

        # Separate test-tier files so we can generate one aggregate tests doc
        test_analyses = {path: a for path, a in analyses.items() if a.has_tests}

        # ── Batch 1: independent project-level docs (run in parallel) ─────
        with console.status("  [cyan]⚡ Generating project-level docs in parallel..."):
            batch1_tasks = [
                self._generator.generate_architecture(
                    repo_info, analyses, relationships, file_docs
                ),
                self._generator.generate_mind_map(repo_info),
                self._generator.generate_glossary(repo_info, analyses),
            ]
            if test_analyses:
                batch1_tasks.append(
                    self._generator.generate_tests_overview(repo_info, test_analyses)
                )

            batch1_results = await asyncio.gather(*batch1_tasks)

        architecture = batch1_results[0]
        mind_map = batch1_results[1]
        glossary = batch1_results[2]
        tests_overview = batch1_results[3] if test_analyses else ""

        console.print(
            f"  [green]✓[/green] Architecture, mind map, glossary"
            f"{', tests overview' if test_analyses else ''} generated"
        )

        # ── Batch 2: docs that depend on architecture (run in parallel) ───
        with console.status("  [cyan]⚡ Generating remaining docs in parallel..."):
            batch2_results = await asyncio.gather(
                self._generator.generate_debugging_guide(
                    repo_info, relationships, architecture, file_docs
                ),
                self._generator.generate_summary(
                    repo_info, relationships, architecture, file_docs, focus_path=focus
                ),
                self._generator.generate_copilot_instructions(
                    repo_info, relationships, architecture, file_docs, focus_path=focus
                ),
            )

        debugging_guide = batch2_results[0]
        summary = batch2_results[1]
        copilot_instructions = batch2_results[2]

        console.print("  [green]✓[/green] All LLM artefacts generated")

        # ── 5. Write output ───────────────────────────────────────────────────
        console.rule("[bold blue]Step 5 / 5 — Writing output files")
        model_name = self._generator._provider.model_name
        kb = KnowledgeBase(
            owner=repo_info.owner,
            repo_name=repo_info.repo_name,
            generated_at=_now(),
            model_used=model_name,
            file_analyses=analyses,
            file_docs=file_docs,
            relationships=relationships,
            architecture_overview=architecture,
            mind_map_mermaid=mind_map,
            relationship_graph_mermaid=relationship_graph_mermaid,
            debugging_guide=debugging_guide,
            glossary=glossary,
            tests_overview=tests_overview,
            summary=summary,
            copilot_instructions=copilot_instructions,
            focus_path=focus,
        )

        with console.status("  [cyan]Writing Markdown files to disk..."):
            output_root = await asyncio.get_running_loop().run_in_executor(
                None, self._writer.write, kb
            )

        # Persist state for future --update runs
        _save_state(output_root, kb)

        stats = kb.stats
        console.print(
            f"  [green]✓[/green] Written to [bold]{output_root}[/bold] "
            f"({stats['documented_files']} files, "
            f"{stats['total_classes']} classes, "
            f"{stats['total_functions']} functions)"
        )

        return kb

    # ── Analysis only (no LLM) ────────────────────────────────────────────────

    async def analyse_only(
        self,
        url: str,
        token: str | None = None,
        focus: str | None = None,
    ) -> dict[str, FileAnalysis]:
        """Fetch and statically analyse a repository without calling an LLM."""
        repo_info = await self._fetch_with_progress(url, token)

        if focus:
            before = len(repo_info.files)
            repo_info = repo_info.model_copy(
                update={"files": [f for f in repo_info.files if f.path.startswith(focus)]}
            )
            console.print(
                f"  [cyan]⤷ --focus[/cyan] [bold]{focus}[/bold]"
                f" — kept [bold]{len(repo_info.files)}[/bold] / {before} files"
            )

        return self._analyse_all(repo_info)

    # ── Lightweight Copilot-only mode (minimal LLM cost) ──────────────────────

    async def generate_copilot_only(
        self,
        url: str,
        token: str | None = None,
        focus: str | None = None,
    ) -> KnowledgeBase:
        """Generate ONLY copilot instructions + summary (no expensive file docs).

        This is a lightweight alternative to full `run()` — skips per-file
        documentation generation to save ~90% on LLM costs. Ideal for:
        - GitHub Copilot workspace instructions
        - Quick architecture overview
        - Minimal NotebookLM context

        Args:
            url:   GitHub repository URL.
            token: GitHub Personal Access Token.
            focus: Restrict analysis to a subpath prefix.
        """

        # ── 1. Fetch ──────────────────────────────────────────────────────────
        console.rule("[bold blue]Step 1 / 3 — Fetching repository")
        repo_info = await self._fetch_with_progress(url, token)
        source_note = " [dim](cache hit)[/dim]" if repo_info.from_cache else ""
        console.print(
            f"  [green]✓[/green] Fetched [bold]{len(repo_info.files)}[/bold] files "
            f"from [bold]{repo_info.display_name}[/bold]{source_note}"
        )

        # Apply --focus filter
        if focus:
            before = len(repo_info.files)
            repo_info = repo_info.model_copy(
                update={"files": [f for f in repo_info.files if f.path.startswith(focus)]}
            )
            console.print(
                f"  [cyan]⤷ --focus[/cyan] [bold]{focus}[/bold]"
                f" — kept [bold]{len(repo_info.files)}[/bold] / {before} files"
            )

        # ── 2. Static analysis ────────────────────────────────────────────────
        console.rule("[bold blue]Step 2 / 3 — Static analysis")
        analyses = self._analyse_all(repo_info)
        console.print(
            f"  [green]✓[/green] Analysed [bold]{len(analyses)}[/bold] files"
        )

        # ── 3. Relationship mapping ───────────────────────────────────────────
        console.rule("[bold blue]Step 3 / 3 — Generating Copilot context")
        with console.status("  [cyan]Building relationship graph..."):
            relationships = self._relationship_mapper.build(analyses)
            relationship_graph_mermaid = self._relationship_mapper.to_mermaid(
                relationships, analyses
            )

        # Generate ONLY the minimal set for Copilot (no per-file docs)
        architecture = await self._with_spinner(
            "Generating architecture overview...",
            self._generator.generate_architecture(
                repo_info, analyses, relationships, {}  # empty file_docs
            ),
        )

        summary = await self._with_spinner(
            "Generating SUMMARY.md...",
            self._generator.generate_summary(
                repo_info, relationships, architecture, {}, focus_path=focus  # empty file_docs
            ),
        )

        copilot_instructions = await self._with_spinner(
            "Generating Copilot instructions...",
            self._generator.generate_copilot_instructions(
                repo_info, relationships, architecture, {}, focus_path=focus  # empty file_docs
            ),
        )

        console.print("  [green]✓[/green] Copilot context generated")

        # ── 4. Write minimal output ───────────────────────────────────────────
        console.rule("[bold blue]Writing output files")
        model_name = self._generator._provider.model_name
        kb = KnowledgeBase(
            owner=repo_info.owner,
            repo_name=repo_info.repo_name,
            generated_at=_now(),
            model_used=model_name,
            file_analyses=analyses,
            file_docs={},  # intentionally empty — no file-level docs
            relationships=relationships,
            architecture_overview=architecture,
            mind_map_mermaid="",
            relationship_graph_mermaid=relationship_graph_mermaid,
            debugging_guide="",
            glossary={},
            tests_overview="",
            summary=summary,
            copilot_instructions=copilot_instructions,
            focus_path=focus,
        )

        with console.status("  [cyan]Writing Markdown files to disk..."):
            output_root = await asyncio.get_running_loop().run_in_executor(
                None, self._writer.write, kb
            )

        console.print(
            f"  [green]✓[/green] Written to [bold]{output_root}[/bold]"
        )
        console.print(
            f"\n[dim]Copilot instructions:[/dim] "
            f"[bold]{output_root}/.github/copilot-instructions.md[/bold]\n"
            f"[dim]Summary:[/dim] [bold]{output_root}/SUMMARY.md[/bold]\n"
            f"\n[dim]Copy[/dim] [bold].github/copilot-instructions.md[/bold] [dim]into your repo root "
            f"for automatic GitHub Copilot workspace context.[/dim]"
        )

        return kb

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_with_progress(self, url: str, token: str | None) -> RepoInfo:
        """Fetch a repository while displaying a live progress bar.

        Uses Rich's thread-safe Progress so callbacks from the background
        executor thread can safely advance the bar.
        """
        with _make_progress() as progress:
            task = progress.add_task(
                "  [cyan]Connecting to GitHub and scanning file tree...",
                total=None,  # indeterminate until tree is known
            )

            def on_tree(total: int) -> None:
                # Called from the background thread once the tree is scanned.
                # Rich Progress is thread-safe so this is safe to call here.
                progress.update(
                    task,
                    description="  [cyan]Fetching files...",
                    total=total,
                    completed=0,
                )

            def on_file() -> None:
                # Called from the background thread after each file fetch.
                progress.advance(task)

            repo_info = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._fetcher.fetch(url, token, on_tree=on_tree, on_file=on_file),
            )

            # Final update — mark the task done with a success label
            progress.update(
                task,
                description=(
                    f"  [green]Fetched {len(repo_info.files)} / "
                    f"{progress.tasks[0].total or '?'} files[/green]"
                ),
                completed=progress.tasks[0].total or len(repo_info.files),
            )

        return repo_info

    def _analyse_all(self, repo_info: RepoInfo) -> dict[str, FileAnalysis]:
        """Run static analysis on all analysable files."""
        analyses: dict[str, FileAnalysis] = {}
        with _make_progress() as progress:
            files = repo_info.analysable_files
            task = progress.add_task("  Analysing files...", total=len(files))
            for file in files:
                analyzer = get_analyzer(file)
                analyses[file.path] = analyzer.analyze(file)
                progress.advance(task)

        # Report errors
        errors = [p for p, a in analyses.items() if a.error]
        if errors:
            console.print(
                f"  [yellow]⚠[/yellow] {len(errors)} files failed analysis "
                f"(e.g. {errors[0]})"
            )

        return analyses

    async def _generate_file_docs(
        self,
        repo_info: RepoInfo,
        analyses: dict[str, FileAnalysis],
        relationships,  # ProjectRelationships
        previous_shas: dict[str, str] | None = None,
    ) -> dict[str, FileDocumentation]:
        """Generate LLM documentation for all analysable files concurrently.

        When *previous_shas* is provided (--update mode), files whose SHA
        matches the previous run are skipped — the LLM disk-cache will return
        the same result anyway, so this just avoids the network round-trip.

        Tier routing:
          SKIP  → excluded entirely (no doc, no output file)
          BRIEF → batched into combined LLM calls (up to 8 per call)
          FULL  → individual LLM calls
        """
        from traverser.generator.doc_generator import _classify_doc_tier

        docs: dict[str, FileDocumentation] = {}
        analysable = {f.path: f for f in repo_info.analysable_files}
        previous_shas = previous_shas or {}

        # Separate unchanged files from changed/new files
        unchanged_paths = {
            path for path, file in analysable.items()
            if previous_shas.get(path) == file.sha
        }
        files_to_process = [f for f in analysable.values() if f.path not in unchanged_paths]

        if unchanged_paths:
            console.print(
                f"  [dim]⤷ Skipping [bold]{len(unchanged_paths)}[/bold] unchanged files "
                f"({len(files_to_process)} to regenerate)[/dim]"
            )

        # ── Classify files into tiers ─────────────────────────────────────────
        hub_set = set(relationships.hub_files)
        entry_set = set(relationships.entry_points)
        full_files: list[FileNode] = []
        brief_files: list[FileNode] = []
        skip_count = 0

        for file in files_to_process:
            analysis = analyses.get(file.path)
            if analysis is None:
                skip_count += 1
                continue
            tier = _classify_doc_tier(file, analysis, hub_set, entry_set)
            if tier == DocTier.FULL:
                full_files.append(file)
            elif tier == DocTier.BRIEF:
                brief_files.append(file)
            else:
                skip_count += 1

        brief_batches = (len(brief_files) + 7) // 8 if brief_files else 0
        total_docs = len(full_files) + len(brief_files)
        console.print(
            f"  [dim]⤷ {len(full_files)} full · {len(brief_files)} brief "
            f"({brief_batches} batch{'es' if brief_batches != 1 else ''}) · "
            f"{skip_count} skipped[/dim]"
        )

        # ── Generate docs ─────────────────────────────────────────────────────
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=32),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                "  Generating file documentation...", total=total_docs
            )

            # FULL files: individual concurrent LLM calls
            async def _doc_full(file: FileNode) -> tuple[str, FileDocumentation]:
                doc = await self._generator.generate_file_doc(
                    file, analyses[file.path], relationships
                )
                progress.advance(task)
                return file.path, doc

            # BRIEF files: batched LLM calls (callback advances the bar)
            def _on_batch_done(n: int) -> None:
                progress.advance(task, advance=n)

            # Run FULL and BRIEF concurrently
            full_coro = asyncio.gather(
                *[_doc_full(f) for f in full_files],
                return_exceptions=True,
            )
            brief_coro = self._generator.generate_brief_batch(
                brief_files, analyses, relationships, on_batch_done=_on_batch_done,
            )

            full_results, brief_docs = await asyncio.gather(full_coro, brief_coro)

        # Merge FULL results
        for result in full_results:
            if isinstance(result, Exception):
                logger.error("File doc generation failed: %s", result)
            else:
                path, doc = result
                docs[path] = doc

        # Merge BRIEF results
        docs.update(brief_docs)

        # Reload unchanged BRIEF/FULL files from cache (SKIP files excluded)
        for path in unchanged_paths:
            file = analysable[path]
            analysis = analyses.get(path)
            if analysis is None:
                continue
            tier = _classify_doc_tier(file, analysis, hub_set, entry_set)
            if tier == DocTier.SKIP:
                continue  # Exclude static-only files from output
            try:
                doc = await self._generator.generate_file_doc(file, analysis, relationships)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not reload doc for unchanged %s: %s", path, exc)
                continue
            docs[path] = doc

        return docs

    @staticmethod
    async def _with_spinner(message: str, coro: "asyncio.Coroutine") -> str:  # type: ignore[type-arg]
        """Run a coroutine and show a spinner with elapsed time."""
        with Progress(
            SpinnerColumn(),
            TextColumn(f"  [cyan]{message}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("", total=None)
            result = await coro
        console.print(f"  [green]✓[/green] {message}")
        return result


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
