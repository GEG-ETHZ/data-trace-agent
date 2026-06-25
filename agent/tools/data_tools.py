"""
Tools for data analysis, including DVC and Parquet file operations.
"""

import io
import os
import subprocess
import sys

import pandas as pd
import yaml
from google.adk.tools import ToolContext


def _apply_dvc_local_config(repo_path: str) -> None:
    """Write DVC workspace (local) config from the ``DVC_CONFIG_LOCAL`` env var.

    Lets credentials for config-based remotes (e.g. WebDAV, SSH) be supplied at
    runtime — for example on Vertex AI Agent Engine — without committing them.
    The contents are written verbatim to ``<repo>/.dvc/config.local``, which DVC
    layers over the repo config and which is git-ignored. GCS remotes do not
    need this: the ``gs`` backend uses Application Default Credentials (the
    runtime service account).
    """
    config_local = os.getenv("DVC_CONFIG_LOCAL")
    if not config_local:
        return
    dvc_dir = os.path.join(repo_path, ".dvc")
    if not os.path.isdir(dvc_dir):
        return
    with open(os.path.join(dvc_dir, "config.local"), "w") as f:
        f.write(config_local)


def _resolve_pull_targets(repo_path: str, file_path: str) -> list[str]:
    """Resolve a user-supplied path to concrete `dvc pull` targets.

    `dvc pull` expects a `.dvc` file, a stage name, or a tracked output path —
    not an arbitrary directory. This maps the common inputs an agent supplies:

    - a directory  -> every `*.dvc` file found beneath it
    - a `.dvc` file -> itself
    - a data path  -> its sibling `<path>.dvc` if that exists, else the path
    """
    if os.path.isabs(file_path):
        file_path = os.path.relpath(file_path, repo_path)
    full = os.path.join(repo_path, file_path)

    if os.path.isdir(full):
        targets: list[str] = []
        for root, _dirs, files in os.walk(full):
            for name in files:
                if name.endswith(".dvc"):
                    abs_dvc = os.path.join(root, name)
                    targets.append(os.path.relpath(abs_dvc, repo_path))
        return sorted(targets)

    if file_path.endswith(".dvc"):
        return [file_path]

    # A data/output path: prefer the sibling `.dvc` file that tracks it.
    if os.path.exists(full + ".dvc"):
        return [file_path + ".dvc"]
    return [file_path]


def dvc_pull(
    file_path: str | None = None, tool_context: ToolContext | None = None
) -> str:
    """
    Run 'dvc pull' to download DVC-tracked data from the configured remote.

    Args:
        file_path: Optional target. May be a project directory (all `.dvc`
                   files beneath it are pulled), a specific `.dvc` file, or a
                   tracked data path. Omit to pull the entire repository.
        tool_context: Injected by ADK.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        _apply_dvc_local_config(repo_path)

        command = [sys.executable, "-m", "dvc", "pull"]
        if file_path:
            targets = _resolve_pull_targets(repo_path, file_path)
            if not targets:
                return (
                    f"ERROR: No DVC-tracked files (`*.dvc`) found under "
                    f"'{file_path}'. Nothing to pull."
                )
            command.extend(targets)

        result = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except FileNotFoundError:
        return (
            "ERROR: 'dvc' command not found. Please ensure dvc is installed in the"
            " current python environment."
        )
    except subprocess.CalledProcessError as e:
        return f"ERROR: dvc pull failed: {e.stderr}"
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def inspect_parquet_file(
    file_path: str, tool_context: ToolContext | None = None
) -> str:
    """
    Inspect a Parquet file and return its schema and a sample of the data.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        full_path = os.path.join(repo_path, file_path)
        if not os.path.exists(full_path):
            return f"ERROR: File not found at {full_path}. Did you run dvc pull?"

        df = pd.read_parquet(full_path)

        buffer = io.StringIO()
        df.info(buf=buffer)
        schema = buffer.getvalue()

        sample = df.head().to_string()
        return f"Schema:\n{schema}\n\nSample Data:\n{sample}"
    except Exception as e:
        return f"ERROR: Could not inspect Parquet file: {e}"


