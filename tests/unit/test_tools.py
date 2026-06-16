"""Unit tests for agent tools — no network, no GCP credentials required.

These exercise the ported Git/DVC tools against a throwaway local Git
repository created per test, plus the pure helper functions and the
``tool_context``-required guard rails.
"""

import os
import subprocess
from typing import cast

import pandas as pd
import pytest
from google.adk.tools import ToolContext

from agent.tools import data_tools, git_tools
from agent.tools.git_tools import (
    _looks_like_remote_repo,
    _redact_credentials,
    _render_yaml_value,
    _resolve_clone_url,
    _split_git_url,
    _to_ssh_url,
)


class _FakeToolContext:
    """Minimal stand-in for ADK's ToolContext — only ``.state`` is used."""

    def __init__(self, state=None):
        self.state = state if state is not None else {}


def fake_ctx(state=None) -> ToolContext:
    """Return a fake ToolContext, typed as ToolContext for the type checker."""
    return cast(ToolContext, _FakeToolContext(state))


def _run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    """A local Git repo with a paired `*.meta.yaml` / `*.dvc` committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    (repo / "foo.meta.yaml").write_text("title: Foo\ndescription: A dataset\n")
    (repo / "foo.dvc").write_text(
        "outs:\n"
        "- md5: md5fixturevalue\n"
        "  path: foo.parquet\n"
        "meta:\n"
        "  repo_url: https://example.com/foo.git\n"
    )
    (repo / "bigquery-location-for-dvc.yml").write_text(
        "gcp:\n"
        "  project: geg-core-dev\n"
        "  location: europe-west6\n"
        "gcs:\n"
        "  bucket: geg-core-dev-project-beach\n"
        "bigquery:\n"
        "  dataset_raw: project_beach_raw\n"
        "  dataset: project_beach\n"
    )
    # Also init DVC and add a remote
    _run(["dvc", "init", "--no-scm"], repo)
    _run(["dvc", "remote", "add", "my-gcs", "gs://my-bucket/data", "--local"], repo)
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "init"], repo)
    return str(repo)


@pytest.fixture
def ctx(git_repo):
    return fake_ctx(state={"repo_path": git_repo})


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_looks_like_remote_repo_true_for_urls():
    assert _looks_like_remote_repo("https://github.com/x/y.git")
    assert _looks_like_remote_repo("git@github.com:x/y.git")
    assert _looks_like_remote_repo("ssh://git@host/x.git")


def test_looks_like_remote_repo_false_for_local_path(git_repo):
    assert _looks_like_remote_repo(git_repo) is False


def test_render_yaml_value_scalar():
    assert _render_yaml_value("hello") == "hello"
    assert _render_yaml_value(42) == "42"


def test_render_yaml_value_dict():
    rendered = _render_yaml_value({"a": 1, "b": 2})
    assert "a: 1" in rendered
    assert "b: 2" in rendered


def test_to_ssh_url_converts_https():
    assert (
        _to_ssh_url("https://gitlab.example.com/group/repo.git")
        == "git@gitlab.example.com:group/repo.git"
    )


def test_to_ssh_url_converts_http_and_nested_path():
    assert (
        _to_ssh_url("http://github.com/org/team/repo.git")
        == "git@github.com:org/team/repo.git"
    )


def test_to_ssh_url_leaves_ssh_unchanged():
    url = "git@gitlab.example.com:group/repo.git"
    assert _to_ssh_url(url) == url


def test_to_ssh_url_preserves_embedded_credentials():
    # A deploy-token URL must not be rewritten (it would lose the token).
    url = "https://oauth2:tok123@gitlab.example.com/group/repo.git"  # pragma: allowlist secret
    assert _to_ssh_url(url) == url


def test_to_ssh_url_preserves_port_and_local_path():
    assert _to_ssh_url("https://host:8443/x.git") == "https://host:8443/x.git"
    assert _to_ssh_url("/local/path/repo") == "/local/path/repo"


def test_split_git_url_https_and_ssh():
    assert _split_git_url("https://gitlab.example.com/group/repo.git") == (
        "gitlab.example.com",
        "group/repo.git",
    )
    assert _split_git_url("git@gitlab.example.com:group/repo.git") == (
        "gitlab.example.com",
        "group/repo.git",
    )


def test_split_git_url_local_path_is_none():
    assert _split_git_url("/local/path/repo") is None


# ---------------------------------------------------------------------------
# Clone-URL resolution: SSH locally, token-HTTPS in a headless runtime
# ---------------------------------------------------------------------------


def test_resolve_clone_url_no_token_uses_ssh(monkeypatch):
    monkeypatch.delenv("GIT_AUTH_TOKEN", raising=False)
    assert (
        _resolve_clone_url("https://gitlab.example.com/group/repo.git")
        == "git@gitlab.example.com:group/repo.git"
    )


def test_resolve_clone_url_token_injects_https(monkeypatch):
    monkeypatch.setenv("GIT_AUTH_TOKEN", "tok")  # pragma: allowlist secret
    monkeypatch.setenv("GIT_AUTH_HOST", "gitlab.example.com")
    expected = "https://oauth2:tok@gitlab.example.com/group/repo.git"  # pragma: allowlist secret
    # Both SSH and HTTPS inputs to the same host become token HTTPS.
    assert _resolve_clone_url("git@gitlab.example.com:group/repo.git") == expected
    assert _resolve_clone_url("https://gitlab.example.com/group/repo.git") == expected


def test_resolve_clone_url_token_custom_username(monkeypatch):
    monkeypatch.setenv("GIT_AUTH_TOKEN", "tok")  # pragma: allowlist secret
    monkeypatch.setenv("GIT_AUTH_HOST", "gitlab.example.com")
    monkeypatch.setenv("GIT_AUTH_USERNAME", "gitlab-ci-token")
    assert (
        _resolve_clone_url("git@gitlab.example.com:g/r.git")
        == "https://gitlab-ci-token:tok@gitlab.example.com/g/r.git"  # pragma: allowlist secret
    )


def test_resolve_clone_url_token_host_mismatch_falls_back_to_ssh(monkeypatch):
    monkeypatch.setenv("GIT_AUTH_TOKEN", "tok")  # pragma: allowlist secret
    monkeypatch.setenv("GIT_AUTH_HOST", "gitlab.example.com")
    # A different host gets no token — falls back to SSH conversion.
    assert _resolve_clone_url("https://github.com/o/r.git") == "git@github.com:o/r.git"


def test_resolve_clone_url_token_host_defaults_to_repo_url(monkeypatch):
    monkeypatch.setenv("GIT_AUTH_TOKEN", "tok")  # pragma: allowlist secret
    monkeypatch.delenv("GIT_AUTH_HOST", raising=False)
    monkeypatch.setenv("REPO_URL", "https://gitlab.example.com/group/registry.git")
    assert (
        _resolve_clone_url("git@gitlab.example.com:group/repo.git")
        == "https://oauth2:tok@gitlab.example.com/group/repo.git"  # pragma: allowlist secret
    )


def test_redact_credentials_masks_token():
    redacted = _redact_credentials(
        "fatal: https://oauth2:supersecret@gitlab.example.com/x.git not found"  # pragma: allowlist secret
    )
    assert "supersecret" not in redacted
    assert "https://***@gitlab.example.com/x.git" in redacted


# ---------------------------------------------------------------------------
# Git tools — happy paths
# ---------------------------------------------------------------------------


def test_set_repository_configures_state(git_repo):
    ctx = fake_ctx()
    result = git_tools.set_repository(git_repo, ctx)
    assert "configured successfully" in result
    assert ctx.state["repo_path"]


def test_set_repository_invalid_path_returns_error(tmp_path):
    ctx = fake_ctx()
    result = git_tools.set_repository(str(tmp_path / "not-a-repo"), ctx)
    assert result.startswith("ERROR:")


def test_find_meta_yaml_files_finds_and_merges(ctx):
    result = git_tools.find_meta_yaml_files(tool_context=ctx)
    assert "foo.meta.yaml" in result
    assert "Foo" in result
    # merged from the paired .dvc file
    assert "repo_url" in result or "md5" in result


def test_find_dvc_files_finds_dvc(ctx):
    result = git_tools.find_dvc_files(tool_context=ctx)
    assert "foo.dvc" in result
    assert "md5fixturevalue" in result


def test_find_top_level_yaml_files_finds_gcp_settings(ctx):
    result = git_tools.find_top_level_yaml_files(tool_context=ctx)
    assert "bigquery-location-for-dvc.yml" in result
    assert "gcp" in result
    assert "bucket" in result
    assert "dataset_raw" in result


def test_find_top_level_yaml_files_no_matches_returns_message(ctx, git_repo):
    # Temporarily remove all top-level yaml files to test the "no matches" case
    repo_path = ctx.state["repo_path"]
    yaml_files = [f for f in os.listdir(repo_path) if f.endswith((".yml", ".yaml"))]
    for f in yaml_files:
        full_path = os.path.join(repo_path, f)
        os.remove(full_path)
    _run(["git", "add", "."], repo_path)
    _run(["git", "commit", "-m", "remove yaml files"], repo_path)

    result = git_tools.find_top_level_yaml_files(tool_context=ctx)
    assert "No top-level" in result

    # Restore the files
    _run(["git", "revert", "--no-edit", "HEAD"], repo_path)


def test_list_projects_lists_project(ctx):
    result = git_tools.list_projects(tool_context=ctx)
    assert "foo" in result
    assert "Foo" in result


def test_get_dvc_md5_returns_hash(ctx):
    result = git_tools.get_dvc_md5("foo.dvc", tool_context=ctx)
    assert result == "md5fixturevalue"


def test_get_repo_url_from_dvc_file(ctx):
    result = git_tools.get_repo_url_from_dvc_file("foo.dvc", tool_context=ctx)
    assert result == "https://example.com/foo.git"


def test_find_commit_by_hash_string(ctx):
    result = git_tools.find_commit_by_hash_string("md5fixturevalue", tool_context=ctx)
    # the commit that introduced the hash string should be found
    assert "No commits found" not in result
    assert "ERROR" not in result


def test_list_files(ctx):
    result = git_tools.list_files(tool_context=ctx)
    assert "foo.dvc" in result
    assert "foo.meta.yaml" in result


def test_checkout_commit_invalid_returns_error(ctx):
    result = git_tools.checkout_commit("deadbeef", tool_context=ctx)
    assert result.startswith("ERROR")


# ---------------------------------------------------------------------------
# Git tools — guard rails
# ---------------------------------------------------------------------------


def test_git_tools_require_tool_context():
    assert git_tools.find_meta_yaml_files() == "ERROR: tool_context is required."
    assert git_tools.find_top_level_yaml_files() == "ERROR: tool_context is required."
    assert git_tools.find_dvc_files() == "ERROR: tool_context is required."
    assert git_tools.list_projects() == "ERROR: tool_context is required."
    assert git_tools.get_dvc_md5("x.dvc") == "ERROR: tool_context is required."
    assert (
        git_tools.find_commit_by_hash_string("x") == "ERROR: tool_context is required."
    )
    assert git_tools.checkout_commit("x") == "ERROR: tool_context is required."
    assert git_tools.list_files() == "ERROR: tool_context is required."
    assert (
        git_tools.get_repo_url_from_dvc_file("x.dvc")
        == "ERROR: tool_context is required."
    )


def test_get_repo_no_repository_configured(monkeypatch):
    monkeypatch.delenv("REPO_URL", raising=False)
    ctx = fake_ctx()
    result = git_tools.find_meta_yaml_files(tool_context=ctx)
    assert "No repository configured" in result


def test_repo_url_rejects_local_folder(monkeypatch, git_repo):
    # REPO_URL must be a remote Git URL, not a local folder path.
    monkeypatch.setenv("REPO_URL", git_repo)
    ctx = fake_ctx()
    result = git_tools.find_meta_yaml_files(tool_context=ctx)
    assert "must be a remote Git URL" in result


# ---------------------------------------------------------------------------
# Data tools
# ---------------------------------------------------------------------------


def test_data_tools_require_tool_context():
    assert data_tools.dvc_pull() == "ERROR: tool_context is required."
    assert data_tools.dvc_list_files() == "ERROR: tool_context is required."
    assert data_tools.dvc_remote_list() == "ERROR: tool_context is required."
    assert data_tools.inspect_parquet_file("x") == "ERROR: tool_context is required."
    assert data_tools.analyze_parquet_file("x") == "ERROR: tool_context is required."
    assert data_tools.inspect_yaml_file("x") == "ERROR: tool_context is required."
    assert data_tools.analyze_yaml_file("x") == "ERROR: tool_context is required."
    assert data_tools.list_files_in_directory("x") == "ERROR: tool_context is required."


def test_data_tools_require_repo_path():
    ctx = fake_ctx()
    assert "Repository path not set" in data_tools.inspect_yaml_file("x", ctx)
    assert "Repository path not set" in data_tools.list_files_in_directory("x", ctx)


def test_inspect_yaml_file(ctx):
    result = data_tools.inspect_yaml_file("foo.meta.yaml", tool_context=ctx)
    assert "Top-level keys" in result
    assert "title" in result


def test_analyze_yaml_file(ctx):
    result = data_tools.analyze_yaml_file("foo.meta.yaml", tool_context=ctx)
    assert "top-level keys" in result


def test_dvc_remote_list_shows_remotes(ctx):
    result = data_tools.dvc_remote_list(tool_context=ctx)
    assert "my-gcs" in result
    assert "gs://my-bucket/data" in result


def test_inspect_yaml_file_missing_returns_error(ctx):
    result = data_tools.inspect_yaml_file("missing.yaml", tool_context=ctx)
    assert result.startswith("ERROR: File not found")


def test_list_files_in_directory(ctx, git_repo):
    result = data_tools.list_files_in_directory(".", tool_context=ctx)
    assert "foo.dvc" in result


def test_apply_dvc_local_config_writes_file(tmp_path, monkeypatch):
    dvc_dir = tmp_path / ".dvc"
    dvc_dir.mkdir()
    monkeypatch.setenv(
        "DVC_CONFIG_LOCAL",
        "['remote \"webdav\"']\n    user = u\n    password = p\n",
    )
    data_tools._apply_dvc_local_config(str(tmp_path))
    written = (dvc_dir / "config.local").read_text()
    assert 'remote "webdav"' in written
    assert "password = p" in written


def test_apply_dvc_local_config_noop_without_env(tmp_path, monkeypatch):
    dvc_dir = tmp_path / ".dvc"
    dvc_dir.mkdir()
    monkeypatch.delenv("DVC_CONFIG_LOCAL", raising=False)
    data_tools._apply_dvc_local_config(str(tmp_path))
    assert not (dvc_dir / "config.local").exists()


def test_apply_dvc_local_config_noop_without_dvc_dir(tmp_path, monkeypatch):
    # No .dvc dir → nothing written, no error.
    monkeypatch.setenv("DVC_CONFIG_LOCAL", "[core]\n    remote = x\n")
    data_tools._apply_dvc_local_config(str(tmp_path))
    assert not (tmp_path / ".dvc" / "config.local").exists()


def test_inspect_and_analyze_parquet(tmp_path):
    parquet_dir = tmp_path / "data"
    parquet_dir.mkdir()
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    df.to_parquet(parquet_dir / "data.parquet")
    ctx = fake_ctx(state={"repo_path": str(parquet_dir)})

    inspected = data_tools.inspect_parquet_file("data.parquet", tool_context=ctx)
    assert "Schema:" in inspected
    assert "Sample Data:" in inspected

    analyzed = data_tools.analyze_parquet_file("data.parquet", tool_context=ctx)
    assert "Data Description:" in analyzed
    assert "Error Summary:" in analyzed


# ---------------------------------------------------------------------------
# Error and edge-case branches
# ---------------------------------------------------------------------------


def test_dvc_pull_in_non_dvc_repo_returns_error(ctx):
    # The git repo is not a DVC project, so `dvc pull` exits non-zero.
    result = data_tools.dvc_pull(tool_context=ctx)
    assert result.startswith("ERROR")


def test_dvc_list_files_returns_string(ctx):
    # `dvc list` runs against the git repo and returns its (possibly empty)
    # listing of DVC-tracked paths as a string.
    result = data_tools.dvc_list_files(tool_context=ctx)
    assert isinstance(result, str)


def test_clone_remote_repository_invalid_returns_error():
    ctx = fake_ctx()
    result = git_tools.clone_remote_repository("/no/such/local/path", ctx)
    assert result.startswith("ERROR")


def test_get_dvc_md5_missing_file_returns_error(ctx):
    result = git_tools.get_dvc_md5("does-not-exist.dvc", tool_context=ctx)
    assert result.startswith("ERROR")


def test_get_repo_url_missing_file_returns_error(ctx):
    result = git_tools.get_repo_url_from_dvc_file("missing.dvc", tool_context=ctx)
    assert "File not found" in result


def test_get_repo_url_no_repo_path_returns_error():
    ctx = fake_ctx()
    result = git_tools.get_repo_url_from_dvc_file("foo.dvc", tool_context=ctx)
    assert "Repository path not set" in result


def test_find_meta_yaml_files_explicit_branch(ctx):
    result = git_tools.find_meta_yaml_files(branch="main", tool_context=ctx)
    assert "foo.meta.yaml" in result


def test_find_meta_yaml_files_unknown_branch_returns_error(ctx):
    result = git_tools.find_meta_yaml_files(branch="nope", tool_context=ctx)
    assert result.startswith("ERROR")


def test_inspect_parquet_missing_file_returns_error(ctx):
    result = data_tools.inspect_parquet_file("missing.parquet", tool_context=ctx)
    assert "File not found" in result


def test_list_files_in_directory_missing_returns_error(ctx):
    result = data_tools.list_files_in_directory("no_such_dir", tool_context=ctx)
    assert "Directory not found" in result
