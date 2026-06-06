from google.adk.agents import Agent

from agent import load_prompt
from agent.tools import get_current_datetime, web_search
from deployment.config import resolve_model

root_agent = Agent(
    name="root_agent",
    model=resolve_model(),
    instruction=load_prompt("root_agent"),
    tools=[get_current_datetime, web_search],
)
