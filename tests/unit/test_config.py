"""Unit tests for deployment configuration helpers."""

from deployment.config import DeploymentConfig, runtime_env_vars


def test_runtime_env_vars_enables_tracing_on_the_deploy_target():
    """Both are required: the flag builds the tracer, and the exporter resolves no
    project inside the Agent Engine container without an explicit one."""
    env = runtime_env_vars("my-project")

    assert env["CLOUD_TRACE_ENABLED"] == "true"
    assert env["OTEL_EXPORTER_GCP_TRACE_PROJECT_ID"] == "my-project"


def test_runtime_env_vars_traces_to_the_argument_not_the_ambient_project(monkeypatch):
    """The deploy target decides, so a stale GOOGLE_CLOUD_PROJECT cannot send this
    agent's spans to the wrong project."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-other-project")

    env = runtime_env_vars("my-project")

    assert env["OTEL_EXPORTER_GCP_TRACE_PROJECT_ID"] == "my-project"


def test_runtime_env_vars_enables_the_agent_engine_console_dashboard():
    """Without these, the console shows the reduced dashboard for agents deployed via
    the API instead of the console UI. Message-content capture is deliberately not
    part of this — that is a data-classification decision, not a deploy default."""
    env = runtime_env_vars("my-project")

    assert env["GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"] == "true"
    assert env["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_latest_experimental"
    assert "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT" not in env


def test_runtime_env_vars_forwards_allowlist(monkeypatch):
    monkeypatch.setenv("REPO_URL", "https://gitlab.example.com/g/r.git")
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.delenv("GIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GIT_AUTH_TOKEN_SECRET", raising=False)

    env = runtime_env_vars("my-project")

    assert env["REPO_URL"] == "https://gitlab.example.com/g/r.git"
    assert env["MODEL_PROVIDER"] == "anthropic"
    assert "GIT_AUTH_TOKEN" not in env


def test_runtime_env_vars_plain_token(monkeypatch):
    monkeypatch.setenv("GIT_AUTH_TOKEN", "tok")  # pragma: allowlist secret
    monkeypatch.delenv("GIT_AUTH_TOKEN_SECRET", raising=False)

    env = runtime_env_vars("my-project")

    assert env["GIT_AUTH_TOKEN"] == "tok"


def test_runtime_env_vars_secret_ref(monkeypatch):
    monkeypatch.setenv("GIT_AUTH_TOKEN_SECRET", "my_git_token")
    monkeypatch.delenv("GIT_AUTH_TOKEN_SECRET_VERSION", raising=False)

    env = runtime_env_vars("my-project")
    ref = env["GIT_AUTH_TOKEN"]

    # A Secret Manager reference, not a plain value. Secret names use
    # underscores — Agent Engine rejects hyphens (see config validation).
    assert ref.secret == "my_git_token"  # pragma: allowlist secret
    assert ref.version == "latest"


def test_runtime_env_vars_dvc_config_plain(monkeypatch):
    monkeypatch.setenv("DVC_CONFIG_LOCAL", "[core]\n    remote = webdav\n")
    monkeypatch.delenv("DVC_CONFIG_LOCAL_SECRET", raising=False)

    env = runtime_env_vars("my-project")

    assert "remote = webdav" in env["DVC_CONFIG_LOCAL"]


def test_runtime_env_vars_dvc_config_secret_ref(monkeypatch):
    monkeypatch.setenv("DVC_CONFIG_LOCAL_SECRET", "dvc_config")
    monkeypatch.setenv("DVC_CONFIG_LOCAL_SECRET_VERSION", "3")

    env = runtime_env_vars("my-project")
    ref = env["DVC_CONFIG_LOCAL"]

    assert ref.secret == "dvc_config"  # pragma: allowlist secret
    assert ref.version == "3"


def test_deployment_config_service_account(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GCS_STAGING_BUCKET", "gs://bucket")
    monkeypatch.setenv(
        "AGENT_ENGINE_SERVICE_ACCOUNT", "sa@proj.iam.gserviceaccount.com"
    )

    config = DeploymentConfig.from_env()

    assert config.service_account == "sa@proj.iam.gserviceaccount.com"


def test_from_env_derives_service_account_from_the_project(monkeypatch):
    """Omitting the SA must not fall back to the project's shared Reasoning Engine
    Service Agent; setup_gcp.sh's agent-engine-sa is fully determined by the project."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GCS_STAGING_BUCKET", "gs://bucket")
    monkeypatch.delenv("AGENT_ENGINE_SERVICE_ACCOUNT", raising=False)

    assert (
        DeploymentConfig.from_env().service_account
        == "agent-engine-sa@proj.iam.gserviceaccount.com"
    )


def test_from_env_falls_back_to_the_derived_service_account_when_empty(monkeypatch):
    """An unset GitHub Actions variable arrives as "", which must not blank the SA."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GCS_STAGING_BUCKET", "gs://bucket")
    monkeypatch.setenv("AGENT_ENGINE_SERVICE_ACCOUNT", "")

    assert (
        DeploymentConfig.from_env().service_account
        == "agent-engine-sa@proj.iam.gserviceaccount.com"
    )


def test_from_env_derives_staging_bucket_from_the_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.delenv("GCS_STAGING_BUCKET", raising=False)

    assert DeploymentConfig.from_env().staging_bucket == "gs://proj-agent-staging"


def test_from_env_accepts_a_bucket_without_the_gs_scheme(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GCS_STAGING_BUCKET", "my-own-bucket")

    assert DeploymentConfig.from_env().staging_bucket == "gs://my-own-bucket"


def test_from_env_leaves_an_explicit_gs_uri_alone(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GCS_STAGING_BUCKET", "gs://my-own-bucket")

    assert DeploymentConfig.from_env().staging_bucket == "gs://my-own-bucket"
