from pathlib import Path
import re

SAFE_RE = re.compile(r"[^a-zA-Z0-9_\-]")
ROW_RE = re.compile(
    r"^\| `(?P<path>[^`]+)` \| \[(?P<label>[^\]]+)\]\((?P<link>[^)]+)\) \| (?P<tier>[^|]+) \| (?P<summary>.*) \|$"
)


def anchor(path: str) -> str:
    return "brief-" + SAFE_RE.sub("-", path).strip("-").lower()


def render_bundle(bundle_no: int, sections: list[tuple[str, str]], repo_slug: str) -> str:
    chunks: list[str] = []
    for p, content in sections:
        chunks.append(f'<a id="{anchor(p)}"></a>\n\n{content.strip()}')
    joined = "\n\n---\n\n".join(chunks)
    return (
        f"# Brief File Docs Bundle {bundle_no} — {repo_slug}\n\n"
        "> Post-processed (no LLM calls)\n\n"
        "---\n\n"
        f"{joined}\n"
    )


def compact_root(root: Path, bundle_size: int = 100) -> dict[str, int]:
    files_dir = root / "files"
    index_path = root / "FILE_INDEX.md"
    if not files_dir.exists() or not index_path.exists():
        return {"brief_docs": 0, "bundles": 0, "deleted": 0}

    lines = index_path.read_text(encoding="utf-8").splitlines()
    brief_rows: list[dict[str, str | int]] = []

    for i, line in enumerate(lines):
        m = ROW_RE.match(line)
        if not m:
            continue
        tier = m.group("tier").strip()
        if "🟡" not in tier:
            continue
        brief_rows.append(
            {
                "line_idx": i,
                "path": m.group("path"),
                "link": m.group("link"),
                "summary": m.group("summary"),
            }
        )

    if not brief_rows:
        return {"brief_docs": 0, "bundles": 0, "deleted": 0}

    for old in files_dir.glob("BRIEF_BUNDLE_*.md"):
        old.unlink(missing_ok=True)

    deleted = 0
    for start in range(0, len(brief_rows), bundle_size):
        chunk = brief_rows[start : start + bundle_size]
        bno = (start // bundle_size) + 1
        bname = f"BRIEF_BUNDLE_{bno:02d}.md"

        sections: list[tuple[str, str]] = []
        for row in chunk:
            path = str(row["path"])
            source_rel = str(row["link"]).split("#", 1)[0]
            source = root / source_rel
            if source.exists() and not source.name.startswith("BRIEF_BUNDLE_"):
                sections.append((path, source.read_text(encoding="utf-8")))

        (files_dir / bname).write_text(
            render_bundle(bno, sections, root.name.replace("_", "/", 1)),
            encoding="utf-8",
        )

        for row in chunk:
            path = str(row["path"])
            summary = str(row["summary"])
            line_idx = int(row["line_idx"])
            lines[line_idx] = (
                f"| `{path}` | [{bname}](files/{bname}#{anchor(path)}) | 🟡 | {summary} |"
            )

            source_rel = str(row["link"]).split("#", 1)[0]
            source = root / source_rel
            if source.exists() and not source.name.startswith("BRIEF_BUNDLE_"):
                source.unlink(missing_ok=True)
                deleted += 1

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "brief_docs": len(brief_rows),
        "bundles": (len(brief_rows) + bundle_size - 1) // bundle_size,
        "deleted": deleted,
    }


if __name__ == "__main__":
    output_dir = Path("/Users/mkoniarek/Dev/traverser/output")
    for root in sorted(output_dir.iterdir()):
        if not root.is_dir():
            continue
        stats = compact_root(root)
        if stats["brief_docs"]:
            print(f"{root.name}: brief={stats['brief_docs']} bundles={stats['bundles']} deleted={stats['deleted']}")
