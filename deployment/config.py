import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


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


@dataclass
class DeploymentConfig:
    project: str
    location: str
    staging_bucket: str
    resource_name: str | None
    agent_display_name: str

    @classmethod
    def from_env(cls) -> "DeploymentConfig":
        return cls(
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1"),
            staging_bucket=os.environ["GCS_STAGING_BUCKET"],
            resource_name=os.getenv("AGENT_ENGINE_RESOURCE_NAME") or None,
            agent_display_name="Data Trace Agent",
        )
