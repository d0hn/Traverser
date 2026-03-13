"""Traverser CLI — entry point for all commands."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from traverser import __version__

app = typer.Typer(
    name="traverser",
    help="AI-powered documentation generator — builds NotebookLM-ready knowledge bases from GitHub repositories.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"traverser version [bold]{__version__}[/bold]")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Traverser — AI-powered GitHub repository documentation generator."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── generate command ──────────────────────────────────────────────────────────


@app.command()
def generate(
    url: Annotated[str, typer.Argument(help="GitHub repository URL (public or private)")],
    token: Annotated[
        Optional[str],
        typer.Option("--token", "-t", help="GitHub Personal Access Token (overrides GITHUB_TOKEN env var)"),
    ] = None,
    output_dir: Annotated[
        Optional[Path],
        typer.Option("--output-dir", "-o", help="Output directory (default: ./output)"),
    ] = None,
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", help="LLM provider: openai | anthropic | github-copilot"),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option("--model", "-m", help="LLM model name (e.g. gpt-4o, claude-3-5-sonnet-20241022)"),
    ] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Disable disk cache")] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch and analyse only — no LLM calls, no output files"),
    ] = False,
    focus: Annotated[
        Optional[str],
        typer.Option("--focus", "-F", help="Restrict analysis to a subpath prefix, e.g. src/services"),
    ] = None,
    update: Annotated[
        bool,
        typer.Option("--update", "-u", help="Re-process only files whose SHA changed since the last run"),
    ] = False,
) -> None:
    """
    Generate a complete NotebookLM-ready knowledge base for a GitHub repository.

    Example:
        traverser generate https://github.com/owner/repo --token ghp_xxx
        traverser generate https://github.com/owner/repo --focus src/services
        traverser generate https://github.com/owner/repo --update
    """
    from traverser.config import Config, LLMProvider
    from traverser.pipeline import Pipeline

    # Build config, applying any CLI overrides
    config = Config()
    if output_dir:
        config = config.model_copy(update={"output_dir": output_dir})
    if provider:
        config = config.model_copy(update={"llm_provider": LLMProvider(provider)})
    if model:
        config = config.model_copy(update={"llm_model": model})
    if no_cache:
        config = config.model_copy(update={"cache_enabled": False})

    # Validate API key eagerly
    if not dry_run:
        try:
            config.require_api_key()
        except ValueError as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc

    pipeline = Pipeline(config)

    try:
        kb = asyncio.run(pipeline.run(url, token=token, dry_run=dry_run, focus=focus, update_mode=update))
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted by user.[/yellow]")
        raise typer.Exit(130) from None

    stats = kb.stats
    console.print("\n[bold green]✓ Done![/bold green]")

    table = Table(title="Knowledge Base Stats", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")
    for key, value in stats.items():
        table.add_row(key.replace("_", " ").title(), str(value))
    console.print(table)

    if not dry_run:
        output_root = config.output_dir / f"{kb.owner}_{kb.repo_name}"
        console.print(
            f"\n[dim]Output:[/dim] [bold]{output_root.resolve()}[/bold]\n"
            f"[dim]Upload all files in that folder to NotebookLM.[/dim]\n"
            f"[dim]Copilot instructions written to:[/dim] "
            f"[bold]{output_root.resolve()}/.github/copilot-instructions.md[/bold]"
        )


# ── analyse command ───────────────────────────────────────────────────────────


@app.command()
def analyse(
    url: Annotated[str, typer.Argument(help="GitHub repository URL")],
    token: Annotated[Optional[str], typer.Option("--token", "-t")] = None,
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir", "-o")] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: table | json"),
    ] = "table",
    focus: Annotated[
        Optional[str],
        typer.Option("--focus", "-F", help="Restrict analysis to a subpath prefix, e.g. src/services"),
    ] = None,
) -> None:
    """
    Statically analyse a repository — no LLM calls required.

    Prints a summary of all files, their imports, classes, and functions.
    Useful for quickly understanding a codebase structure.
    """
    from traverser.config import Config
    from traverser.pipeline import Pipeline

    config = Config()
    pipeline = Pipeline(config)

    try:
        analyses = asyncio.run(pipeline.analyse_only(url, token=token, focus=focus))
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if fmt == "json":
        data = {path: a.model_dump() for path, a in analyses.items()}
        console.print_json(json.dumps(data))
        return

    table = Table(title=f"Static Analysis — {url}", show_header=True, show_lines=False)
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Lang", style="dim")
    table.add_column("Lines", justify="right")
    table.add_column("Classes", justify="right")
    table.add_column("Functions", justify="right")
    table.add_column("Imports", justify="right")

    for path, a in sorted(analyses.items()):
        table.add_row(
            path,
            a.language,
            str(a.line_count),
            str(len(a.classes)),
            str(len(a.functions)),
            str(len(a.imports)),
        )

    console.print(table)
    console.print(
        f"\n[bold]{len(analyses)}[/bold] files analysed. "
        f"[dim]Run [cyan]traverser generate {url}[/cyan] to generate full documentation.[/dim]"
    )


# ── context command ───────────────────────────────────────────────────────────


@app.command()
def context(
    url: Annotated[str, typer.Argument(help="GitHub repository URL")],
    token: Annotated[Optional[str], typer.Option("--token", "-t")] = None,
    focus: Annotated[
        Optional[str],
        typer.Option("--focus", "-F", help="Restrict to subpath prefix"),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Write to file instead of stdout"),
    ] = None,
) -> None:
    """
    Generate a compact context file for GitHub Copilot CLI.

    Outputs a unified, concise repository context that can be piped to
    `gh copilot suggest` or `gh copilot explain` commands. Use this to query
    your codebase with GitHub Copilot CLI without burning LLM costs.

    Examples:
        # Output to stdout
        traverser context https://github.com/owner/repo > context.md

        # Use with GitHub Copilot CLI
        gh copilot suggest "how do I implement auth" \\
            < <(traverser context https://github.com/owner/repo)

        # Restrict to subdirectory
        traverser context https://github.com/owner/repo --focus src/api
    """
    from traverser.config import Config
    from traverser.pipeline import Pipeline

    config = Config()
    pipeline = Pipeline(config)

    try:
        kb = asyncio.run(pipeline.generate_copilot_only(url, token=token, focus=focus))
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted by user.[/yellow]")
        raise typer.Exit(130) from None

    # Build a concise context document
    focus_note = f"\n**Focus:** `{kb.focus_path}`\n" if kb.focus_path else ""
    context_text = f"""\
# {kb.owner}/{kb.repo_name} — Codebase Context

{focus_note}

## Summary

{kb.summary}

---

## Architecture

{kb.architecture_overview}

---

## Copilot Instructions

{kb.copilot_instructions}
"""

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(context_text, encoding="utf-8")
        console.print(f"[green]✓[/green] Context written to [bold]{output.resolve()}[/bold]")
        console.print(
            f"\n[dim]Use with GitHub Copilot CLI:[/dim]\n"
            f"  [cyan]gh copilot suggest \"your question\" < {output.resolve()}[/cyan]"
        )
    else:
        # Output to stdout for piping
        console.print(context_text)


# ── copilot command ───────────────────────────────────────────────────────────


@app.command()
def copilot(
    url: Annotated[str, typer.Argument(help="GitHub repository URL (public or private)")],
    token: Annotated[
        Optional[str],
        typer.Option("--token", "-t", help="GitHub Personal Access Token"),
    ] = None,
    output_dir: Annotated[
        Optional[Path],
        typer.Option("--output-dir", "-o", help="Output directory (default: ./output)"),
    ] = None,
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", help="LLM provider: openai | anthropic | github-copilot"),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option("--model", "-m", help="LLM model name"),
    ] = None,
    focus: Annotated[
        Optional[str],
        typer.Option("--focus", "-F", help="Restrict analysis to a subpath prefix"),
    ] = None,
) -> None:
    """
    Generate ONLY .github/copilot-instructions.md and SUMMARY.md (minimal cost).

    This is a lightweight alternative to `generate` — skips expensive per-file
    documentation. Ideal for GitHub Copilot workspace context without burning money
    on per-file LLM documentation.

    The generated `.github/copilot-instructions.md` can be copied to your repo root.
    GitHub Copilot will automatically load it in all conversations in that workspace.

    Example:
        traverser copilot https://github.com/owner/repo --token ghp_xxx
        # Copy to repo:
        cp output/owner_repo/.github/copilot-instructions.md .github/copilot-instructions.md
    """
    from traverser.config import Config, LLMProvider
    from traverser.pipeline import Pipeline

    config = Config()
    if output_dir:
        config = config.model_copy(update={"output_dir": output_dir})
    if provider:
        config = config.model_copy(update={"llm_provider": LLMProvider(provider)})
    if model:
        config = config.model_copy(update={"llm_model": model})

    try:
        config.require_api_key()
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    pipeline = Pipeline(config)

    try:
        kb = asyncio.run(pipeline.generate_copilot_only(url, token=token, focus=focus))
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted by user.[/yellow]")
        raise typer.Exit(130) from None

    output_root = config.output_dir / f"{kb.owner}_{kb.repo_name}"
    copilot_path = output_root / ".github" / "copilot-instructions.md"

    console.print("\n[bold green]✓ Done![/bold green]")
    console.print(
        f"\n[dim]Copy this command to add Copilot instructions to your repo:[/dim]\n"
        f"  [cyan]cp {copilot_path.resolve()} .github/copilot-instructions.md[/cyan]\n"
        f"\n[dim]Then commit and push — GitHub Copilot will pick it up automatically.[/dim]"
    )


# ── config command ────────────────────────────────────────────────────────────


@app.command()
def compact(
    output_root: Annotated[
        Path,
        typer.Argument(
            help="Existing traverser output folder, e.g. output/owner_repo"
        ),
    ],
    bundle_size: Annotated[
        int,
        typer.Option("--bundle-size", help="How many BRIEF docs per bundle"),
    ] = 100,
) -> None:
    """Bundle BRIEF docs in an already-generated output folder.

    No fetch, no analysis, no LLM calls.
    Useful when you already generated docs and need fewer files for NotebookLM.
    """
    from traverser.output.compactor import compact_output

    try:
        stats = compact_output(output_root, bundle_size=bundle_size)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[bold green]✓ Compact complete[/bold green]")
    console.print(
        f"  Brief docs: [bold]{stats['brief_docs']}[/bold]\n"
        f"  Bundles: [bold]{stats['bundles']}[/bold]\n"
        f"  Deleted per-file BRIEF docs: [bold]{stats['deleted_files']}[/bold]"
    )


# ── config command ────────────────────────────────────────────────────────────


@app.command(name="config")
def show_config() -> None:
    """Show the resolved configuration (reads .env if present)."""
    from traverser.config import Config, LLMProvider

    cfg = Config()

    table = Table(title="Resolved Configuration", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value")

    def _mask(value: str | None) -> str:
        if not value:
            return "[dim]not set[/dim]"
        return f"{value[:8]}..." if len(value) > 8 else "***"

    table.add_row("llm_provider", cfg.llm_provider.value)
    table.add_row("llm_model", cfg.llm_model)
    table.add_row("llm_max_tokens", str(cfg.llm_max_tokens))

    if cfg.llm_provider == LLMProvider.GITHUB_COPILOT:
        import shutil
        copilot_bin = shutil.which("copilot")
        table.add_row("copilot_cli", copilot_bin or "[red]not found[/red]")
        table.add_row("auth_method", "GitHub account (copilot login / GITHUB_TOKEN)")
    else:
        table.add_row("openai_api_key", _mask(cfg.openai_api_key))
        table.add_row("anthropic_api_key", _mask(cfg.anthropic_api_key))

    table.add_row("github_token", _mask(cfg.github_token))
    table.add_row("output_dir", str(cfg.output_dir))
    table.add_row("cache_dir", str(cfg.cache_dir))
    table.add_row("cache_enabled", str(cfg.cache_enabled))
    table.add_row("max_file_size_kb", str(cfg.max_file_size_kb))
    table.add_row("max_concurrent_llm_requests", str(cfg.max_concurrent_llm_requests))

    console.print(table)


if __name__ == "__main__":
    app()
