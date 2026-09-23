import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _project_name() -> str:
    """Read the project name from pyproject.toml so derived names stay in sync."""
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        return tomllib.load(f)["project"]["name"]


def resolve_model():
    """Return the ADK-compatible model handle based on MODEL_PROVIDER env var.

    Supported values for MODEL_PROVIDER:
      google    (default) — Gemini 2.5 Pro via native ADK
      anthropic           — Claude via LiteLLM
      openai              — GPT-4o via LiteLLM
      litellm             — any model; set LITELLM_MODEL to the full model string
    """
    provider = os.getenv("MODEL_PROVIDER", "google").lower()

    if provider == "google":
        return "gemini-2.5-pro"

    from google.adk.models.lite_llm import LiteLlm  # noqa: PLC0415

    match provider:
        case "anthropic":
            return LiteLlm(model="anthropic/claude-opus-4-8")
        case "openai":
            return LiteLlm(model="openai/gpt-4o")
        case "litellm":
            model = os.environ["LITELLM_MODEL"]
            return LiteLlm(model=model)
        case _:
            raise ValueError(
                f"Unknown MODEL_PROVIDER: {provider!r}. "
                "Valid options: google, anthropic, openai, litellm"
            )


# Environment variables forwarded verbatim to the deployed Agent Engine runtime
# (plain values). Credentials/tokens are handled separately in runtime_env_vars().
_FORWARDED_ENV_VARS = (
    "REPO_URL",
    "GIT_AUTH_HOST",
    "GIT_AUTH_USERNAME",
    "MODEL_PROVIDER",
    "LITELLM_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


_SECRET_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _set_secret_or_plain(env: dict, key: str) -> None:
    """Populate ``env[key]`` from ``<KEY>_SECRET`` (Secret Manager ref, preferred)
    or a plain ``<KEY>`` value. Does nothing if neither is set."""
    secret = os.getenv(f"{key}_SECRET")
    if secret:
        # Vertex AI Agent Engine rejects secret names with hyphens at the API
        # level despite its error message claiming hyphens are allowed. Use
        # underscores when naming secrets (e.g. gitlab_deploy_token, not
        # gitlab-deploy-token).
        if not _SECRET_NAME_RE.match(secret):
            raise ValueError(
                f"{key}_SECRET={secret!r} contains characters not accepted by "
                "Vertex AI Agent Engine. Secret Manager secret names must use "
                "only alphanumeric characters and underscores (not hyphens)."
            )
        from google.cloud.aiplatform_v1.types import env_var  # noqa: PLC0415

        env[key] = env_var.SecretRef(
            secret=secret,
            version=os.getenv(f"{key}_SECRET_VERSION", "latest"),
        )
    elif os.getenv(key):
        env[key] = os.environ[key]


def runtime_env_vars(project: str) -> dict:
    """Build the env-var map passed to the deployed agent at create/update time.

    Forwards a fixed allowlist of variables from the deploy environment so the
    headless runtime can clone private repos and pull DVC data (no SSH key or
    DVC credentials there). Sensitive values — the Git auth token
    (``GIT_AUTH_TOKEN``) and DVC workspace config (``DVC_CONFIG_LOCAL``, used for
    config-based remotes such as WebDAV) — are supplied either as a Secret
    Manager reference (preferred, via ``<NAME>_SECRET``) or as a plain value.

    GCS DVC remotes need no value here: the ``gs`` backend authenticates via the
    runtime service account's Application Default Credentials (see
    ``DeploymentConfig.service_account``).

    ``project`` is the deploy target's project, not the ambient environment's.
    """
    # Logs need nothing here; traces need both of these and neither is optional.
    # observability.py builds no tracer without the flag, and CloudTraceSpanExporter()
    # otherwise resolves no project inside the Agent Engine container, failing every
    # export with "INVALID_ARGUMENT: Invalid project id in name!".
    #
    # GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY and OTEL_SEMCONV_STABILITY_OPT_IN are
    # separate from the pair above: they are what the Agent Engine console itself reads
    # to unlock its own dashboard/observability view for agents deployed via the API
    # rather than the console UI. Deliberately not setting
    # OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT here — it would have the console
    # log full prompt/response content, which is a data-classification decision, not a
    # deploy default.
    env: dict = {
        "CLOUD_TRACE_ENABLED": "true",
        "OTEL_EXPORTER_GCP_TRACE_PROJECT_ID": project,
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
    }
    for name in _FORWARDED_ENV_VARS:
        value = os.getenv(name)
        if value:
            env[name] = value

    _set_secret_or_plain(env, "GIT_AUTH_TOKEN")
    _set_secret_or_plain(env, "DVC_CONFIG_LOCAL")

    return env


# The service account `setup_gcp.sh` creates, and the staging bucket it provisions.
# Both are fully determined by the project id, so neither has to be restated in .env
# -- keep these in step with SA_NAME and BUCKET in deployment/scripts/setup_gcp.sh.
RUNTIME_SA_NAME = "agent-engine-sa"
STAGING_BUCKET_SUFFIX = "-agent-staging"


def default_service_account(project: str) -> str:
    """The runtime identity `setup_gcp.sh` provisions for this project."""
    return f"{RUNTIME_SA_NAME}@{project}.iam.gserviceaccount.com"


def default_staging_bucket(project: str) -> str:
    """The staging bucket `setup_gcp.sh` provisions for this project."""
    return f"gs://{project}{STAGING_BUCKET_SUFFIX}"


def normalise_bucket(value: str) -> str:
    """Add the `gs://` scheme if the caller left it off.

    `setup_gcp.sh` prints the bucket as a bare name while the docs show it with the
    scheme, and Vertex wants a full `gs://` URI -- so accept either spelling rather
    than making the difference matter.
    """
    return value if value.startswith("gs://") else f"gs://{value}"


@dataclass
class DeploymentConfig:
    project: str
    location: str
    staging_bucket: str
    resource_name: str | None
    agent_display_name: str
    gcs_dir_name: str
    service_account: str

    @classmethod
    def from_env(cls) -> "DeploymentConfig":
        """Build the config from the environment.

        `GOOGLE_CLOUD_PROJECT` is the only required variable. The staging bucket and
        the runtime service account are derived from it, matching what
        `setup_gcp.sh` provisions, and the corresponding environment variables exist
        only to override that for a renamed bucket or SA.
        """
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        staging_bucket = os.getenv("GCS_STAGING_BUCKET")
        service_account = os.getenv("AGENT_ENGINE_SERVICE_ACCOUNT")
        return cls(
            project=project,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1"),
            staging_bucket=(
                normalise_bucket(staging_bucket)
                if staging_bucket
                else default_staging_bucket(project)
            ),
            resource_name=os.getenv("AGENT_ENGINE_RESOURCE_NAME") or None,
            agent_display_name="Data Trace Agent",
            # Staging subfolder within the bucket; project-named so artifacts
            # land at <bucket>/data-trace-agent/ instead of the generic default.
            gcs_dir_name=_project_name(),
            # Always set: omitting it silently falls back to the project's shared
            # Reasoning Engine Service Agent. Its Application Default Credentials
            # also authenticate DVC's GCS remote pulls, so it needs read access to
            # the DVC remote bucket(s).
            service_account=service_account or default_service_account(project),
        )
