#!/usr/bin/env bash
# Read this agent's Cloud Logging output.
#
# Usage: bash deployment/scripts/read_logs.sh [SINCE] [LIMIT]
#   SINCE  gcloud --freshness value, e.g. 1h, 30m, 2d  (default: 1h)
#   LIMIT  maximum entries to return                   (default: 200)
#
# Reads the two reasoning_engine log names Agent Engine writes container output to.
# They are shared by every engine in the project, so the query is scoped by
# reasoning_engine_id rather than by agent_name, which is "root_agent" everywhere.
#
# Nothing returned, from any agent? See "Observability" in AGENTS.md -- a disabled
# _Default sink is the usual cause and looks exactly like broken instrumentation.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in .env}"
SINCE="${1:-1h}"
LIMIT="${2:-200}"

# Resource name from the environment (CI sets it) or the file deploy.py writes.
RESOURCE_NAME="${AGENT_ENGINE_RESOURCE_NAME:-}"
if [ -z "$RESOURCE_NAME" ] && [ -f .agent_engine_resource ]; then
  RESOURCE_NAME="$(tr -d '[:space:]' < .agent_engine_resource)"
fi

LOG_NAMES="logName=\"projects/$PROJECT/logs/aiplatform.googleapis.com%2Freasoning_engine_stdout\"
   OR logName=\"projects/$PROJECT/logs/aiplatform.googleapis.com%2Freasoning_engine_stderr\""

if [ -n "$RESOURCE_NAME" ]; then
  ENGINE_ID="${RESOURCE_NAME##*/}"
  FILTER="($LOG_NAMES)
   AND resource.labels.reasoning_engine_id=\"$ENGINE_ID\""
else
  echo "Note: no AGENT_ENGINE_RESOURCE_NAME and no .agent_engine_resource file, so this"
  echo "      shows every agent in the project, not just this one."
  echo ""
  FILTER="$LOG_NAMES"
fi

echo "Fetching logs for data-trace-agent (last $SINCE, limit $LIMIT)..."
echo ""

gcloud logging read \
  "$FILTER" \
  --project="$PROJECT" \
  --freshness="$SINCE" \
  --limit="$LIMIT" \
  --format="json" \
  | python3 -c "
import json, sys
entries = json.load(sys.stdin)
if not entries:
    print('No entries matched.')
    print('If the agent has served traffic in this window, check the project log sink')
    print('before suspecting the agent -- a disabled _Default sink discards every')
    print('non-audit entry, however it was written:')
    print('  gcloud logging sinks describe _Default --project=<project>')
for e in entries:
    ts = e.get('timestamp', '')[:19]
    severity = e.get('severity', 'INFO')[:4]
    payload = e.get('jsonPayload') or e.get('textPayload') or e.get('protoPayload') or {}
    if isinstance(payload, dict):
        msg = payload.get('message') or payload.get('msg') or payload.get('event') or json.dumps(payload)
    else:
        msg = str(payload)
    color = '\033[31m' if severity in ('ERRO','CRIT') else '\033[33m' if severity == 'WARN' else '\033[0m'
    print(f'{color}[{ts}] [{severity}] {msg}\033[0m')
"
