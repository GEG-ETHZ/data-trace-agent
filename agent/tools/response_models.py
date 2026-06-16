"""Pydantic response models for structured tool return types.

The current Git/DVC tools return human-readable strings (the ADK ReAct loop
synthesises the final answer), so no models are defined yet. Add a
``pydantic.BaseModel`` subclass here when a tool needs to return structured
data, and annotate the tool's return type with it.
"""

from pydantic import BaseModel, Field


class BQQueryResponse(BaseModel):
    """A Pydantic model for the response of the BigQuery query tool."""

    results: list[dict] | None = Field(
        default=None, description="The results of the BigQuery query."
    )
    error: str | None = Field(default=None, description="Any error that occurred.")
