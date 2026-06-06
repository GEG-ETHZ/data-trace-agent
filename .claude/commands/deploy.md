---
description: Deploy the agent to Vertex AI Agent Engine (prod)
---

Run `make deploy-prod`. After it completes:

1. Report the Agent Engine resource name from `.agent_engine_resource` or stdout
2. If the deploy fails, read the error output carefully and suggest a specific fix
3. Common failures:
   - `GOOGLE_CLOUD_PROJECT not set` → user needs to fill in `.env`
   - `GCS_STAGING_BUCKET not set` → run `make setup-gcp` first
   - Authentication error → run `gcloud auth application-default login`
