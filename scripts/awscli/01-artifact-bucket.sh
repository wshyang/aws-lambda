#!/usr/bin/env bash
#
# 01 - Artefact bucket.
#
# Same object as infrastructure/cloudformation/artifacts-bucket.yaml: versioned,
# encrypted, public access blocked, old versions expired.
#
# Usage: scripts/awscli/01-artifact-bucket.sh [--profile NAME] [--region NAME] [--artifact-bucket NAME]
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=00-env.sh
. "${SCRIPT_DIR}/00-env.sh"
parse_common_args "$@"

require_commands aws
BUCKET=$(resolved_artifact_bucket)

if aws_cli s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  info "bucket already exists: ${BUCKET}"
else
  info "creating bucket: ${BUCKET}"
  if [ "$REGION" = "us-east-1" ]; then
    aws_cli s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws_cli s3api create-bucket \
      --bucket "$BUCKET" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi
fi

info "applying versioning, encryption, public-access block and lifecycle"
aws_cli s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled >/dev/null

aws_cli s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}' >/dev/null

aws_cli s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null

aws_cli s3api put-bucket-ownership-controls \
  --bucket "$BUCKET" \
  --ownership-controls '{"Rules":[{"ObjectOwnership":"BucketOwnerEnforced"}]}' >/dev/null

aws_cli s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET" \
  --lifecycle-configuration \
  '{"Rules":[{"ID":"abort-incomplete-uploads","Status":"Enabled","AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}},{"ID":"expire-old-versions","Status":"Enabled","NoncurrentVersionExpiration":{"NoncurrentDays":90},"Filter":{"Prefix":""}}]}' >/dev/null

info "artefact bucket ready: s3://${BUCKET}"
info "artefact key: ${ARTIFACT_KEY}"
