"""GitHub repository fetcher — supports public and private repos."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from github import Auth, Github, GithubException
from github.ContentFile import ContentFile
from github.Repository import Repository

from traverser.config import Config
from traverser.models.repo_models import FileNode, Language, RepoInfo, detect_language

logger = logging.getLogger(__name__)

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

        files = self._walk_tree(repo, on_tree=on_tree, on_file=on_file)

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
        on_tree: Callable[[int], None] | None = None,
        on_file: Callable[[], None] | None = None,
    ) -> list[FileNode]:
        """Walk the repository tree recursively and collect all relevant files."""
        binary_exts = self.config.binary_extensions_set
        skip_dirs = self.config.skip_directories_set
        max_bytes = self.config.max_file_size_kb * 1024

        files: list[FileNode] = []

        try:
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
        except GithubException as exc:
            logger.error("Failed to get git tree: %s", exc)
            return files

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

        for item in eligible:
            file_node = self._fetch_file(repo, item.path, item.sha)
            if file_node is not None:
                files.append(file_node)
            if on_file is not None:
                on_file()

        return files

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
