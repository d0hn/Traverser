"""Tests for the GitHub URL parser and file-filter helpers."""

from __future__ import annotations

import pytest

from traverser.config import Config
from traverser.fetcher.github_fetcher import (
    GithubFetcher,
    _clone_repo,
    _git_available,
    _is_binary_path,
    _should_skip_path,
    parse_github_url,
)
from traverser.models.repo_models import FileNode, Language, RepoInfo


class TestParseGithubUrl:
    def test_https_url(self) -> None:
        owner, repo = parse_github_url("https://github.com/octocat/Hello-World")
        assert owner == "octocat"
        assert repo == "Hello-World"

    def test_https_url_with_git_suffix(self) -> None:
        owner, repo = parse_github_url("https://github.com/octocat/Hello-World.git")
        assert owner == "octocat"
        assert repo == "Hello-World"

    def test_ssh_url(self) -> None:
        owner, repo = parse_github_url("git@github.com:octocat/Hello-World.git")
        assert owner == "octocat"
        assert repo == "Hello-World"

    def test_url_with_trailing_slash(self) -> None:
        owner, repo = parse_github_url("https://github.com/octocat/Hello-World/")
        assert owner == "octocat"
        assert repo == "Hello-World"

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse GitHub URL"):
            parse_github_url("https://gitlab.com/owner/repo")

    def test_not_a_url_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_github_url("not-a-url")

    def test_org_with_hyphens(self) -> None:
        owner, repo = parse_github_url("https://github.com/my-org/my-repo")
        assert owner == "my-org"
        assert repo == "my-repo"


class TestIsBinaryPath:
    _BINARY_EXTS = frozenset({".png", ".jpg", ".pdf", ".exe", ".pyc", ".lock"})

    def test_png_is_binary(self) -> None:
        assert _is_binary_path("assets/logo.png", self._BINARY_EXTS) is True

    def test_py_is_not_binary(self) -> None:
        assert _is_binary_path("src/main.py", self._BINARY_EXTS) is False

    def test_case_insensitive(self) -> None:
        assert _is_binary_path("IMAGE.PNG", self._BINARY_EXTS) is True

    def test_nested_path(self) -> None:
        assert _is_binary_path("deep/nested/photo.jpg", self._BINARY_EXTS) is True

    def test_lock_file(self) -> None:
        assert _is_binary_path("package-lock.json.lock", self._BINARY_EXTS) is True

    def test_no_extension(self) -> None:
        assert _is_binary_path("Makefile", self._BINARY_EXTS) is False


class TestShouldSkipPath:
    _SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".git", "dist", ".venv"})

    def test_node_modules(self) -> None:
        assert _should_skip_path("node_modules/lodash/index.js", self._SKIP_DIRS) is True

    def test_pycache(self) -> None:
        assert _should_skip_path("src/__pycache__/main.cpython-311.pyc", self._SKIP_DIRS) is True

    def test_normal_file(self) -> None:
        assert _should_skip_path("src/main.py", self._SKIP_DIRS) is False

    def test_dist(self) -> None:
        # 'dist' is in the skip list, so this path should be skipped
        assert _should_skip_path("dist/bundle.js", self._SKIP_DIRS) is True

    def test_non_skip_dir(self) -> None:
        # 'src' is not in the skip list
        assert _should_skip_path("src/bundle.js", self._SKIP_DIRS) is False

    def test_nested_skip_dir(self) -> None:
        assert _should_skip_path("packages/lib/node_modules/pkg/index.js", self._SKIP_DIRS) is True

    def test_similar_name_not_skipped(self) -> None:
        # 'nodes_modules' is NOT in the skip list
        assert _should_skip_path("nodes_modules/something.js", self._SKIP_DIRS) is False


class TestRepositorySnapshotCache:
    def test_save_then_load_with_matching_head_sha(self, tmp_path) -> None:
        config = Config(cache_dir=tmp_path / ".cache", cache_enabled=True)
        fetcher = GithubFetcher(config)

        repo = RepoInfo(
            owner="octocat",
            repo_name="hello-world",
            full_name="octocat/hello-world",
            description="cached snapshot",
            default_branch="main",
            head_commit_sha="abc123",
            url="https://github.com/octocat/hello-world",
            clone_url="https://github.com/octocat/hello-world.git",
            files=[
                FileNode(
                    path="src/main.py",
                    name="main.py",
                    language=Language.PYTHON,
                    size_bytes=16,
                    content="print('hello')\n",
                    sha="blob1",
                )
            ],
        )

        fetcher._save_repo_cache(repo, head_sha="abc123")
        loaded = fetcher._load_repo_cache(
            "octocat",
            "hello-world",
            expected_head_sha="abc123",
        )

        assert loaded is not None
        assert loaded.full_name == "octocat/hello-world"
        assert len(loaded.files) == 1
        assert loaded.files[0].path == "src/main.py"

    def test_load_returns_none_when_head_sha_differs(self, tmp_path) -> None:
        config = Config(cache_dir=tmp_path / ".cache", cache_enabled=True)
        fetcher = GithubFetcher(config)

        repo = RepoInfo(
            owner="octocat",
            repo_name="hello-world",
            full_name="octocat/hello-world",
            default_branch="main",
            head_commit_sha="oldsha",
            url="https://github.com/octocat/hello-world",
            clone_url="https://github.com/octocat/hello-world.git",
            files=[],
        )
        fetcher._save_repo_cache(repo, head_sha="oldsha")

        loaded = fetcher._load_repo_cache(
            "octocat",
            "hello-world",
            expected_head_sha="newsha",
        )
        assert loaded is None


class TestGitAvailable:
    def test_returns_bool(self) -> None:
        result = _git_available()
        assert isinstance(result, bool)

    def test_git_is_available_in_test_env(self) -> None:
        # git should be available in any standard CI / dev environment
        assert _git_available() is True

    def test_returns_false_when_git_missing(self, monkeypatch) -> None:
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        assert _git_available() is False


class TestCloneRepo:
    def test_timeout_returns_false(self, monkeypatch, tmp_path) -> None:
        import subprocess

        def _raise_timeout(*_a, **_kw):  # noqa: ANN202
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=300)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        result = _clone_repo("https://github.com/owner/repo.git", None, tmp_path / "dest")
        assert result is False

    def test_nonzero_exit_returns_false(self, monkeypatch, tmp_path) -> None:
        import subprocess

        mock_result = subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout=b"", stderr=b"fatal: repo not found"
        )
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: mock_result)
        result = _clone_repo("https://github.com/owner/repo.git", None, tmp_path / "dest")
        assert result is False

    def test_success_returns_true(self, monkeypatch, tmp_path) -> None:
        import subprocess

        mock_result = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=b"", stderr=b""
        )
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: mock_result)
        result = _clone_repo("https://github.com/owner/repo.git", None, tmp_path / "dest")
        assert result is True

    def test_token_embedded_in_url(self, monkeypatch, tmp_path) -> None:
        """Token must be embedded in the HTTPS URL but never logged."""
        import subprocess

        captured: list[list[str]] = []

        def _capture_subprocess_call(cmd, **_kw):  # noqa: ANN202
            captured.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", _capture_subprocess_call)
        _clone_repo("https://github.com/owner/repo.git", "mytoken", tmp_path / "dest")

        assert captured, "subprocess.run was not called"
        clone_cmd = captured[0]
        assert any("mytoken@github.com" in part for part in clone_cmd), (
            "Token should be embedded in the clone URL"
        )
