"""
Data analysis sub-agent.

This agent is responsible for pulling DVC tracked data, inspecting Parquet
and YAML files, and performing basic data analysis.
"""

from google.adk.agents import Agent

from agent import load_prompt
from agent.tools import (
    analyze_parquet_file,
    analyze_yaml_file,
    clone_remote_repository,
    dvc_list_files,
    dvc_pull,
    inspect_parquet_file,
    inspect_yaml_file,
    list_files_in_directory,
    set_repository,
    switch_to_registry,
)
from deployment.config import resolve_model

data_analysis_agent = Agent(
    name="data_analysis_agent",
    model=resolve_model(),
    description=(
        "A sub-agent that can pull DVC tracked data, inspect Parquet files, "
        "and perform basic data analysis."
    ),
    instruction=load_prompt("data_analysis_agent"),
    tools=[
        switch_to_registry,
        set_repository,
        clone_remote_repository,
        dvc_pull,
        dvc_list_files,
        inspect_parquet_file,
        analyze_parquet_file,
        analyze_yaml_file,
        inspect_yaml_file,
        list_files_in_directory,
    ],
)
