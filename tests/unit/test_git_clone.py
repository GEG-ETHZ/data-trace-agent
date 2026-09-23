"""Clone and checkout behaviour for repositories with large committed files.

A project repository can carry multi-GB data archives committed straight to
git. A full clone of one took ~120 s and ~3.8 GB, while Agent Engine killed the
worker after ~70 s. These tests cover the partial-clone path that skips large
blobs, and the fail-fast behaviour of git subprocesses.
"""

import os
import subprocess
import time
from typing import cast

import pytest
from git import GitCommandError, Repo
from google.adk.tools import ToolContext

from agent.tools import git_tools

_TEST_BLOB_LIMIT = 1024
_LARGE_NAME = "data/big file*.zip"


class _FakeToolContext:
    def __init__(self):
        self.state = {}


def _ctx() -> ToolContext:
    return cast(ToolContext, _FakeToolContext())


def _run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _missing_blobs(repo_dir: str, rev: str) -> list[str]:
    output = subprocess.run(
        ["git", "rev-list", "--objects", "--missing=print", "--no-walk", rev],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line[1:] for line in output.splitlines() if line.startswith("?")]


@pytest.fixture
def small_blob_limit(monkeypatch):
    monkeypatch.setattr(git_tools, "_MAX_BLOB_BYTES", _TEST_BLOB_LIMIT)


@pytest.fixture
def remote_with_large_file(tmp_path):
    """A file:// remote whose first commit has a large file that the second removes.

    Returns ``(url, commit_with_large_file, head_commit)``.
    """
    work = tmp_path / "work"
    work.mkdir()
    _run(["git", "init", "-b", "main"], work)
    _run(["git", "config", "user.email", "test@example.com"], work)
    _run(["git", "config", "user.name", "Test User"], work)
    (work / "data.dvc").write_text("outs:\n- md5: abc123\n  path: data\n")
    (work / "data").mkdir()
    (work / _LARGE_NAME).write_bytes(os.urandom(_TEST_BLOB_LIMIT * 4))
    _run(["git", "add", "-A"], work)
    _run(["git", "commit", "-m", "add data archive"], work)
    commit_with_large = Repo(work).head.commit.hexsha
    _run(["git", "rm", "-q", _LARGE_NAME], work)
    _run(["git", "commit", "-m", "remove data archive"], work)
    head = Repo(work).head.commit.hexsha

    remote = tmp_path / "remote.git"
    _run(["git", "clone", "--bare", str(work), str(remote)], tmp_path)
    _run(["git", "config", "uploadpack.allowFilter", "true"], remote)
    _run(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], remote)
    return f"file://{remote}", commit_with_large, head


def test_clone_repository_at_revision_skips_large_files(
    small_blob_limit, remote_with_large_file
):
    url, commit_with_large, _head = remote_with_large_file
    ctx = _ctx()

    result = git_tools.clone_repository_at_revision(url, commit_with_large, ctx)

    assert "Successfully cloned" in result
    repo_dir = ctx.state["repo_path"]
    assert os.path.exists(os.path.join(repo_dir, "data.dvc"))
    assert not os.path.exists(os.path.join(repo_dir, _LARGE_NAME))
    assert Repo(repo_dir).head.commit.hexsha == commit_with_large
    assert len(_missing_blobs(repo_dir, commit_with_large)) == 1


def test_clone_repository_at_revision_lists_and_explains_skipped_file(
    small_blob_limit, remote_with_large_file
):
    url, commit_with_large, _head = remote_with_large_file
    ctx = _ctx()
    git_tools.clone_repository_at_revision(url, commit_with_large, ctx)

    listed = git_tools.list_files(tool_context=ctx)
    content = git_tools.read_file_content(_LARGE_NAME, tool_context=ctx)

    assert _LARGE_NAME in listed.splitlines()
    assert "not downloaded" in content


def test_clone_remote_repository_skips_large_files_at_head(
    small_blob_limit, remote_with_large_file, tmp_path
):
    url, commit_with_large, _head = remote_with_large_file
    remote_dir = url.removeprefix("file://")
    _run(["git", "update-ref", "refs/heads/main", commit_with_large], remote_dir)
    ctx = _ctx()

    result = git_tools.clone_remote_repository(url, ctx)

    assert "Successfully cloned" in result
    repo_dir = ctx.state["repo_path"]
    assert os.path.exists(os.path.join(repo_dir, "data.dvc"))
    assert not os.path.exists(os.path.join(repo_dir, _LARGE_NAME))
    assert Repo(repo_dir).active_branch.name == "main"


def test_checkout_commit_skips_large_files(small_blob_limit, remote_with_large_file):
    url, commit_with_large, head = remote_with_large_file
    ctx = _ctx()
    git_tools.clone_repository_at_revision(url, head, ctx)

    result = git_tools.checkout_commit(commit_with_large, tool_context=ctx)

    assert "Successfully checked out" in result
    repo_dir = ctx.state["repo_path"]
    assert not os.path.exists(os.path.join(repo_dir, _LARGE_NAME))
    assert Repo(repo_dir).head.commit.hexsha == commit_with_large


def test_clone_repository_at_revision_rejects_unsafe_protocol(tmp_path):
    marker = tmp_path / "pwned"
    ctx = _ctx()

    result = git_tools.clone_repository_at_revision(
        f"ext::sh -c touch% {marker}", "abc123", ctx
    )

    assert result.startswith("ERROR")
    assert not marker.exists()
    assert "repo_path" not in ctx.state


def test_run_git_times_out_and_kills_child_processes(tmp_path):
    started = time.monotonic()

    with pytest.raises(GitCommandError, match="timed out"):
        git_tools._run_git(
            ["-c", "alias.slow=!sleep 30", "slow"],
            cwd=str(tmp_path),
            deadline=time.monotonic() + 0.5,
        )

    assert time.monotonic() - started < 5


def test_run_git_disables_terminal_prompt(tmp_path):
    output = git_tools._run_git(
        ["-c", "alias.showprompt=!printenv GIT_TERMINAL_PROMPT", "showprompt"],
        cwd=str(tmp_path),
        deadline=time.monotonic() + 10,
    )

    assert output.strip() == "0"


def test_run_git_redacts_credentials_in_errors(tmp_path):
    url = "https://oauth2:s3cret@invalid.nonexistent.example/r.git"  # pragma: allowlist secret

    with pytest.raises(GitCommandError) as excinfo:
        git_tools._run_git(
            ["ls-remote", url],
            cwd=str(tmp_path),
            deadline=time.monotonic() + 10,
        )

    assert "s3cret" not in str(excinfo.value)


def test_sparse_exclude_pattern_escapes_special_characters():
    assert git_tools._sparse_exclude_pattern("data/big file*.zip") == (
        "!/data/big file\\*.zip"
    )
    assert git_tools._sparse_exclude_pattern("a/[x]?.bin") == "!/a/\\[x\\]\\?.bin"
    assert git_tools._sparse_exclude_pattern("trailing ") == "!/trailing\\ "
