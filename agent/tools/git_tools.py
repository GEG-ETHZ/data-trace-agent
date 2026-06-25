"""
Git and DVC metadata tools for the Data Trace Agent.

Every function in this module is registered as a callable tool on an ADK Agent.
The LLM drives the ReAct loop: it calls these tools to gather evidence, then
synthesises a final structured answer for the user.

Tool inventory
--------------
set_repository              - Point the agent at a local Git repo directory or remote URL.
find_meta_yaml_files        - Find and extract all `*.meta.yaml` files in a branch/commit.
find_dvc_files              - Find and extract all `*.dvc` files in a branch/commit.
list_projects               - List projects by finding `*.meta.yaml` files.
clone_remote_repository     - Clone a remote Git repository URL into a temporary directory.
get_dvc_md5                 - Extract the MD5 hash from the 'outs' section of a .dvc file.
find_commit_by_hash_string  - Find a git commit by a string in its history.
checkout_commit             - Check out a specific git commit by its hash.
list_files                  - List all files in the current checkout of the repository.
get_repo_url_from_dvc_file  - Extract the repository URL from a .dvc file.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
from typing import Any, cast

import git
import yaml
from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STATE_KEY = "repo_path"


def _get_repo(tool_context: ToolContext) -> Repo:
    """Retrieve the active Repo from ADK session state, or raise.

    On a fresh session the active repository defaults to the DVC registry
    cloned from the ``REPO_URL`` environment variable. ``REPO_URL`` must be a
    remote Git URL (https://, git@, or ssh://) — not a local folder.
    """
    repo_path: str | None = tool_context.state.get(_STATE_KEY)
    if not repo_path:
        repo_url = os.getenv("REPO_URL")
        if not repo_url:
            raise ValueError(
                "No repository configured. Set the REPO_URL environment variable "
                "to a remote Git URL, or call set_repository first."
            )
        if not _looks_like_remote_repo(repo_url):
            raise ValueError(
                f"REPO_URL must be a remote Git URL, got '{repo_url}'. "
                "Provide an https://, git@, or ssh:// URL to the DVC registry "
                "repository."
            )
        try:
            resolved = _resolve_repository_location(repo_url)
        except ValueError as exc:
            raise ValueError(
                f"Could not clone the DVC registry from REPO_URL: {exc}"
            ) from exc
        tool_context.state[_STATE_KEY] = resolved
        repo_path = resolved

    try:
        return Repo(repo_path, search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        raise ValueError(f"Cannot open Git repository at '{repo_path}': {exc}") from exc


def _looks_like_remote_repo(repo_location: str) -> bool:
    """Check if a location string looks like a remote Git URL."""
    candidate = os.path.expanduser(repo_location)
    if os.path.exists(candidate):
        return False
    normalized = repo_location.lower()
    return (
        normalized.startswith("http://")
        or normalized.startswith("https://")
        or normalized.startswith("git@")
        or normalized.startswith("ssh://")
        or normalized.endswith(".git")
    )


def _split_git_url(url: str) -> tuple[str, str] | None:
    """Return ``(host, path)`` for an HTTP(S) or scp-style SSH Git URL.

    Returns ``None`` for local paths, and for HTTP(S) URLs that already embed
    credentials (``user@host``) or a port (``host:443``) — those are left
    untouched by callers.
    """
    normalized = url.lower()
    if normalized.startswith("http://") or normalized.startswith("https://"):
        parsed = urllib.parse.urlparse(url)
        if "@" in parsed.netloc or ":" in parsed.netloc:
            return None
        host = parsed.netloc
        path = parsed.path.lstrip("/")
        return (host, path) if host and path else None

    # scp-style SSH: git@host:group/repo.git (or any user@host:path)
    match = re.match(r"^[^@/]+@([^:/]+):(.+)$", url)
    if match:
        return match.group(1), match.group(2)
    return None


def _git_token_for_host(host: str) -> tuple[str, str] | None:
    """Return ``(username, token)`` if a Git auth token is configured for ``host``.

    Reads ``GIT_AUTH_TOKEN`` (the token), ``GIT_AUTH_USERNAME`` (defaults to
    ``oauth2``), and ``GIT_AUTH_HOST`` (the host the token is valid for). When
    ``GIT_AUTH_HOST`` is unset it defaults to the host of ``REPO_URL``, so a
    single token covers the registry and the project repos on the same server.
    """
    token = os.getenv("GIT_AUTH_TOKEN")
    if not token:
        return None

    configured_host = os.getenv("GIT_AUTH_HOST")
    if not configured_host:
        repo_url = os.getenv("REPO_URL")
        parts = _split_git_url(repo_url) if repo_url else None
        configured_host = parts[0] if parts else None

    if configured_host and host == configured_host:
        return os.getenv("GIT_AUTH_USERNAME", "oauth2"), token
    return None


def _to_ssh_url(repo_location: str) -> str:
    """Convert an HTTP(S) Git URL to its SSH (scp-like) form.

    e.g. ``https://gitlab.example.com/group/repo.git``
      -> ``git@gitlab.example.com:group/repo.git``

    Cloning over SSH uses the developer's SSH key, whereas HTTPS would require
    stored credentials. URLs that already embed credentials (``user:token@host``)
    or specify a port are left unchanged, as are non-HTTP URLs (already SSH,
    local paths, etc.).
    """
    parts = _split_git_url(repo_location)
    if parts is None or not repo_location.lower().startswith(("http://", "https://")):
        return repo_location
    host, path = parts
    return f"git@{host}:{path}"


def _resolve_clone_url(repo_location: str) -> str:
    """Rewrite a Git URL for the credentials available in the current runtime.

    - If a token is configured (``GIT_AUTH_TOKEN``) for the URL's host, return an
      HTTPS URL with the token embedded. This works in headless runtimes such as
      Vertex AI Agent Engine, which have no SSH key.
    - Otherwise convert HTTP(S) URLs to their SSH form so local clones use the
      developer's SSH key.

    Local paths and URLs that already embed credentials or a port are returned
    unchanged.
    """
    parts = _split_git_url(repo_location)
    if parts is None:
        return repo_location
    host, path = parts
    cred = _git_token_for_host(host)
    if cred:
        username, token = cred
        # URL-encode so special chars in deploy token names (e.g. "+") are safe.
        username = urllib.parse.quote(username, safe="")
        token = urllib.parse.quote(token, safe="")
        return f"https://{username}:{token}@{host}/{path}"
    return _to_ssh_url(repo_location)


def _redact_credentials(text: str) -> str:
    """Mask ``user:token@`` credentials in URLs so tokens never reach output."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)


def _resolve_repository_location(repo_location: str) -> str:
    """Resolve a repository location (local path or remote URL) to an absolute path."""
    repo_location = _resolve_clone_url(repo_location)
    candidate = os.path.expanduser(repo_location)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)

    if _looks_like_remote_repo(repo_location):
        temp_dir = tempfile.mkdtemp(prefix="data_trace_agent_repo_")
        try:
            Repo.clone_from(repo_location, temp_dir)
        except GitCommandError as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError(
                "Cannot clone remote Git repository at "
                f"'{_redact_credentials(repo_location)}': "
                f"{_redact_credentials(str(exc))}"
            ) from exc
        return temp_dir

    raise ValueError(
        f"Cannot resolve repository location '{_redact_credentials(repo_location)}'. "
        "Provide a local Git repository path or a valid remote Git URL."
    )


