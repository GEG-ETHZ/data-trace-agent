#!/usr/bin/env bash
# One-time GCP project bootstrap for Data Trace Agent.
#
# Run once per GCP project — once for dev, once for prod, each pointed at its
# own GOOGLE_CLOUD_PROJECT. Safe to re-run — checks before creating.
#
# Usage:
#   ./deployment/scripts/setup_gcp.sh dev     # bootstrap the dev project
#   ./deployment/scripts/setup_gcp.sh prod    # bootstrap the prod project (default)
set -euo pipefail

GH_ENVIRONMENT="${1:-prod}"
case "$GH_ENVIRONMENT" in
  dev|prod) ;;
  *)
    echo "Usage: $0 [dev|prod]  (defaults to prod)" >&2
    exit 1
    ;;
esac

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in .env or export it first}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-europe-west1}"
SA_NAME="agent-engine-sa"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
BUCKET="${GCS_STAGING_BUCKET:-${PROJECT}-agent-staging}"

echo "=== GCP Bootstrap: Data Trace Agent ($GH_ENVIRONMENT) ==="
echo "  Project:  $PROJECT"
echo "  Location: $LOCATION"
echo "  SA:       $SA_EMAIL"
echo "  Bucket:   gs://$BUCKET"
echo ""

# Enable required APIs
echo "> Enabling APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  logging.googleapis.com \
  cloudtrace.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT"

# Create service account (idempotent)
echo "> Creating service account..."
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" &>/dev/null; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Agent Engine SA for Data Trace Agent" \
    --project="$PROJECT"
else
  echo "  Service account already exists, skipping."
fi

# Grant IAM roles
echo "> Granting IAM roles..."
for ROLE in roles/aiplatform.user roles/logging.logWriter roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE" \
    --condition=None \
    --quiet
done

# Grant actAs on the SA resource itself to the two principals that deploy: the SA
# (how CI authenticates) and whoever runs this bootstrap. Teammates deploying from
# their own machines must be added by hand -- see "Runtime identity" in AGENTS.md.
echo "> Granting actAs on the runtime service account..."
ACT_AS_MEMBERS=("serviceAccount:$SA_EMAIL")

# `|| true` because `set -e` would abort here when no account is configured, and
# get-value reports an unset value as either empty or the literal "(unset)".
DEPLOYER="$(gcloud config get-value account 2>/dev/null || true)"
case "$DEPLOYER" in
  "" | "(unset)" | "$SA_EMAIL")
    # Nothing to add: no configured account, or it is the SA already covered above.
    ;;
  *.iam.gserviceaccount.com)
    ACT_AS_MEMBERS+=("serviceAccount:$DEPLOYER")
    ;;
  *)
    ACT_AS_MEMBERS+=("user:$DEPLOYER")
    ;;
esac

for MEMBER in "${ACT_AS_MEMBERS[@]}"; do
  echo "  $MEMBER"
  gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --member="$MEMBER" \
    --role="roles/iam.serviceAccountUser" \
    --project="$PROJECT" \
    --condition=None \
    --quiet
done

# Create GCS staging bucket (idempotent)
echo "> Creating staging bucket..."
if ! gsutil ls "gs://$BUCKET" &>/dev/null; then
  gsutil mb -p "$PROJECT" -l "$LOCATION" "gs://$BUCKET"
  gsutil iam ch "serviceAccount:${SA_EMAIL}:roles/storage.objectAdmin" "gs://$BUCKET"
else
  echo "  Bucket already exists, skipping."
fi

# Generate SA key for GitHub Actions
echo "> Generating service account key..."
KEY_FILE="gcp-sa-key.json"
gcloud iam service-accounts keys create "$KEY_FILE" \
  --iam-account="$SA_EMAIL" \
  --project="$PROJECT"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Create the '$GH_ENVIRONMENT' GitHub Environment if it doesn't exist yet"
echo "(Settings > Environments > New environment), then add the following as its"
echo "ENVIRONMENT secrets (not repository secrets — dev and prod must not share these):"
echo ""
echo "  GCP_SA_KEY           = (not printed — see below)"
echo "  GOOGLE_CLOUD_PROJECT = $PROJECT"
echo ""
echo "Add the following as '$GH_ENVIRONMENT' environment variables:"
echo "  GOOGLE_CLOUD_LOCATION = $LOCATION"
echo "  MODEL_PROVIDER        = google"
echo ""
echo "Not needed -- deploy.py derives these from GOOGLE_CLOUD_PROJECT. Set them only"
echo "if you renamed the bucket or the service account:"
echo "  GCS_STAGING_BUCKET           = $BUCKET"
echo "  AGENT_ENGINE_SERVICE_ACCOUNT = $SA_EMAIL"
echo ""
echo "(AGENT_ENGINE_RESOURCE_NAME is an environment variable too — add it after this"
echo "environment's first deploy, once deploy.yml prints the created resource name.)"
echo ""
echo "To set GCP_SA_KEY without the key ever entering your terminal scrollback, pipe it"
echo "straight into gh (the value is base64 of $KEY_FILE):"
echo ""
echo "  base64 < $KEY_FILE | tr -d '\\n' | \\"
echo "    gh secret set GCP_SA_KEY --env $GH_ENVIRONMENT"
echo ""
echo "Then revoke the key — server-side first, so any copy that did leak is inert:"
echo ""
echo "  KEY_ID=\$(python3 -c \"import json;print(json.load(open('$KEY_FILE'))['private_key_id'])\")"
echo "  gcloud iam service-accounts keys delete \"\$KEY_ID\" \\"
echo "    --iam-account=$SA_EMAIL --project=$PROJECT --quiet"
echo "  rm $KEY_FILE"
echo ""
echo "This key is on the SHARED service account: a leak affects every agent in the"
echo "project. Re-running this script mints another key and revokes nothing, so audit"
echo "with: gcloud iam service-accounts keys list --iam-account=$SA_EMAIL"
echo "Replacing this flow with Workload Identity Federation is tracked separately."
echo ""
echo "Update your .env file with:"
cat <<ENV
GOOGLE_CLOUD_PROJECT=$PROJECT
GOOGLE_CLOUD_LOCATION=$LOCATION
MODEL_PROVIDER=google
ENV
echo ""
echo "(The staging bucket and runtime service account are derived from the project id;"
echo " add GCS_STAGING_BUCKET or AGENT_ENGINE_SERVICE_ACCOUNT only to override them.)"
