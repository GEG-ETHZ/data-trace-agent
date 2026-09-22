# AGENTS.md — Data Trace Agent

This file is read automatically by AI coding assistants (Claude Code, Cursor, GitHub Copilot, Gemini Code Assist, etc.). It contains everything needed to work on this repository without further orientation.

## What this project is

**Data Trace Agent** is a [Google ADK](https://google.github.io/adk-docs/) agent deployed on [Vertex AI Agent Engine](https://cloud.google.com/vertex-ai/docs/agents/overview). It tracks [agent-deployment-template](https://github.com/danielvogler/agent-deployment-template) via cruft — see `.cruft.json`, and run `cruft update` to pull template changes.

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
  monitoring/
    dashboard.json    Cloud Monitoring dashboard (requests, latency, CPU, memory)
    alerting/         error-rate and p95-latency alert policies
  scripts/
    setup_gcp.sh          one-time GCP bootstrap
    setup_monitoring.sh   one-time Cloud Monitoring bootstrap
    upload_secret.sh      upload a secret to Secret Manager
    health_check.py       smoke-test the deployed resource
    read_logs.sh          stream Cloud Logging
    read_traces.py        list Cloud Trace spans
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
| `make health-check` | Smoke-test the deployed resource without redeploying |
| `make rollback` | Redeploy a previous git ref: `make rollback REF=<tag> [ENV=prod\|dev]` |
| `make logs` | Stream Cloud Logging |
| `make traces` | List this agent's Cloud Trace spans |
| `make setup-gcp` | One-time GCP bootstrap |
| `make setup-monitoring` | One-time Cloud Monitoring dashboard + alert policy bootstrap |
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
2. Create `agent/agents/<name>_agent.py` defining the agent with `Agent()` (standard ADK
   syntax), following the existing modules there
3. Export it from `agent/agents/__init__.py`
4. Wire to `root_agent` via `sub_agents=[...]` in `agent/agent.py`

## Observability

`agent/observability.py` applies structured JSON logging at the boundary this project
controls — tool calls — rather than `Runner.run_async`, which Agent Engine's managed runtime
drives internally and our code never touches in production.

- **`@instrument`** — wraps a tool function (sync or async) and logs `<name>.start`,
  `<name>.end` (with `duration_ms`) or `<name>.error` (with the exception message) as JSON,
  and opens a span when tracing is on. Applied to all 26 tools across `agent/tools/`.
- **`log_event(event_type, fields, severity="INFO")`** — one structured JSON line for anything
  else worth recording. The Python log level derived from `severity` sets the LogEntry's own
  severity, so `severity=ERROR` is filterable directly in Logs Explorer.
- **`redact_pii(value)`** — recursively redacts emails, SSNs and card-shaped numbers.
  `log_event` and `@instrument` both apply it, but it is defence in depth, not a licence to
  log sensitive fields.
- **`log_model_usage(event)`** — token counts from an ADK event's `usage_metadata`, for code
  that iterates the event stream itself (the promptfoo eval provider does).

**Logs need no configuration.** Agent Engine forwards container stdout/stderr to Cloud Logging
and parses a JSON stdout line into a structured `jsonPayload`. `make logs` reads exactly those,
scoped to this agent by `reasoning_engine_id` — the log names are shared by every reasoning
engine in the project.

**Traces do need configuration**, because nothing forwards spans. `deployment/config.py` sets
two variables on the deployed resource, and neither is optional:

| Variable | Effect |
|---|---|
| `CLOUD_TRACE_ENABLED` | Builds the OpenTelemetry tracer; without it `_build_tracer()` returns `None` and no span is ever emitted |
| `OTEL_EXPORTER_GCP_TRACE_PROJECT_ID` | The project to export to. Without it the exporter falls back to `google.auth.default()`, which resolves no project inside the Agent Engine container, and every export fails with `INVALID_ARGUMENT: Invalid project id in name!` |

Tracing is off by default so a local `make dev` writes only to stdout and needs no
credentials; export `CLOUD_TRACE_ENABLED=true` to opt in locally. Telemetry never breaks a
tool call: a missing library or unresolvable credentials is reported once on stderr and the
tool runs on.

`@instrument` spans are **children** of ADK's `invoke_workflow` root span, so `make traces`
lists roots only — use `read_traces.py --spans` to expand them.

> **If application logs seem to be missing, check the project's log sink first.** A disabled
> `_Default` sink discards every non-audit entry however it was written, which looks exactly
> like broken instrumentation.
>
> ```bash
> gcloud logging sinks describe _Default --project=$GOOGLE_CLOUD_PROJECT   # disabled: true is the bug
> ```

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
| `CLOUD_TRACE_ENABLED` | No | off | Export `@instrument` spans to Cloud Trace; set on the deployed resource by `deploy.py` |

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

### Pre-commit (required — always fix before committing)

```bash
make pre-commit   # runs all hooks
```

Hooks: ruff (lint + format), pyright, detect-secrets, markdownlint.

- If ruff fails: run `make format` then `make lint` — ruff autofixes most issues
- If pyright fails: fix the type errors it reports
- If detect-secrets fails: make sure you have not committed credentials
- **Never use `git commit --no-verify`** — this bypasses safety checks

### Conventional commits (enforced by commitizen hook)

Format: `type(scope): description`

```text
feat(agent): add calendar lookup tool
fix(prompts): correct safety guidelines for PII handling
chore(deps): bump google-adk to 1.1.0
docs(readme): update deployment instructions
test(evals): add promptfoo test for jailbreak via roleplay
refactor(deployment): simplify config dataclass
```

The commit-msg hook rejects non-conforming messages. `lint-pr.yml` checks the PR title
separately, because a squash merge discards those commit subjects and uses the PR title.

### CHANGELOG (update for every user-facing change)

Add an entry under `[Unreleased]` in `CHANGELOG.md` before committing, in Keep a Changelog
format:

```markdown
## [Unreleased]

### Added
- Calendar lookup tool powered by Google Calendar API

### Fixed
- Web search stub now includes query in snippet for easier local debugging
```

Run `uv run cz bump` to cut a release and move unreleased entries to a dated section.

## Claude Code slash commands

| Command | What it does |
|---|---|
| `/deploy` | Runs `make deploy-prod` and reports the resource name |
| `/eval` | Runs `make eval` and summarises results |
| `/logs` | Runs `make logs` and streams Cloud Logging output |

## CI/CD

| Workflow | Trigger | What it checks |
|---|---|---|
| `ci.yml` | push + PR | lint, format, typecheck, unit tests |
| `security.yml` | push to main + weekly | CodeQL, pip-audit, secret scan |
| `eval.yml` | PR to main | promptfoo red-team (90% pass threshold) |
| `lint-pr.yml` | PR opened/edited | PR title is a conventional commit — it becomes the squash subject |
| `cruft-check.yml` | push + PR + weekly | template drift against `.cruft.json` (non-blocking) |
| `deploy.yml` | manual only | deploy to Agent Engine, environment chosen as an input |

`deploy.yml` is `workflow_dispatch`-only deliberately: it previously fired on every push to
main with no dependency on `ci.yml`, so a red build still deployed against the live resource.
Re-enabling push-triggered CD needs the runtime secrets configured, a `dev` GitHub Environment
to exist (only `prod` does), and a `workflow_run` gate on `ci.yml`.

Required GitHub Secrets: `GCP_SA_KEY`, `GOOGLE_CLOUD_PROJECT`, `GCS_STAGING_BUCKET`, `GOOGLE_API_KEY`  # pragma: allowlist secret
Required GitHub Variables: `GOOGLE_CLOUD_LOCATION`, `MODEL_PROVIDER`, `AGENT_ENGINE_RESOURCE_NAME` (after first deploy)
