#!/usr/bin/env bash
# Open Cloud Trace for this agent in the browser, and print the gcloud filter.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in .env}"
CONSOLE_URL="https://console.cloud.google.com/traces/list?project=${PROJECT}"

echo "Cloud Trace filter for data-trace-agent:"
echo ""
echo "  resource.type=\"aiplatform.googleapis.com/Endpoint\""
echo ""
echo "Opening Cloud Trace console..."
echo "  $CONSOLE_URL"
echo ""

if command -v open &>/dev/null; then
  open "$CONSOLE_URL"
elif command -v xdg-open &>/dev/null; then
  xdg-open "$CONSOLE_URL"
else
  echo "Could not open browser automatically. Visit the URL above manually."
fi
