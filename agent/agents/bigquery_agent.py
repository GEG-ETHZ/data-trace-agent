"""
BigQuery sub-agent.
"""

from google.adk.agents import Agent

from agent import load_prompt
from agent.tools import find_top_level_yaml_files, query_bigquery
from deployment.config import resolve_model

bigquery_agent = Agent(
    name="bigquery_agent",
    model=resolve_model(),
    description=(
        "A focused sub-agent that queries BigQuery databases and provides the "
        "results for analysis."
    ),
    instruction=load_prompt("bigquery_agent"),
    tools=[
        query_bigquery,
        find_top_level_yaml_files,
    ],
)
