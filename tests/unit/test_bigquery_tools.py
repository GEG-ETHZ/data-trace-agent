"""query_bigquery: cost cap and failure visibility in the logs."""

from agent.tools import bigquery_tools
from agent.tools.bigquery_tools import query_bigquery


def test_query_bigquery_caps_bytes_billed(mocker):
    client_cls = mocker.patch("agent.tools.bigquery_tools.bigquery.Client")
    client = client_cls.return_value
    client.query.return_value.result.return_value = [{"n": 1}]

    result = query_bigquery(query="SELECT 1", project_id="p", location="europe-west6")

    job_config = client.query.call_args.kwargs["job_config"]
    assert job_config.maximum_bytes_billed == bigquery_tools._MAX_BYTES_BILLED
    assert result.results == [{"n": 1}]
    assert result.error is None


def test_query_bigquery_logs_error_event_when_query_fails(mocker):
    mocker.patch(
        "agent.tools.bigquery_tools.bigquery.Client",
        side_effect=Exception("Query exceeded limit for bytes billed"),
    )
    log_event = mocker.patch("agent.tools.bigquery_tools.log_event")

    result = query_bigquery(query="SELECT 1", project_id="p", location="europe-west6")

    assert result.error == "Query exceeded limit for bytes billed"
    log_event.assert_called_once_with(
        "query_bigquery.query_failed",
        {
            "error": "Query exceeded limit for bytes billed",
            "project_id": "p",
            "location": "europe-west6",
        },
        severity="ERROR",
    )


def test_query_bigquery_does_not_log_error_event_on_success(mocker):
    client_cls = mocker.patch("agent.tools.bigquery_tools.bigquery.Client")
    client_cls.return_value.query.return_value.result.return_value = []
    log_event = mocker.patch("agent.tools.bigquery_tools.log_event")

    query_bigquery(query="SELECT 1")

    log_event.assert_not_called()
