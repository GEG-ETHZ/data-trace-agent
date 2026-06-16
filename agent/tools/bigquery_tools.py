"""
Tools for interacting with BigQuery.
"""

from __future__ import annotations

from google.cloud import bigquery

from agent.tools.response_models import BQQueryResponse


def query_bigquery(
    query: str, project_id: str | None = None, location: str | None = None
) -> BQQueryResponse:
    """
    Execute a SQL query on BigQuery and return the results.

    Args:
        query: The SQL query to execute.
        project_id: The GCP project ID.
        location: The location of the BigQuery dataset.

    Returns:
        A Pydantic model containing the query results as a list of dictionaries.
    """
    try:
        client = bigquery.Client(project=project_id, location=location)
        query_job = client.query(query)
        results = query_job.result()
        # Convert to a list of dictionaries.
        records = [dict(row) for row in results]
        return BQQueryResponse(results=records)
    except Exception as e:
        return BQQueryResponse(error=str(e))