def _resolve_commit(
    repo: Repo, branch: str = "main", commit: str | None = None
) -> git.Commit:
    """Resolve a branch/commit name to a git.Commit object."""
    if commit:
        try:
            return repo.commit(commit)
        except (git.BadName, git.BadObject) as exc:
            raise ValueError(f"Commit '{commit}' not found: {exc}") from exc

    try:
        return repo.commit(branch)
    except (git.BadName, git.BadObject):
        if branch == "main":
            try:
                return repo.commit("HEAD")
            except (git.BadName, git.BadObject) as exc:
                raise ValueError(
                    "Could not resolve branch 'main' or HEAD to a commit."
                ) from exc
        raise ValueError(f"Branch '{branch}' not found.") from None


def _render_yaml_value(value: Any) -> str:
    """Format a YAML value for display, handling nested structures."""
    if isinstance(value, (dict, list)):
        dumped = yaml.safe_dump(value, default_flow_style=False, sort_keys=False)
        return dumped.strip()
    return str(value)


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


def set_repository(repo_path: str, tool_context: ToolContext) -> str:
    """
    Switch to a *different* Git repository, given its remote Git URL.

    You normally do NOT need this: the DVC registry is configured via the
    REPO_URL environment variable and is cloned automatically the first time a
    repository tool is used. Only call this to point the agent at another
    repository whose URL you actually have (for example, a URL obtained from a
    `.dvc` file). Never pass a guessed local filesystem path.

    Args:
        repo_path: A remote Git URL (https://, git@, or ssh://). An existing
                   local repository path is also accepted, but never invent one.
        tool_context: Injected by ADK — used to persist the repo path in state.

    Returns:
        A confirmation string, or an error message if the repository is invalid.
    """
    try:
        resolved = _resolve_repository_location(repo_path)
        repo = Repo(resolved, search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError, ValueError) as exc:
        return f"ERROR: '{repo_path}' is not a valid Git repository — {exc}"

    tool_context.state[_STATE_KEY] = resolved
    branch_count = len(list(repo.branches))  # type: ignore[attr-defined]
    commit_count = sum(1 for _ in repo.iter_commits("--all"))
    return (
        f"Repository configured successfully.\n"
        f"  Path:    {resolved}\n"
        f"  Branches: {branch_count}\n"
        f"  Total commits (all branches): {commit_count}\n"
    )


