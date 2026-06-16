"""
Data Trace Agent — Google ADK root agent.

A guiding root agent for repository metadata work. It coordinates three
sub-agents following the ReAct (Reason → Act → Observe) pattern natively
supported by Google ADK:

  • metadata_agent       — extracts `*.meta.yaml` / `*.dvc` metadata.
  • code_analysis_agent  — clones repos, maps DVC hashes to commits, analyses code.
  • data_analysis_agent  — pulls DVC tracked data and inspects Parquet/YAML files.
  • bigquery_agent       — queries BigQuery databases.

The active repository is stored in ADK session state (key ``repo_path``) and
defaults to the DVC registry cloned from the ``REPO_URL`` environment variable
(a remote Git URL) when set.
"""

from google.adk.agents import Agent

from agent import load_prompt
from agent.agents import (
    bigquery_agent,
    code_analysis_agent,
    data_analysis_agent,
    metadata_agent,
)
from agent.tools import get_repo_url_from_dvc_file, set_repository
from deployment.config import resolve_model

root_agent = Agent(
    name="root_agent",
    model=resolve_model(),
    description=(
        "A guiding root agent for repository metadata work. "
        "It helps the user and delegates the actual metadata extraction to the sub-agents."
    ),
    instruction=load_prompt("root_agent"),
    tools=[
        set_repository,
        get_repo_url_from_dvc_file,
    ],
    sub_agents=[
        metadata_agent,
        code_analysis_agent,
        data_analysis_agent,
        bigquery_agent,
    ],
)
