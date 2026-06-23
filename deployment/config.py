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


def runtime_env_vars() -> dict:
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
    """
    env: dict = {}
    for name in _FORWARDED_ENV_VARS:
        value = os.getenv(name)
        if value:
            env[name] = value

    _set_secret_or_plain(env, "GIT_AUTH_TOKEN")
    _set_secret_or_plain(env, "DVC_CONFIG_LOCAL")

    return env


@dataclass
class DeploymentConfig:
    project: str
    location: str
    staging_bucket: str
    resource_name: str | None
    agent_display_name: str
    gcs_dir_name: str
    service_account: str | None

    @classmethod
    def from_env(cls) -> "DeploymentConfig":
        return cls(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1"),
            staging_bucket=os.environ["GCS_STAGING_BUCKET"],
            resource_name=os.getenv("AGENT_ENGINE_RESOURCE_NAME") or None,
            agent_display_name="Data Trace Agent",
            # Staging subfolder within the bucket; project-named so artifacts
            # land at <bucket>/data-trace-agent/ instead of the generic default.
            gcs_dir_name=_project_name(),
            # Service account the deployed agent runs as. Its Application Default
            # Credentials authenticate DVC's GCS remote pulls, so it needs read
            # access to the DVC remote bucket(s). Omit to use the Agent Engine
            # default service account.
            service_account=os.getenv("AGENT_ENGINE_SERVICE_ACCOUNT") or None,
        )
