# AGENTS.md — Data Trace Agent

This file is read automatically by AI coding assistants (Claude Code, Cursor, GitHub Copilot, Gemini Code Assist, etc.). It contains everything needed to work on this repository without further orientation.

## What this project is

**Data Trace Agent** is a [Google ADK](https://google.github.io/adk-docs/) agent deployed on [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/docs/agents/overview). It was generated from [agent-deployment-template](https://github.com/GEG-ETHZ/agent-deployment-template).

## Installation and setup

Prerequisites: Python 3.11+, `uv`, Node.js 20+, `gcloud` CLI

```bash
make install                  # install all dependencies
cp .env.example .env          # then fill in GOOGLE_CLOUD_PROJECT and GOOGLE_API_KEY
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
make dev                      # run agent at http://localhost:8000
```

One-time GCP setup (required before first deploy):

```bash
make setup-gcp                # creates service account, bucket, prints GitHub secrets to add
```

## Project structure

```text
agent/
  __init__.py         load_prompt() — reads prompts/prompts.yaml and concatenates .md files
  agent.py            root_agent (ADK Agent, no custom classes)
  agents/
    bigquery_agent.py        BigQuery query sub-agent
    code_analysis_agent.py   clone repos, map DVC hashes to commits, analyse code
    data_analysis_agent.py   pull DVC data, inspect Parquet / YAML files
    metadata_agent.py        extract *.meta.yaml / *.dvc metadata from the registry
  tools/
    __init__.py       re-exports all tools
    bigquery_tools.py  query_bigquery
    data_tools.py      dvc_pull, dvc_list_files, dvc_remote_list, inspect_parquet_file,
                       analyze_parquet_file, inspect_yaml_file, analyze_yaml_file,
                       list_files_in_directory
    git_tools.py       set_repository, find_meta_yaml_files, find_top_level_yaml_files,
                       list_projects, find_dvc_files, clone_remote_repository,
                       clone_repository_at_revision, get_dvc_import_info, get_dvc_md5,
                       find_commit_by_hash_string, checkout_commit, list_files,
                       read_file_content, get_repo_url_from_dvc_file,
                       initialize_registry, get_registry_context, switch_to_registry
    response_models.py Pydantic schemas for tool outputs
prompts/
  prompts.yaml        registry: maps agent names to prompt .md files
  system/
    base.md           identity and style instructions
    safety.md         refusal and safety guidelines
  tasks/
    root_agent.md     session initialization and delegation instructions
    metadata.md       metadata extraction task instructions
    data_analysis.md  DVC pull and data analysis task instructions
    code_analysis.md  code analysis and reproducibility task instructions
    bigquery.md       BigQuery query task instructions
deployment/
  config.py           resolve_model() + DeploymentConfig
  deploy.py           deploy to Agent Engine (create or update)
  scripts/
    setup_gcp.sh      one-time GCP bootstrap
    upload_secret.sh  upload a secret to Secret Manager
    read_logs.sh      stream Cloud Logging
    read_traces.sh    open Cloud Trace in browser
tests/
  unit/               pure function tests — no GCP, no network required
  evals/
    promptfoo.yaml    red-team + quality evaluation
    provider.py       promptfoo Python provider (runs agent inline)
    datasets/
      golden_set.jsonl reference test cases
```

## Make targets

| Target | Description |
|---|---|
| `make dev` | Run agent locally at http://localhost:8000 |
| `make install` | Install all dependencies |
| `make test` | Unit tests with coverage |
| `make eval` | Promptfoo red-team evaluation |
| `make lint` | Ruff lint |
| `make format` | Ruff format |
| `make typecheck` | Pyright |
| `make pre-commit` | All pre-commit hooks |
| `make deploy-dev` | Deploy to Agent Engine (dev) |
| `make deploy-prod` | Deploy to Agent Engine (prod) |
| `make logs` | Stream Cloud Logging |
| `make traces` | Open Cloud Trace in browser |
| `make setup-gcp` | One-time GCP bootstrap |
| `make upload-secret` | Upload a secret (e.g., GitLab deploy token) to Secret Manager |

## How to add a tool

1. Write the function in an existing file under `agent/tools/` (e.g. `git_tools.py`) or create a new module there — add type annotations and a docstring (ADK uses both to build the tool schema)
2. Export from `agent/tools/__init__.py`
3. Add to `tools=[...]` in `agent/agent.py` and/or the relevant sub-agent in `agent/agents/`
4. Add unit tests in `tests/unit/test_tools.py`

## How to modify prompts

1. Edit or create a `.md` file in `prompts/system/` or `prompts/tasks/`
2. Register it in `prompts/prompts.yaml` under the relevant agent
3. Verify with `make dev`

## How to add a sub-agent

1. Add an entry in `prompts/prompts.yaml`
2. Define the agent in `agent/agent.py` using `Agent()` (standard ADK syntax)
3. Wire to `root_agent` via `sub_agents=[new_agent]`

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Deploy | — | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | Deploy | `europe-west1` | Vertex AI region |
| `GCS_STAGING_BUCKET` | Deploy | — | GCS bucket for Agent Engine artefacts |
| `REPO_URL` | No | — | Remote Git URL of the DVC registry; cloned on first use as the default repository |
| `GIT_AUTH_TOKEN` | No | — | Git token (deploy token / PAT) for cloning private repos in headless runtimes. When set, repo URLs on the matching host are cloned over token-HTTPS instead of SSH |
| `GIT_AUTH_HOST` | No | host of `REPO_URL` | Host the `GIT_AUTH_TOKEN` is valid for (e.g. `gitlab.example.com`) |
| `GIT_AUTH_USERNAME` | No | `oauth2` | Username paired with `GIT_AUTH_TOKEN` in the HTTPS URL |
| `GIT_AUTH_TOKEN_SECRET` | No | — | Secret Manager secret id for the Git token (preferred over `GIT_AUTH_TOKEN` at deploy) |
| `GIT_AUTH_TOKEN_SECRET_VERSION` | No | `latest` | Secret version for `GIT_AUTH_TOKEN_SECRET` |
| `AGENT_ENGINE_SERVICE_ACCOUNT` | No | Agent Engine default | Service account the deployed agent runs as; grant it read on the DVC remote bucket(s) |
| `DVC_CONFIG_LOCAL` | No | — | Verbatim DVC workspace config written to `<repo>/.dvc/config.local` before `dvc pull` |
| `DVC_CONFIG_LOCAL_SECRET` | No | — | Secret Manager secret id for `DVC_CONFIG_LOCAL` (preferred at deploy) |
| `DVC_CONFIG_LOCAL_SECRET_VERSION` | No | `latest` | Secret version for `DVC_CONFIG_LOCAL_SECRET` |
| `AGENT_ENGINE_RESOURCE_NAME` | No | — | Existing resource to update; omit to create new |
| `MODEL_PROVIDER` | No | `google` | `google` \| `anthropic` \| `openai` \| `litellm` |
| `LITELLM_MODEL` | If provider=litellm | — | Full LiteLLM model string |
| `GOOGLE_API_KEY` | Local dev | — | Not needed on GCP (uses ADC) |
| `ANTHROPIC_API_KEY` | If provider=anthropic | — | |
| `OPENAI_API_KEY` | If provider=openai | — | |
| `SERPAPI_API_KEY` | No | — | Enables live web search; omit for stub |

## Model providers

Set `MODEL_PROVIDER` in `.env`:

| Value | Model |
|---|---|
| `google` (default) | Gemini 2.5 Pro |
| `anthropic` | Claude Opus 4.8 via LiteLLM |
| `openai` | GPT-4o via LiteLLM |
| `litellm` | Any model — set `LITELLM_MODEL` |

## Code conventions

- **No `print()` in Python package code** — use `logging`
- **Conventional commits**: `feat(scope): description` — enforced by commitizen
- **Pre-commit hooks**: ruff (lint + format), pyright, detect-secrets, markdownlint
  - Run `make pre-commit` before committing; never use `--no-verify`
- **CHANGELOG**: update `CHANGELOG.md` under `[Unreleased]` for every user-facing change

## CI/CD

| Workflow | Trigger | What it checks |
|---|---|---|
| `ci.yml` | push + PR | lint, format, typecheck, unit tests |
| `security.yml` | push to main + weekly | CodeQL, pip-audit, secret scan |
| `eval.yml` | PR to main | promptfoo red-team (90% pass threshold) |
| `deploy.yml` | push to main | deploy to Agent Engine prod |

Required GitHub Secrets: `GCP_SA_KEY`, `GOOGLE_CLOUD_PROJECT`, `GCS_STAGING_BUCKET`, `GOOGLE_API_KEY`  # pragma: allowlist secret
Required GitHub Variables: `GOOGLE_CLOUD_LOCATION`, `MODEL_PROVIDER`, `AGENT_ENGINE_RESOURCE_NAME` (after first deploy)
