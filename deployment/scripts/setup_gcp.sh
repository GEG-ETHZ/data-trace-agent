#!/usr/bin/env bash
# One-time GCP project bootstrap for Data Trace Agent.
# Run once per GCP project. Safe to re-run — checks before creating.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in .env or export it first}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-europe-west1}"
SA_NAME="agent-engine-sa"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
BUCKET="${GCS_STAGING_BUCKET:-${PROJECT}-agent-staging}"

echo "=== GCP Bootstrap: Data Trace Agent ==="
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
echo "Add the following to GitHub repository secrets (Settings > Secrets and variables > Actions):"
echo ""
echo "  GCP_SA_KEY          = $(cat "$KEY_FILE" | base64 | tr -d '\n')"
echo "  GOOGLE_CLOUD_PROJECT = $PROJECT"
echo "  GCS_STAGING_BUCKET   = $BUCKET"
echo ""
echo "Add the following as GitHub repository variables:"
echo "  GOOGLE_CLOUD_LOCATION = $LOCATION"
echo "  MODEL_PROVIDER        = google"
echo ""
echo "IMPORTANT: Delete $KEY_FILE after copying the value above."
echo "  rm $KEY_FILE"
echo ""
echo "Update your .env file with:"
cat <<ENV
GOOGLE_CLOUD_PROJECT=$PROJECT
GOOGLE_CLOUD_LOCATION=$LOCATION
GCS_STAGING_BUCKET=$BUCKET
MODEL_PROVIDER=google
ENV
