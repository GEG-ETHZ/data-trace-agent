# Changelog

All notable changes to Data Trace Agent are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [Unreleased]

### Added

- Initial agent scaffold from [agent-deployment-template](https://github.com/GEG-ETHZ/agent-deployment-template)
- Git authentication for headless runtimes via GitLab Group Deploy Token
  (recommended) or Personal Access Token: when `GIT_AUTH_TOKEN` is set, repo
  URLs on the matching host (`GIT_AUTH_HOST`, defaulting to the `REPO_URL` host)
  are cloned over token-HTTPS (`https://<GIT_AUTH_USERNAME>:<token>@host/...`),
  so the registry and project repos clone on Vertex AI Agent Engine (which has
  no SSH key). For deploy tokens set `GIT_AUTH_USERNAME` to the token name; for
  PATs use `oauth2`. Credentials are redacted from any error output. Deploy
  forwards a curated env-var allowlist to the runtime and supports the token as a
  Secret Manager `SecretRef` (`GIT_AUTH_TOKEN_SECRET`) or a plain value
- `make upload-secret` target and `deployment/scripts/upload_secret.sh` helper
  for uploading deploy tokens (or any secret) to Secret Manager
- DVC remote credentials on headless runtimes: deploy now sets the Agent Engine
  `service_account` (`AGENT_ENGINE_SERVICE_ACCOUNT`) whose ADC authenticates GCS
  remote pulls, and `dvc_pull` writes `<repo>/.dvc/config.local` from
  `DVC_CONFIG_LOCAL` (plain or Secret Manager `SecretRef`) so config-based
  remotes (WebDAV, SSH) get credentials at runtime without committing them
- Git/DVC tooling ported from `data-history-agent`: `set_repository`,
  `find_meta_yaml_files`, `find_dvc_files`, `list_projects`,
  `clone_remote_repository`, `get_dvc_md5`, `find_commit_by_hash_string`,
  `checkout_commit`, `list_files`, `get_repo_url_from_dvc_file` (`git_tools`)
  and `dvc_pull`, `dvc_list_files`, `inspect_parquet_file`,
  `analyze_parquet_file`, `inspect_yaml_file`, `analyze_yaml_file`,
  `list_files_in_directory` (`data_tools`)
- `metadata_agent`, `code_analysis_agent`, and `data_analysis_agent` sub-agents
  wired to `root_agent` via ADK `sub_agents`
- Prompt composition system via `prompts/prompts.yaml`
- Deployment scripts for Vertex AI Agent Engine
- Promptfoo red-team evaluation suite
- GitHub Actions: CI, security, eval, deploy workflows

### Changed

- `root_agent` now coordinates Git/DVC sub-agents instead of using the
  example `get_current_datetime` / `web_search` tools
- Added `gitpython`, `pandas`, `pyarrow`, and `dvc[all]` dependencies
  (`dvc[all]` installs every DVC storage backend — gs, s3, azure, webdav, ssh,
  gdrive, hdfs, oss, webhdfs — so `dvc pull` works regardless of a project's
  remote type)
- The default DVC registry is now configured via the `REPO_URL` environment
  variable, which must be a remote Git URL and is cloned on first use
  (replaces the previous `REPO_LOCATION` local-folder fallback)
- HTTP(S) Git URLs are automatically rewritten to their SSH (scp-like) form
  before cloning (e.g. `https://gitlab.example.com/group/repo.git` →
  `git@gitlab.example.com:group/repo.git`), so clones use the developer's SSH
  key. URLs with embedded credentials (deploy tokens) or a port are left as-is.

### Fixed

- `find_commit_by_hash_string` now uses `git log -S` (pickaxe) instead of
  `iter_commits(all=True, S=...)`, which always failed because `git rev-list`
  rejects both `--all` and a default revision together
- Git/DVC tool output now emits real newlines instead of literal `\n`
