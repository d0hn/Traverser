"""Repository and file data models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    VUE = "vue"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    CPP = "cpp"
    C = "c"
    CSHARP = "csharp"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    SCALA = "scala"
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    HTML = "html"
    CSS = "css"
    SQL = "sql"
    SHELL = "shell"
    DOCKERFILE = "dockerfile"
    UNKNOWN = "unknown"


# Maps file extensions to language enum values
EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".vue": Language.VUE,
    ".java": Language.JAVA,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".c": Language.C,
    ".h": Language.C,
    ".cs": Language.CSHARP,
    ".rb": Language.RUBY,
    ".php": Language.PHP,
    ".swift": Language.SWIFT,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".scala": Language.SCALA,
    ".md": Language.MARKDOWN,
    ".mdx": Language.MARKDOWN,
    ".json": Language.JSON,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".toml": Language.TOML,
    ".html": Language.HTML,
    ".htm": Language.HTML,
    ".css": Language.CSS,
    ".scss": Language.CSS,
    ".sass": Language.CSS,
    ".less": Language.CSS,
    ".sql": Language.SQL,
    ".sh": Language.SHELL,
    ".bash": Language.SHELL,
    ".zsh": Language.SHELL,
    ".fish": Language.SHELL,
    "Dockerfile": Language.DOCKERFILE,
}

# Languages worth deep-analysing with the LLM
ANALYSABLE_LANGUAGES: frozenset[Language] = frozenset(
    {
        Language.PYTHON,
        Language.JAVASCRIPT,
        Language.TYPESCRIPT,
        Language.VUE,
        Language.JAVA,
        Language.GO,
        Language.RUST,
        Language.CPP,
        Language.C,
        Language.CSHARP,
        Language.RUBY,
        Language.PHP,
        Language.SWIFT,
        Language.KOTLIN,
        Language.SCALA,
    }
)


def detect_language(filename: str) -> Language:
    """Detect the language of a file from its name/extension."""
    # Special cases first
    basename = filename.rsplit("/", 1)[-1]
    if basename in ("Dockerfile", "dockerfile"):
        return Language.DOCKERFILE
    if basename in ("Makefile", "makefile", "GNUmakefile"):
        return Language.SHELL

    dot_pos = basename.rfind(".")
    if dot_pos == -1:
        return Language.UNKNOWN

    ext = basename[dot_pos:].lower()
    return EXTENSION_TO_LANGUAGE.get(ext, Language.UNKNOWN)


class FileNode(BaseModel):
    """A single file fetched from the repository."""

    path: str  # full path relative to repo root
    name: str  # basename
    language: Language
    size_bytes: int
    content: str
    sha: str  # git blob SHA — used for cache invalidation

    @property
    def extension(self) -> str:
        dot = self.name.rfind(".")
        return self.name[dot:] if dot != -1 else ""

    @property
    def is_analysable(self) -> bool:
        return self.language in ANALYSABLE_LANGUAGES

    @property
    def line_count(self) -> int:
        return self.content.count("\n") + 1


class RepoInfo(BaseModel):
    """Metadata + file tree for a fetched GitHub repository."""

    owner: str
    repo_name: str
    full_name: str
    description: str | None = None
    default_branch: str = "main"
    head_commit_sha: str | None = None
    url: str
    clone_url: str
    files: list[FileNode] = Field(default_factory=list)
    from_cache: bool = False

    @property
    def display_name(self) -> str:
        return self.full_name

    @property
    def languages_used(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for f in self.files:
            lang = f.language.value
            if lang not in seen and f.language in ANALYSABLE_LANGUAGES:
                seen.add(lang)
                result.append(lang)
        return sorted(result)

    @property
    def analysable_files(self) -> list[FileNode]:
        return [f for f in self.files if f.is_analysable]
