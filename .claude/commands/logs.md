---
description: Stream Cloud Logging output for the deployed agent
---

Run `make logs`. Then:

1. Stream the output to the user
2. Highlight any lines with severity ERROR or WARNING in your summary
3. If `GOOGLE_CLOUD_PROJECT` is not set, remind the user to configure `.env`
4. If no logs appear, check that the agent has been deployed (`make deploy-dev` or `make deploy-prod`)
