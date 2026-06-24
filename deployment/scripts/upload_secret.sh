#!/bin/bash
#
# Creates or updates a secret in Google Cloud Secret Manager with the content of a file.
#
# This script is designed to be used for uploading secrets like SSH keys or Git PATs
# that are needed for the Cloud Run service to clone repositories.
#
# The secret itself should be managed by Terraform, but this script handles the
# sensitive value, which should not be in version control.

set -euo pipefail

# --- Configuration ---
# The GCP project ID. It's recommended to configure this via `gcloud config set project`.
PROJECT_ID=$(gcloud config get-value project)
LOCATION="global" # Secret Manager is a global service for the secret itself.

# --- Functions ---

function print_usage() {
  echo "Usage: $0 <secret-name> <path-to-secret-file>"
  echo "  <secret-name>: The name of the secret in Secret Manager (e.g., 'gitlab-pat')."
  echo "  <path-to-secret-file>: The local file path containing the secret value."
  echo ""
  echo "Example: $0 gitlab-pat ~/.ssh/gitlab_pat"
}

function secret_exists() {
  gcloud secrets describe "$1" --project="$PROJECT_ID" &>/dev/null
}

# --- Main Script ---

if [[ "$#" -ne 2 ]]; then
  echo "Error: Invalid number of arguments."
  print_usage
  exit 1
fi

SECRET_NAME="$1"
SECRET_FILE="$2"

if [[ ! -f "$SECRET_FILE" ]]; then
  echo "Error: Secret file not found at '$SECRET_FILE'"
  exit 1
fi

echo "Project: $PROJECT_ID"
echo "Secret Name: $SECRET_NAME"
echo "Secret File: $SECRET_FILE"
echo "---"

# Create the secret if it doesn't exist
if secret_exists "$SECRET_NAME"; then
  echo "Secret '$SECRET_NAME' already exists. Adding a new version."
else
  echo "Secret '$SECRET_NAME' not found. Creating it..."
  gcloud secrets create "$SECRET_NAME" \
    --project="$PROJECT_ID" \
    --replication-policy="automatic"
fi

# Add the file content as a new secret version
echo "Adding secret value from '$SECRET_FILE' as a new version..."
gcloud secrets versions add "$SECRET_NAME" \
  --project="$PROJECT_ID" \
  --data-file="$SECRET_FILE"

echo "✅ Successfully uploaded new version for secret '$SECRET_NAME'."
