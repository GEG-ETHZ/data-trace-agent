"""
Tools for interacting with BigQuery.
"""

from __future__ import annotations

from google.cloud import bigquery

from agent.observability import instrument, log_event
from agent.tools.response_models import BQQueryResponse

# BigQuery rejects a query whose bytes billed would exceed this, without charging
# for it. A guard against runaway model-written queries, not a quota.
_MAX_BYTES_BILLED = 10 * 1024**3


@instrument
def query_bigquery(
    query: str, project_id: str | None = None, location: str | None = None
) -> BQQueryResponse:
    """
    Execute a SQL query on BigQuery and return the results.

    Queries that would bill more than 10 GiB are rejected before they run. `LIMIT`
    does not reduce the bytes billed, so select only the columns you need.

    Args:
        query: The SQL query to execute.
        project_id: The GCP project ID.
        location: The location of the BigQuery dataset.

    Returns:
        A Pydantic model containing the query results as a list of dictionaries.
    """
    try:
        client = bigquery.Client(project=project_id, location=location)
        job_config = bigquery.QueryJobConfig(maximum_bytes_billed=_MAX_BYTES_BILLED)
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()
        # Convert to a list of dictionaries.
        records = [dict(row) for row in results]
        return BQQueryResponse(results=records)
    except Exception as e:
        # The error is returned to the model rather than raised, so @instrument
        # records this call as a success; log the failure explicitly.
        log_event(
            "query_bigquery.query_failed",
            {"error": str(e), "project_id": project_id, "location": location},
            severity="ERROR",
        )
        return BQQueryResponse(error=str(e))
