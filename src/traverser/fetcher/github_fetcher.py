"""GitHub repository fetcher — supports public and private repos."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any

from github import Auth, Github, GithubException
from github.ContentFile import ContentFile
from github.Repository import Repository

from traverser.config import Config
from traverser.models.repo_models import FileNode, RepoInfo, detect_language

logger = logging.getLogger(__name__)

# Maximum number of concurrent GitHub API requests when fetching file contents
# (used as fallback when git-clone is unavailable).
_FETCH_WORKERS = 15

# Regex to parse GitHub URLs in various forms:
# https://github.com/owner/repo
# https://github.com/owner/repo.git
# git@github.com:owner/repo.git
_GITHUB_URL_RE = re.compile(
    r"(?:https?://github\.com/|git@github\.com:)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$"
)


def parse_github_url(url: str) -> tuple[str, str]:
    """Return (owner, repo_name) from a GitHub URL.

    Raises ValueError if the URL is not a valid GitHub repository URL.
    """
    m = _GITHUB_URL_RE.match(url.strip().rstrip("/"))
    if not m:
        raise ValueError(
            f"Cannot parse GitHub URL: {url!r}. "
            "Expected format: https://github.com/owner/repo"
        )
    return m.group("owner"), m.group("repo")


def _is_binary_path(path: str, binary_extensions: frozenset[str]) -> bool:
    """Return True if the file extension indicates a binary file."""
    ext = PurePosixPath(path).suffix.lower()
    return ext in binary_extensions


def _should_skip_path(path: str, skip_directories: frozenset[str]) -> bool:
    """Return True if any component of the path is in the skip list."""
    parts = PurePosixPath(path).parts
    return any(part in skip_directories for part in parts)


def _decode_content(content_file: ContentFile) -> str | None:
    """Decode a GitHub ContentFile to a UTF-8 string, returning None for binaries."""
    try:
        raw = base64.b64decode(content_file.content)
        return raw.decode("utf-8")
    except (UnicodeDecodeError, Exception):
        return None  # Binary or undecodable file


def _git_available() -> bool:
    """Return True if the ``git`` executable is on PATH."""
    return shutil.which("git") is not None


def _clone_repo(clone_url: str, token: str | None, dest: Path) -> bool:
    """Shallow-clone a repository into *dest* using ``git clone --depth=1``.

    For authenticated access the token is embedded in the HTTPS URL so no
    credential helper configuration is required.  The URL is **never logged**
    to avoid leaking the token.

    Returns True on success, False on any error (timeout, auth failure, etc.).
    """
    auth_url = clone_url.replace("https://", f"https://{token}@", 1) if token else clone_url

    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", "--single-branch", auth_url, str(dest)],
            capture_output=True,
            timeout=300,  # 5 minutes
        )
        if result.returncode != 0:
            # Log stderr without the URL (may contain token).
            logger.warning(
                "git clone failed (exit %d): %s",
                result.returncode,
                result.stderr.decode(errors="replace")[:500],
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("git clone timed out after 300 s")
        return False
    except FileNotFoundError:
        logger.warning("git executable not found")
        return False


class GithubFetcher:
    """Fetches all source files from a GitHub repository."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def fetch(
        self,
        url: str,
        token: str | None = None,
        on_tree: Callable[[int], None] | None = None,
        on_file: Callable[[], None] | None = None,
    ) -> RepoInfo:
        """Fetch all files from a GitHub repository URL.

        Args:
            url: GitHub repository URL (public or private).
            token: GitHub Personal Access Token. Falls back to config.github_token.
            on_tree: Called once after the tree is scanned, with the number of
                files that will be fetched (excluding skipped/binary).
            on_file: Called after each individual file is successfully fetched.

        Returns:
            RepoInfo with all fetched FileNodes.
        """
        effective_token = token or self.config.github_token

        auth = Auth.Token(effective_token) if effective_token else None
        gh = Github(auth=auth, per_page=100)

        owner, repo_name = parse_github_url(url)
        logger.info("Fetching repository %s/%s", owner, repo_name)

        try:
            repo = gh.get_repo(f"{owner}/{repo_name}")
        except GithubException as exc:
            if exc.status == 404:
                raise ValueError(
                    f"Repository {owner}/{repo_name} not found. "
                    "If it is private, provide a GitHub token with read access."
                ) from exc
            raise

        head_sha = self._get_head_commit_sha(repo)
        if self.config.cache_enabled and head_sha:
            cached = self._load_repo_cache(owner, repo_name, expected_head_sha=head_sha)
            if cached is not None:
                logger.info(
                    "Using cached repository snapshot for %s/%s at %s",
                    owner,
                    repo_name,
                    head_sha,
                )
                if on_tree is not None:
                    on_tree(len(cached.files))
                if on_file is not None:
                    for _ in cached.files:
                        on_file()
                return cached.model_copy(
                    update={
                        "description": repo.description,
                        "default_branch": repo.default_branch,
                        "url": repo.html_url,
                        "clone_url": repo.clone_url,
                        "head_commit_sha": head_sha,
                        "from_cache": True,
                    }
                )

        files = self._walk_tree(repo, token=effective_token, on_tree=on_tree, on_file=on_file)

        repo_info = RepoInfo(
            owner=owner,
            repo_name=repo_name,
            full_name=repo.full_name,
            description=repo.description,
            default_branch=repo.default_branch,
            head_commit_sha=head_sha,
            url=repo.html_url,
            clone_url=repo.clone_url,
            files=files,
            from_cache=False,
        )
        if self.config.cache_enabled and head_sha:
            self._save_repo_cache(repo_info, head_sha)

        logger.info(
            "Fetched %d files from %s/%s", len(files), owner, repo_name
        )
        return repo_info

    def _get_head_commit_sha(self, repo: Repository) -> str | None:
        """Return the current default-branch head commit SHA for a repository."""
        branch = repo.default_branch
        if not branch:
            return None
        try:
            branch_obj = repo.get_branch(branch)
            return branch_obj.commit.sha
        except GithubException as exc:
            logger.warning("Could not resolve head SHA for %s: %s", repo.full_name, exc)
            return None

    def _repo_cache_path(self, owner: str, repo_name: str) -> Path:
        """Return the on-disk cache path for a repository snapshot."""
        key = hashlib.sha256(f"{owner}/{repo_name}".lower().encode("utf-8")).hexdigest()[:16]
        cache_dir = self.config.cache_dir / "repos"
        return cache_dir / f"{owner}_{repo_name}_{key}.json"

    def _load_repo_cache(
        self,
        owner: str,
        repo_name: str,
        expected_head_sha: str,
    ) -> RepoInfo | None:
        """Load a cached RepoInfo if it matches the remote head SHA."""
        path = self._repo_cache_path(owner, repo_name)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_sha = payload.get("head_commit_sha")
            if cached_sha != expected_head_sha:
                return None
            repo_payload = payload.get("repo_info")
            if not isinstance(repo_payload, dict):
                return None
            return RepoInfo.model_validate(repo_payload)
        except Exception as exc:
            logger.warning("Ignoring corrupted repository cache %s: %s", path, exc)
            return None

    def _save_repo_cache(self, repo_info: RepoInfo, head_sha: str) -> None:
        """Persist a fetched repository snapshot for fast reuse."""
        path = self._repo_cache_path(repo_info.owner, repo_info.repo_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "head_commit_sha": head_sha,
            "repo_info": repo_info.model_dump(mode="json"),
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Could not write repository cache %s: %s", path, exc)

    def _walk_tree(
        self,
        repo: Repository,
        token: str | None = None,
        on_tree: Callable[[int], None] | None = None,
        on_file: Callable[[], None] | None = None,
    ) -> list[FileNode]:
        """Walk the repository tree and collect all relevant files.

        Strategy (fastest first):
        1. ``git clone --depth=1`` — downloads the entire working tree in one
           network round-trip, then reads files directly from disk.  This is
           dramatically faster than fetching each blob individually and does not
           exhaust the GitHub API connection pool.
        2. Parallel Git Blobs API — fallback when ``git`` is unavailable or the
           clone fails (e.g., in network-restricted environments).
        """
        if _git_available():
            files = self._walk_tree_via_clone(repo, token=token, on_tree=on_tree, on_file=on_file)
            if files is not None:
                return files
            logger.info("git clone failed; falling back to GitHub Blobs API")

        return self._walk_tree_via_api(repo, on_tree=on_tree, on_file=on_file)

    # ── Clone-based strategy ──────────────────────────────────────────────────

    def _walk_tree_via_clone(
        self,
        repo: Repository,
        token: str | None = None,
        on_tree: Callable[[int], None] | None = None,
        on_file: Callable[[], None] | None = None,
    ) -> list[FileNode] | None:
        """Shallow-clone the repository and read files from disk.

        Returns a list of FileNodes on success, or None if the clone failed.
        The caller is responsible for falling back to the API strategy.
        """
        binary_exts = self.config.binary_extensions_set
        skip_dirs = self.config.skip_directories_set
        max_bytes = self.config.max_file_size_kb * 1024

        with tempfile.TemporaryDirectory(prefix="traverser_clone_") as tmp_str:
            clone_dest = Path(tmp_str) / "repo"
            clone_url = repo.clone_url  # always HTTPS

            logger.info("Cloning %s via git clone --depth=1", repo.full_name)
            if not _clone_repo(clone_url, token, clone_dest):
                return None

            # Build a sha-map from the git tree (1 API call) so we can key
            # our cache entries by blob SHA exactly as the API strategy does.
            sha_map: dict[str, str] = {}
            try:
                tree = repo.get_git_tree(repo.default_branch, recursive=True)
                for item in tree.tree:
                    if item.type == "blob":
                        sha_map[item.path] = item.sha
            except GithubException as exc:
                logger.warning("Could not fetch git tree for SHA map: %s", exc)
                # Continue without SHAs — use content hash as fallback.

            # Walk the cloned directory tree.
            eligible: list[Path] = []
            for abs_path in clone_dest.rglob("*"):
                if not abs_path.is_file():
                    continue
                rel_path = abs_path.relative_to(clone_dest).as_posix()
                if _should_skip_path(rel_path, skip_dirs):
                    logger.debug("Skipping (directory filter): %s", rel_path)
                    continue
                if _is_binary_path(rel_path, binary_exts):
                    logger.debug("Skipping (binary extension): %s", rel_path)
                    continue
                if abs_path.stat().st_size > max_bytes:
                    logger.debug(
                        "Skipping (too large: %d KB > %d KB): %s",
                        abs_path.stat().st_size // 1024,
                        self.config.max_file_size_kb,
                        rel_path,
                    )
                    continue
                eligible.append(abs_path)

            if on_tree is not None:
                on_tree(len(eligible))

            files: list[FileNode] = []
            for abs_path in eligible:
                rel_path = abs_path.relative_to(clone_dest).as_posix()
                try:
                    content = abs_path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    logger.debug("Skipping (read error): %s — %s", rel_path, exc)
                    if on_file is not None:
                        on_file()
                    continue

                # Use blob SHA from tree if available, fall back to content hash.
                sha = sha_map.get(rel_path) or hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()

                name = PurePosixPath(rel_path).name
                language = detect_language(name)
                files.append(
                    FileNode(
                        path=rel_path,
                        name=name,
                        language=language,
                        size_bytes=len(content.encode("utf-8")),
                        content=content,
                        sha=sha,
                    )
                )
                if on_file is not None:
                    on_file()

        # Sort to match the deterministic order the API strategy produces
        # (git tree order approximated by lexicographic sort).
        files.sort(key=lambda f: f.path)
        logger.info("Fetched %d files via git clone from %s", len(files), repo.full_name)
        return files

    # ── API-based strategy (fallback) ─────────────────────────────────────────

    def _walk_tree_via_api(
        self,
        repo: Repository,
        on_tree: Callable[[int], None] | None = None,
        on_file: Callable[[], None] | None = None,
    ) -> list[FileNode]:
        """Walk the repository tree recursively and collect all relevant files.

        Uses the GitHub Blobs API with a thread pool for parallel downloads.
        This is the fallback strategy when ``git clone`` is not available.
        """
        binary_exts = self.config.binary_extensions_set
        skip_dirs = self.config.skip_directories_set
        max_bytes = self.config.max_file_size_kb * 1024

        try:
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
        except GithubException as exc:
            logger.error("Failed to get git tree: %s", exc)
            return []

        blobs: list[Any] = [item for item in tree.tree if item.type == "blob"]
        logger.info("Tree contains %d blobs", len(blobs))

        # Pre-filter to count how many we will attempt to fetch before starting
        eligible: list[Any] = []
        for item in blobs:
            path: str = item.path
            if _should_skip_path(path, skip_dirs):
                logger.debug("Skipping (directory filter): %s", path)
                continue
            if _is_binary_path(path, binary_exts):
                logger.debug("Skipping (binary extension): %s", path)
                continue
            if item.size and item.size > max_bytes:
                logger.debug(
                    "Skipping (too large: %d KB > %d KB): %s",
                    item.size // 1024,
                    self.config.max_file_size_kb,
                    path,
                )
                continue
            eligible.append(item)

        # Notify caller of total so it can set up a determinate progress bar
        if on_tree is not None:
            on_tree(len(eligible))

        # Fetch file contents in parallel using the Git Blobs API.
        files: list[FileNode] = []

        def _fetch_blob(item: Any) -> FileNode | None:
            return self._fetch_blob(repo, item.path, item.sha)

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            futures = {pool.submit(_fetch_blob, item): item for item in eligible}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    files.append(result)
                if on_file is not None:
                    on_file()

        # Preserve deterministic ordering (original tree order)
        path_order = {item.path: idx for idx, item in enumerate(eligible)}
        files.sort(key=lambda f: path_order.get(f.path, 0))

        return files

    def _fetch_blob(
        self, repo: Repository, path: str, sha: str
    ) -> FileNode | None:
        """Fetch file content via the Git Blobs API (faster than get_contents).

        Uses ``repo.get_git_blob(sha)`` which only needs the blob SHA — no
        path resolution on the server side — and is safe for parallel calls.
        """
        try:
            blob = repo.get_git_blob(sha)
        except GithubException as exc:
            logger.warning("Could not fetch blob %s (%s): %s", path, sha[:8], exc)
            return None

        try:
            raw = base64.b64decode(blob.content)
            content = raw.decode("utf-8")
        except (UnicodeDecodeError, Exception):
            logger.debug("Skipping (binary content): %s", path)
            return None

        name = PurePosixPath(path).name
        language = detect_language(name)

        return FileNode(
            path=path,
            name=name,
            language=language,
            size_bytes=len(content.encode("utf-8")),
            content=content,
            sha=sha,
        )

    def _fetch_file(
        self, repo: Repository, path: str, sha: str
    ) -> FileNode | None:
        """Fetch the content of a single file."""
        try:
            content_file = repo.get_contents(path)
        except GithubException as exc:
            logger.warning("Could not fetch %s: %s", path, exc)
            return None

        # Handle list responses (shouldn't happen for file paths, but guard anyway)
        if isinstance(content_file, list):
            return None

        content = _decode_content(content_file)
        if content is None:
            logger.debug("Skipping (binary content): %s", path)
            return None

        name = PurePosixPath(path).name
        language = detect_language(name)

        return FileNode(
            path=path,
            name=name,
            language=language,
            size_bytes=len(content.encode("utf-8")),
            content=content,
            sha=sha,
        )
