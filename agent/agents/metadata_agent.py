"""
Metadata sub-agent.

This agent extracts `*.meta.yaml` and `*.dvc` metadata from the configured
repository and returns structured key/value output.

Available tools (see agent/tools/git_tools.py for full docstrings):
  • set_repository           — Configure the Git repository to work with.
  • list_projects            — List projects by finding `*.meta.yaml` files.
  • find_meta_yaml_files     — Extract all `*.meta.yaml` files from a branch/commit.
  • find_dvc_files           — Extract all `*.dvc` files from a branch/commit.
  • clone_remote_repository  — Clone a remote repository by URL (on-demand).
"""

from google.adk.agents import Agent

from agent import load_prompt
from agent.tools import (
    clone_remote_repository,
    find_dvc_files,
    find_meta_yaml_files,
    list_projects,
    set_repository,
)
from deployment.config import resolve_model

metadata_agent = Agent(
    name="metadata_agent",
    model=resolve_model(),
    description=(
        "A focused sub-agent that extracts `*.meta.yaml` and `*.dvc` metadata "
        "from the configured repository and returns structured key/value output."
    ),
    instruction=load_prompt("metadata_agent"),
    tools=[
        set_repository,
        list_projects,
        find_meta_yaml_files,
        find_dvc_files,
        clone_remote_repository,
    ],
)
