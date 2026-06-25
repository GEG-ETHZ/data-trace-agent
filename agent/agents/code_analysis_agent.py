"""
Code analysis sub-agent.

This agent is responsible for cloning repositories, finding specific commits
related to DVC tracked files, checking out the code, and providing analysis.
"""

from google.adk.agents import Agent

from agent import load_prompt
from agent.tools import (
    checkout_commit,
    clone_remote_repository,
    clone_repository_at_revision,
    find_commit_by_hash_string,
    get_dvc_import_info,
    get_dvc_md5,
    list_files,
    read_file_content,
)
from deployment.config import resolve_model

code_analysis_agent = Agent(
    name="code_analysis_agent",
    model=resolve_model(),
    description=(
        "A sub-agent that can clone repositories at specific revisions, find "
        "commits related to DVC file hashes, checkout the code, read README and "
        "documentation files, and perform code and reproducibility analysis."
    ),
    instruction=load_prompt("code_analysis_agent"),
    tools=[
        clone_remote_repository,
        clone_repository_at_revision,
        get_dvc_import_info,
        get_dvc_md5,
        find_commit_by_hash_string,
        checkout_commit,
        list_files,
        read_file_content,
    ],
)