def analyze_parquet_file(
    file_path: str, tool_context: ToolContext | None = None
) -> str:
    """
    Perform a basic data analysis on a Parquet file.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        full_path = os.path.join(repo_path, file_path)
        if not os.path.exists(full_path):
            return f"ERROR: File not found at {full_path}. Did you run dvc pull?"

        df = pd.read_parquet(full_path)
        description = df.describe(include="all").to_string()

        # Basic error checking
        errors = []
        if df.isnull().values.any():
            errors.append("Missing values found.")

        # Add more checks as needed, e.g., for duplicates, outliers, etc.

        error_summary = "\n".join(errors) if errors else "No obvious errors found."

        return f"Data Description:\n{description}\n\nError Summary:\n{error_summary}"
    except Exception as e:
        return f"ERROR: Could not analyze Parquet file: {e}"


def dvc_list_files(tool_context: ToolContext | None = None) -> str:
    """
    List all files tracked by DVC in the repository.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        result = subprocess.run(
            [sys.executable, "-m", "dvc", "list", ".", "--dvc-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except FileNotFoundError:
        return (
            "ERROR: 'dvc' command not found. Please ensure dvc is installed in the"
            " current python environment."
        )
    except subprocess.CalledProcessError as e:
        return f"ERROR: dvc list failed: {e.stderr}"
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def inspect_yaml_file(file_path: str, tool_context: ToolContext | None = None) -> str:
    """
    Inspect a YAML file and return its structure and a sample of the data.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        full_path = os.path.join(repo_path, file_path)
        if not os.path.exists(full_path):
            return f"ERROR: File not found at {full_path}. Did you run dvc pull?"

        with open(full_path) as f:
            data = yaml.safe_load(f)

        # Basic structure by showing keys at the first level
        structure = f"Top-level keys: {list(data.keys())}"

        # Sample data (first 5 key-value pairs)
        sample = dict(list(data.items())[:5])

        return f"Structure:\n{structure}\n\nSample Data:\n{sample}"
    except yaml.constructor.ConstructorError:
        with open(full_path) as f:
            content = f.read()
        return (
            "Could not fully parse YAML file due to custom objects. "
            f"Raw content:\n{content}"
        )
    except Exception as e:
        return f"ERROR: Could not inspect YAML file: {e}"


def analyze_yaml_file(file_path: str, tool_context: ToolContext | None = None) -> str:
    """
    Perform a basic data analysis on a YAML file.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        full_path = os.path.join(repo_path, file_path)
        if not os.path.exists(full_path):
            return f"ERROR: File not found at {full_path}. Did you run dvc pull?"

        with open(full_path) as f:
            data = yaml.safe_load(f)

        description = f"The YAML file has {len(data)} top-level keys."

        # Basic error checking
        errors = []
        if not data:
            errors.append("YAML file is empty.")

        error_summary = "\n".join(errors) if errors else "No obvious errors found."

        return f"Data Description:\n{description}\n\nError Summary:\n{error_summary}"
    except yaml.constructor.ConstructorError:
        with open(full_path) as f:
            content = f.read()
        return (
            "Could not fully parse YAML file due to custom objects. "
            f"Raw content:\n{content}"
        )
    except Exception as e:
        return f"ERROR: Could not analyze YAML file: {e}"


def list_files_in_directory(
    directory_path: str, tool_context: ToolContext | None = None
) -> str:
    """
    List all files and directories in a given directory.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."
    try:
        repo_path = tool_context.state.get("repo_path")
        if not repo_path:
            return "ERROR: Repository path not set. Please use set_repository first."

        full_path = os.path.join(repo_path, directory_path)
        if not os.path.isdir(full_path):
            return f"ERROR: Directory not found at {full_path}."

        files = os.listdir(full_path)
        return "\n".join(files)
    except Exception as e:
        return f"ERROR: An unexpected error occurred: {e}"


def dvc_remote_list(tool_context: ToolContext | None = None) -> str:
    """
    List configured DVC remotes to find data storage locations.

    Reads the DVC configuration (e.g., from `.dvc/config`) and lists the
    names and URLs of the configured remotes (like GCS, S3, etc.).

    Args:
        tool_context: Injected by ADK.

    Returns:
        A string listing the DVC remotes, or an error message.
    """
    if tool_context is None:
        return "ERROR: tool_context is required."

    repo_path = tool_context.state.get("repo_path")
    if not repo_path:
        return "ERROR: Repository path not set. Please use set_repository first."

    try:
        # The DVC API is complex for just listing remotes; shell out for simplicity.
        # No `--local`: the registry's remote is defined in `.dvc/config` (repo
        # config), which `--local` would exclude. Invoke via `-m dvc` so it
        # resolves to the same interpreter as the other tools (no PATH reliance).
        result = subprocess.run(
            [sys.executable, "-m", "dvc", "remote", "list"],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        if not output:
            return "No DVC remotes configured in this repository."
        return f"DVC remotes:\n{output}"
    except FileNotFoundError:
        return "ERROR: `dvc` command not found. Is DVC installed?"
    except subprocess.CalledProcessError as exc:
        if "is not a DVC repository" in exc.stderr:
            return "This is not a DVC repository. Initialize with `dvc init` first."
        return f"ERROR: `dvc remote list` failed:\n{exc.stderr}"