def find_meta_yaml_files(
    branch: str = "main",
    commit: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """
    Find all `*.meta.yaml` files in the selected branch/commit and extract their contents.

    This tool also locates paired `*.dvc` files with the same base name and merges
    their content with the metadata files.

    Args:
        branch:       Branch name to use when commit is not provided.
        commit:       Optional commit hash to inspect.
        tool_context: Injected by ADK.

    Returns:
        A structured report with file paths and YAML content for every matching file.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
    except ValueError as exc:
        return f"ERROR: {exc}"

    try:
        target_commit = _resolve_commit(repo, branch, commit)
    except ValueError as exc:
        return f"ERROR: {exc}"

    # Build a map of blob path -> tree item for quick lookup (helps find paired .dvc files)
    blobs_by_path: dict[str, Any] = {}
    for raw_item in target_commit.tree.traverse():
        item = cast(Any, raw_item)
        if getattr(item, "type", None) == "blob":
            blobs_by_path[item.path] = item

    matches: list[dict[str, Any]] = []
    for path, item in blobs_by_path.items():
        if not fnmatch.fnmatch(path, "*.meta.yaml"):
            continue

        try:
            raw_text = item.data_stream.read().decode("utf-8", errors="replace")
        except Exception:
            continue

        try:
            docs = list(yaml.safe_load_all(raw_text))
        except yaml.YAMLError as exc:
            matches.append(
                {
                    "file_path": path,
                    "error": str(exc),
                    "commit": target_commit.hexsha,
                }
            )
            continue

        match_entry: dict[str, Any] = {
            "file_path": path,
            "commit": target_commit.hexsha,
            "branch": branch,
            "documents": docs,
        }

        # Attempt to locate a paired .dvc file with the same base name
        # e.g. datasets/foo.meta.yaml -> datasets/foo.dvc
        base = re.sub(r"\.meta\.ya?ml$", "", path, flags=re.IGNORECASE)
        dvc_candidate = f"{base}.dvc"
        dvc_item = blobs_by_path.get(dvc_candidate)
        if dvc_item is not None:
            try:
                dvc_raw = dvc_item.data_stream.read().decode("utf-8", errors="replace")
                dvc_docs = list(yaml.safe_load_all(dvc_raw))
                match_entry["dvc_path"] = dvc_candidate
                match_entry["dvc_documents"] = dvc_docs
            except yaml.YAMLError as exc:
                match_entry["dvc_error"] = str(exc)
            except Exception:
                # non-fatal; ignore binary or unreadable .dvc files
                match_entry["dvc_error"] = "Unable to read .dvc file"

        # Build joined documents where possible: merge dict documents from meta with first dict dvc document
        joined: list[dict[str, Any]] = []
        dvc_docs = match_entry.get("dvc_documents") or []
        first_dvc = None
        for d in dvc_docs:
            if isinstance(d, dict):
                first_dvc = d
                break

        for doc in docs:
            if isinstance(doc, dict) and isinstance(first_dvc, dict):
                merged = dict(first_dvc)  # dvc keys as defaults
                merged.update(doc)  # meta.yaml overrides
                joined.append(merged)
            else:
                # if we can't merge, just include the original meta document
                if isinstance(doc, dict):
                    joined.append(doc)

        if joined:
            match_entry["joined_documents"] = joined

        matches.append(match_entry)

    if not matches:
        return (
            f"No '*.meta.yaml' files were found in branch '{branch}' "
            f"at commit '{commit or target_commit.hexsha[:8]}'."
        )

    lines: list[str] = [
        f"Found {len(matches)} '*.meta.yaml' file(s) in commit {target_commit.hexsha[:8]} "
        f"(branch={branch}, commit={commit or 'latest'}):",
        "=" * 70,
    ]

    for idx, match in enumerate(matches, 1):
        lines.append(f"\n[{idx}] File: {match['file_path']}")
        lines.append(f"     Commit: {match['commit'][:8]}")
        lines.append(f"     Branch: {match['branch']}")

        if "error" in match:
            lines.append(f"     ERROR: {match['error']}")
            continue

        # Show joined documents if available, otherwise show raw documents
        documents_to_show = match.get("joined_documents", match.get("documents", []))
        for doc_index, document in enumerate(documents_to_show, start=1):
            lines.append(f"     Document: {doc_index}")
            if isinstance(document, dict):
                for key, value in document.items():
                    lines.append(f"       • {key}: {_render_yaml_value(value)}")
            else:
                rendered = yaml.safe_dump(
                    document, default_flow_style=False, sort_keys=False
                ).strip()
                lines.append(f"       • {rendered}")
        lines.append("-" * 70)

    return "\n".join(lines)


def _is_top_level_yaml(path: str) -> bool:
    return (
        "/" not in path
        and (fnmatch.fnmatch(path, "*.yml") or fnmatch.fnmatch(path, "*.yaml"))
        and not fnmatch.fnmatch(path, "*.meta.yaml")
    )


def find_top_level_yaml_files(
    branch: str = "main",
    commit: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """
    Find all top-level `*.yml` and `*.yaml` files in the selected branch/commit.

    This tool is intended to discover repository-level YAML config files that
    may contain GCP project, location, bucket, and BigQuery dataset settings.

    Args:
        branch:       Branch name to use when commit is not provided.
        commit:       Optional commit hash to inspect.
        tool_context: Injected by ADK.

    Returns:
        A structured report with file paths and YAML content for every matching file.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
    except ValueError as exc:
        return f"ERROR: {exc}"

    try:
        target_commit = _resolve_commit(repo, branch, commit)
    except ValueError as exc:
        return f"ERROR: {exc}"

    matches: list[dict[str, Any]] = []
    for raw_item in target_commit.tree.traverse():
        item = cast(Any, raw_item)
        if getattr(item, "type", None) != "blob":
            continue
        if not _is_top_level_yaml(item.path):
            continue

        try:
            raw_text = item.data_stream.read().decode("utf-8", errors="replace")
        except Exception:
            continue

        try:
            docs = list(yaml.safe_load_all(raw_text))
        except yaml.YAMLError as exc:
            matches.append(
                {
                    "file_path": item.path,
                    "error": str(exc),
                    "commit": target_commit.hexsha,
                }
            )
            continue

        match_entry: dict[str, Any] = {
            "file_path": item.path,
            "commit": target_commit.hexsha,
            "branch": branch,
            "documents": docs,
        }
        matches.append(match_entry)

    if not matches:
        repo_path = repo.working_dir or repo.git_dir
        return (
            f"No top-level '*.yml' or '*.yaml' files were found in branch '{branch}' "
            f"at commit '{commit or target_commit.hexsha[:8]}' "
            f"in the repository at '{repo_path}'.\n\n"
            "Please check the following:\n"
            "1. The file exists in the root of the project.\n"
            "2. The file is committed to the repository.\n"
            "3. You are looking at the correct branch.\n"
            "4. The agent is configured with the correct repository URL (via REPO_URL environment variable or the `set_repository` tool)."
        )

    lines: list[str] = [
        f"Found {len(matches)} top-level '*.yml'/*.yaml file(s) in commit {target_commit.hexsha[:8]} "
        f"(branch={branch}, commit={commit or 'latest'}):",
        "=" * 70,
    ]

    for idx, match in enumerate(matches, 1):
        lines.append(f"\n[{idx}] File: {match['file_path']}")
        lines.append(f"     Commit: {match['commit'][:8]}")
        lines.append(f"     Branch: {match['branch']}")

        if "error" in match:
            lines.append(f"     ERROR: {match['error']}")
            continue

        documents = match.get("documents", [])
        for doc_index, document in enumerate(documents, start=1):
            lines.append(f"     Document: {doc_index}")
            if isinstance(document, dict):
                for key, value in document.items():
                    lines.append(f"       • {key}: {_render_yaml_value(value)}")
            else:
                rendered = yaml.safe_dump(
                    document, default_flow_style=False, sort_keys=False
                ).strip()
                lines.append(f"       • {rendered}")
        lines.append("-" * 70)

    return "\n".join(lines)


def list_projects(
    branch: str = "main",
    commit: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """
    List projects by finding `*.meta.yaml` files.

    For each project, this tool extracts the project title or description from
    the `*.meta.yaml` file and returns a user-friendly summary. A project is
    identified by the presence of a `.meta.yaml` file; a corresponding `.dvc`
    file is not required.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."

    try:
        repo = _get_repo(tool_context)
    except ValueError as exc:
        return f"ERROR: {exc}"

    try:
        target_commit = _resolve_commit(repo, branch, commit)
    except ValueError as exc:
        return f"ERROR: {exc}"

    blobs_by_path: dict[str, Any] = {}
    for raw_item in target_commit.tree.traverse():
        item = cast(Any, raw_item)
        if getattr(item, "type", None) == "blob":
            blobs_by_path[item.path] = item

    projects: list[dict[str, Any]] = []
    for path in sorted(blobs_by_path):
        if not fnmatch.fnmatch(path, "*.meta.yaml"):
            continue

        base = re.sub(r"\.meta\.ya?ml$", "", path, flags=re.IGNORECASE)
        project_dir = os.path.dirname(path)

        summary = "No description available."
        repo_url = None
        dvc_remote_url = None
        try:
            meta_blob = blobs_by_path[path]
            meta_content = meta_blob.data_stream.read().decode("utf-8")
            meta_data = yaml.safe_load(meta_content)
            if isinstance(meta_data, dict):
                title = meta_data.get("title")
                description = meta_data.get("description")

                # The registry schema stores provenance under `source:`.
                source = meta_data.get("source")
                if isinstance(source, dict):
                    repo_url = source.get("gitlab-or-github-url") or source.get("url")
                    dvc_remote_url = source.get("dvc-remote-url")

                # Legacy / alternative top-level fields.
                repo_url = repo_url or (
                    meta_data.get("repository")
                    or meta_data.get("repo")
                    or meta_data.get("url")
                )

                if title and description:
                    summary = f"{title}: {description}"
                elif title:
                    summary = title
                elif description:
                    summary = description
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            logger.warning("Could not parse %s: %s", path, exc)

        # If repo_url is not in .meta.yaml, inspect the `.dvc` file(s) sitting in
        # the same directory (their base name need not match the meta file —
        # e.g. `beach-project.meta.yaml` is paired with `data-location.dvc`).
        if not repo_url:
            for dvc_path in sorted(blobs_by_path):
                if os.path.dirname(dvc_path) != project_dir or not dvc_path.endswith(
                    ".dvc"
                ):
                    continue
                try:
                    dvc_content = (
                        blobs_by_path[dvc_path].data_stream.read().decode("utf-8")
                    )
                    dvc_data = yaml.safe_load(dvc_content)
                except (yaml.YAMLError, UnicodeDecodeError) as exc:
                    logger.warning("Could not parse %s: %s", dvc_path, exc)
                    continue
                if not isinstance(dvc_data, dict):
                    continue
                for dep in dvc_data.get("deps") or []:
                    if isinstance(dep, dict) and isinstance(dep.get("repo"), dict):
                        repo_url = dep["repo"].get("url")
                        if repo_url:
                            break
                if repo_url:
                    break

        project_info = {
            "project": base,
            "summary": summary,
            "commit": target_commit.hexsha[:8],
        }
        if repo_url:
            project_info["repository"] = repo_url
        if dvc_remote_url:
            project_info["dvc_remote"] = dvc_remote_url
        projects.append(project_info)

    if not projects:
        return (
            f"No projects were found in branch '{branch}' "
            f"at commit '{commit or target_commit.hexsha[:8]}'."
        )

    lines = [
        f"Found {len(projects)} project(s) in commit {target_commit.hexsha[:8]} "
        f"(branch={branch}, commit={commit or 'latest'}):"
    ]
    for project in projects:
        lines.append(f"\nProject: {project['project']}")
        lines.append(f"  Summary: {project['summary']}")
        if "repository" in project:
            lines.append(f"  Repository: {project['repository']}")
        if "dvc_remote" in project:
            lines.append(f"  DVC remote: {project['dvc_remote']}")
    return "\n".join(lines)


def find_dvc_files(
    branch: str = "main",
    commit: str | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """
    Find all `*.dvc` files in the selected branch/commit and extract their contents.

    Args:
        branch:       Branch name to use when commit is not provided.
        commit:       Optional commit hash to inspect.
        tool_context: Injected by ADK.

    Returns:
        A structured report with file paths and YAML content for every matching file.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
    except ValueError as exc:
        return f"ERROR: {exc}"

    try:
        target_commit = _resolve_commit(repo, branch, commit)
    except ValueError as exc:
        return f"ERROR: {exc}"

    matches: list[dict[str, Any]] = []
    for raw_item in target_commit.tree.traverse():
        item = cast(Any, raw_item)
        if getattr(item, "type", None) != "blob":
            continue
        if not fnmatch.fnmatch(item.path, "*.dvc"):
            continue

        try:
            raw_text = item.data_stream.read().decode("utf-8", errors="replace")
        except Exception:
            continue

        try:
            docs = list(yaml.safe_load_all(raw_text))
        except yaml.YAMLError as exc:
            matches.append(
                {
                    "file_path": item.path,
                    "error": str(exc),
                    "commit": target_commit.hexsha,
                }
            )
            continue

        match_entry: dict[str, Any] = {
            "file_path": item.path,
            "commit": target_commit.hexsha,
            "branch": branch,
            "documents": docs,
        }
        matches.append(match_entry)

    if not matches:
        return (
            f"No '*.dvc' files were found in branch '{branch}' "
            f"at commit '{commit or target_commit.hexsha[:8]}'."
        )

    lines: list[str] = [
        f"Found {len(matches)} '*.dvc' file(s) in commit {target_commit.hexsha[:8]} "
        f"(branch={branch}, commit={commit or 'latest'}):",
        "=" * 70,
    ]

    for idx, match in enumerate(matches, 1):
        lines.append(f"\n[{idx}] File: {match['file_path']}")
        lines.append(f"     Commit: {match['commit'][:8]}")
        lines.append(f"     Branch: {match['branch']}")

        if "error" in match:
            lines.append(f"     ERROR: {match['error']}")
            continue

        documents = match.get("documents", [])
        for doc_index, document in enumerate(documents, start=1):
            lines.append(f"     Document: {doc_index}")
            if isinstance(document, dict):
                for key, value in document.items():
                    lines.append(f"       • {key}: {_render_yaml_value(value)}")
            else:
                rendered = yaml.safe_dump(
                    document, default_flow_style=False, sort_keys=False
                ).strip()
                lines.append(f"       • {rendered}")
        lines.append("-" * 70)

    return "\n".join(lines)


def clone_remote_repository(
    repo_url: str,
    tool_context: ToolContext,
) -> str:
    """
    Clone a remote Git repository from a URL into a temporary directory.

    This tool is useful for on-demand cloning of repositories referenced in
    a session. The cloned repository's path is persisted in session state,
    making it the active repository for subsequent tool calls.
    """
    try:
        resolved_path = _resolve_repository_location(repo_url)
        tool_context.state[_STATE_KEY] = resolved_path
        return f"Successfully cloned '{repo_url}' to '{resolved_path}'."
    except ValueError as exc:
        return f"ERROR: Could not clone repository — {exc}"


def get_dvc_md5(
    file_path: str,
    tool_context: ToolContext | None = None,
) -> str:
    """
    Extract the MD5 hash from the 'outs' section of a .dvc file.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
    except ValueError as exc:
        return f"ERROR: {exc}"

    try:
        blob = repo.head.commit.tree / file_path
        content = blob.data_stream.read().decode("utf-8")
        data = yaml.safe_load(content)
        if "outs" in data and isinstance(data["outs"], list) and len(data["outs"]) > 0:
            if "md5" in data["outs"][0]:
                return data["outs"][0]["md5"]
        return "ERROR: Could not find MD5 hash in the 'outs' section of the .dvc file."
    except (KeyError, IndexError, yaml.YAMLError, UnicodeDecodeError) as e:
        return f"ERROR: Could not parse .dvc file: {e}"
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def find_commit_by_hash_string(
    hash_string: str,
    tool_context: ToolContext | None = None,
) -> str:
    """
    Find a git commit by a string in its history using 'git log -S'.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
        # `-S` (pickaxe) is a `git log` option; iter_commits shells out to
        # `git rev-list`, which does not accept it. Use git log directly.
        output = repo.git.log("--all", "-S", hash_string, "--format=%H %s").strip()
        if output:
            return output
        return "No commits found with the given hash string."
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def checkout_commit(
    commit_hash: str,
    tool_context: ToolContext | None = None,
) -> str:
    """
    Check out a specific git commit by its hash.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
        repo.git.checkout(commit_hash)
        return f"Successfully checked out commit {commit_hash}."
    except GitCommandError as e:
        return f"ERROR: Could not checkout commit: {e}"
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def list_files(tool_context: ToolContext | None = None) -> str:
    """
    List all files in the current checkout of the repository.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
        return "\n".join(repo.git.ls_files().splitlines())
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def get_repo_url_from_dvc_file(
    file_path: str, tool_context: ToolContext | None = None
) -> str:
    """
    Read a `.dvc` file and extract the source repository URL from it.

    DVC import files (created by `dvc import`) record the source repository
    under ``deps[].repo.url``. This tool returns that URL so the caller can
    switch to the project's own repository for code analysis.

    Args:
        file_path: Path to the `.dvc` file, relative to the registry root
                   (e.g. ``datasets/beach-project/data-location.dvc``).
        tool_context: Injected by ADK.

    Returns:
        The repository URL, or an error message if none is found.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo = _get_repo(tool_context)
    except ValueError as exc:
        return f"ERROR: {exc}"

    working_dir = repo.working_dir
    if not working_dir:
        return "ERROR: The registry has no working tree to read files from."

    full_path = os.path.join(working_dir, file_path)
    if not os.path.exists(full_path):
        return f"ERROR: File not found at '{file_path}' in the registry."

    try:
        with open(full_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return f"ERROR: Could not parse '{file_path}': {exc}"

    if not isinstance(data, dict):
        return f"ERROR: '{file_path}' did not parse to a YAML mapping."

    # DVC import: deps[].repo.url is the source repository.
    for dep in data.get("deps") or []:
        if isinstance(dep, dict) and isinstance(dep.get("repo"), dict):
            url = dep["repo"].get("url")
            if url:
                return url

    # Legacy fallback: an explicit meta.repo_url field.
    meta = data.get("meta")
    if isinstance(meta, dict) and meta.get("repo_url"):
        return meta["repo_url"]

    return (
        f"ERROR: No repository URL found in '{file_path}'. Looked under "
        "deps[].repo.url (DVC import) and meta.repo_url. This .dvc file may "
        "track a local output rather than importing from another repository."
    )
