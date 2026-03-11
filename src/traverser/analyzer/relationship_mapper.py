"""Builds a cross-file relationship graph and generates Mermaid output."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import networkx as nx

from traverser.models.doc_models import FileAnalysis, ProjectRelationships

logger = logging.getLogger(__name__)


def _resolve_relative_import(importer_path: str, module: str) -> str | None:
    """Try to resolve a relative Python import to a repo-relative path.

    Returns a candidate path like 'src/package/module.py' or None if
    resolution is not possible.
    """
    # e.g. importer = src/pkg/subpkg/file.py, module = .sibling
    #  → src/pkg/subpkg/sibling.py
    importer_dir = str(PurePosixPath(importer_path).parent)
    # module starts with '.' or is dotted (e.g. 'models.user')
    parts = module.lstrip(".").replace(".", "/")
    if not parts:
        return None
    candidate = f"{importer_dir}/{parts}.py"
    return candidate


def _module_to_paths(module: str, all_paths: set[str]) -> list[str]:
    """Map an absolute module name to candidate repo file paths.

    For a module like 'traverser.models.user', try:
      - traverser/models/user.py
      - src/traverser/models/user.py
      - traverser/models/user/__init__.py
    """
    rel = module.replace(".", "/")
    candidates = [
        f"{rel}.py",
        f"src/{rel}.py",
        f"{rel}/__init__.py",
        f"src/{rel}/__init__.py",
    ]
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
                    candidate = _resolve_relative_import(path, imp.module)
                    if candidate and candidate in all_paths:
                        resolved = [candidate]
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
