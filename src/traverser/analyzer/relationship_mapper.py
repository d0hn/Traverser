"""Builds a cross-file relationship graph and generates Mermaid output."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import networkx as nx

from traverser.models.doc_models import FileAnalysis, ImportInfo, ProjectRelationships

logger = logging.getLogger(__name__)

# JS/TS/Vue extensions tried when resolving a bare relative path (no extension).
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".vue", ".mjs", ".cjs")


def _normalize_posix(path: str) -> str:
    """Normalize a POSIX path string, collapsing ``..`` and ``.`` components.

    Works on relative paths (no leading ``/``).
    """
    parts: list[str] = []
    for segment in path.split("/"):
        if segment == "..":
            if parts:
                parts.pop()
        elif segment and segment != ".":
            parts.append(segment)
    return "/".join(parts)


def _resolve_relative_import(importer_path: str, imp: ImportInfo) -> list[str]:
    """Resolve a relative import to a list of candidate repo-relative paths.

    Handles two distinct styles:

    **Python style** (``from .sibling import X``, ``from ..parent import Y``):
        The AST parser strips leading dots and stores the level separately.
        ``imp.module`` is the plain module name (e.g. ``"sibling"``), and
        ``imp.level`` is the number of dots (1 = same package, 2 = parent, …).

    **JavaScript / TypeScript style** (``import x from './path'``):
        The full path is stored verbatim in ``imp.module`` (e.g.
        ``"../models/User"``).  We resolve it relative to the importer's
        directory and try multiple extensions.
    """
    module = imp.module
    importer_dir = str(PurePosixPath(importer_path).parent)

    # ── JavaScript / TypeScript / CSS path-style relative imports ────────────
    if module.startswith("./") or module.startswith("../"):
        # Strip query strings / hash fragments (e.g. "./foo?raw", "#hash")
        clean = module.split("?")[0].split("#")[0]
        raw_resolved = _normalize_posix(f"{importer_dir}/{clean}")

        candidates: list[str] = []
        # If the import already has a recognised extension, try it first.
        suffix = PurePosixPath(raw_resolved).suffix.lower()
        if suffix in frozenset(_JS_EXTENSIONS) | {".py", ".css", ".scss", ".sass", ".less"}:
            candidates.append(raw_resolved)
        else:
            # Bare path: try each JS/TS extension and index files.
            for ext in _JS_EXTENSIONS:
                candidates.append(f"{raw_resolved}{ext}")
            for ext in _JS_EXTENSIONS:
                candidates.append(f"{raw_resolved}/index{ext}")

        return candidates

    # ── Python relative import (level-based) ────────────────────────────────
    # Navigate *up* (level - 1) directories from the importer's package dir.
    # level=1 → same package directory; level=2 → parent package, etc.
    level = max(imp.level, 1)  # is_relative=True guarantees level >= 1
    base_parts = importer_dir.split("/") if importer_dir else []
    # Remove (level - 1) trailing parts to walk up the package hierarchy.
    steps_up = level - 1
    if steps_up > 0:
        base_parts = base_parts[: max(0, len(base_parts) - steps_up)]
    base_dir = "/".join(base_parts)

    if not module:
        # e.g. ``from . import sibling`` — no module name
        return []

    parts = module.replace(".", "/")
    prefix = f"{base_dir}/" if base_dir else ""
    return [f"{prefix}{parts}.py"]


def _module_to_paths(module: str, all_paths: set[str]) -> list[str]:
    """Map an absolute module name to candidate repo file paths.

    For a module like ``traverser.models.user``, try:
      - ``traverser/models/user.py``
      - ``src/traverser/models/user.py``
      - ``traverser/models/user/__init__.py``
      - ``src/traverser/models/user/__init__.py``

    Also handles JS/TS bare module specifiers that look like relative paths
    without a leading ``./`` (unusual but sometimes used with path aliases).
    """
    rel = module.replace(".", "/")
    candidates = [
        f"{rel}.py",
        f"src/{rel}.py",
        f"{rel}/__init__.py",
        f"src/{rel}/__init__.py",
    ]
    # Try JS/TS extensions for absolute bare specifiers (e.g. path aliases).
    for ext in _JS_EXTENSIONS:
        candidates.append(f"{rel}{ext}")
        candidates.append(f"src/{rel}{ext}")
    return [c for c in candidates if c in all_paths]


class RelationshipMapper:
    """Builds a directed dependency graph from per-file analysis results."""

    def build(self, analyses: dict[str, FileAnalysis]) -> ProjectRelationships:
        """Build the relationship graph from all file analyses.

        Args:
            analyses: mapping of file_path → FileAnalysis

        Returns:
            ProjectRelationships with resolved import edges.
        """
        all_paths: set[str] = set(analyses.keys())
        graph: nx.DiGraph = nx.DiGraph()

        # Add all files as nodes
        for path in all_paths:
            graph.add_node(path)

        # Add edges
        imports_from: dict[str, list[str]] = {p: [] for p in all_paths}
        imported_by: dict[str, list[str]] = {p: [] for p in all_paths}

        for path, analysis in analyses.items():
            for imp in analysis.imports:
                resolved: list[str] = []

                if imp.is_relative:
                    candidates = _resolve_relative_import(path, imp)
                    resolved = [c for c in candidates if c in all_paths]
                else:
                    resolved = _module_to_paths(imp.module, all_paths)

                for target in resolved:
                    if target != path:  # avoid self-loops
                        graph.add_edge(path, target)
                        if target not in imports_from[path]:
                            imports_from[path].append(target)
                        if path not in imported_by[target]:
                            imported_by[target].append(path)

        # Hub files: top 10 by in-degree
        in_degrees = sorted(graph.in_degree(), key=lambda x: x[1], reverse=True)
        hub_files = [node for node, deg in in_degrees[:10] if deg > 1]

        # Entry points: nodes with in-degree 0 that are analysable
        entry_points = [
            node
            for node, deg in graph.in_degree()
            if deg == 0 and analyses[node].language not in ("markdown", "json", "yaml", "toml")
        ]

        # Circular dependencies (simple cycles)
        circular_deps: list[list[str]] = []
        try:
            for cycle in nx.simple_cycles(graph):
                if len(cycle) <= 5:  # Only report short cycles
                    circular_deps.append(cycle)
        except Exception:  # noqa: BLE001
            pass

        return ProjectRelationships(
            imports_from=imports_from,
            imported_by=imported_by,
            hub_files=hub_files,
            entry_points=entry_points[:20],
            circular_deps=circular_deps[:10],
        )

    def to_mermaid(
        self,
        relationships: ProjectRelationships,
        analyses: dict[str, FileAnalysis],
        max_nodes: int = 60,
    ) -> str:
        """Generate a Mermaid graph diagram from the relationship data.

        Only the most important nodes are shown to keep the diagram readable.
        """
        # Prioritise hub files + entry points + their immediate neighbours
        priority: list[str] = list(
            dict.fromkeys(relationships.hub_files + relationships.entry_points)
        )

        # Add neighbours until we hit max_nodes
        shown: set[str] = set(priority[:max_nodes])
        for node in priority:
            if len(shown) >= max_nodes:
                break
            for neighbour in relationships.imports_from.get(node, []):
                if len(shown) < max_nodes:
                    shown.add(neighbour)

        def _short(path: str) -> str:
            """Create a short, Mermaid-safe node label."""
            p = PurePosixPath(path)
            # Use last two path components
            parts = p.parts
            label = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
            # Mermaid node ids cannot have slashes or dots
            node_id = path.replace("/", "_").replace(".", "_").replace("-", "_")
            return f'{node_id}["{label}"]'

        lines = ["graph TD"]
        seen_edges: set[tuple[str, str]] = set()

        for src in shown:
            for dst in relationships.imports_from.get(src, []):
                if dst in shown and (src, dst) not in seen_edges:
                    seen_edges.add((src, dst))
                    src_id = src.replace("/", "_").replace(".", "_").replace("-", "_")
                    dst_id = dst.replace("/", "_").replace(".", "_").replace("-", "_")
                    lines.append(f"    {src_id} --> {dst_id}")

        # Add labels for nodes that appear in edges
        node_ids: set[str] = set()
        for src, dst in seen_edges:
            node_ids.add(src)
            node_ids.add(dst)

        label_lines: list[str] = []
        for path in node_ids:
            p = PurePosixPath(path)
            parts = p.parts
            label = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
            node_id = path.replace("/", "_").replace(".", "_").replace("-", "_")
            label_lines.append(f'    {node_id}["{label}"]')

        if not seen_edges:
            lines.append("    note[No resolved inter-file dependencies found]")

        return "\n".join(lines + label_lines)
