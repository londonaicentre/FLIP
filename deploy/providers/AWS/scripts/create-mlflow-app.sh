#!/usr/bin/env bash
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Create the SageMaker serverless MLflow App that backs FLIP's experiment
# tracking + model registry (FLIP#745, WP6), together with its artifact bucket
# and artifact-store role.
#
# This is an operator step, not Terraform: the AWS provider has no resource for
# MLflow Apps yet (only the older, sized `aws_sagemaker_mlflow_tracking_server`,
# which we deliberately do NOT use — it costs ~$470/month). The script exists so
# the step is repeatable, reviewable, and above all *tagged*: without tags at
# creation time the spend is unattributable in Cost Explorer, and tags are not
# applied retroactively.
#
# After it prints the App ARN:
#   1. set MLFLOW_TRACKING_URI=<arn> in the environment file (.env.stag/.env.prod)
#   2. `make plan && make apply` — the ARN-gated IAM policies in iam_ecs.tf attach
#      (sagemaker:CallMlflowAppApi scoped to this App, plus the granular
#      sagemaker-mlflow layer) and the hub task definitions pick up the env var
#   3. redeploy the hub services
#
# Costs: MLflow Apps carry no compute charge; expect ~$0/month. Verify with a
# Cost Explorer query on the SageMaker service after go-live (see README).
#
# Usage:
#   AWS_PROFILE=stag ./scripts/create-mlflow-app.sh [--name flip-mlflow] [--region eu-west-2]
set -euo pipefail

NAME="flip-mlflow"
REGION="${AWS_REGION:-eu-west-2}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)   NAME="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${NAME}-artifacts-${ACCOUNT_ID}"

# Applied to every resource this script creates. `flip:component=mlflow` is what
# a Cost Explorer / Budgets filter keys on — it must be activated as a cost
# allocation tag in the Organization's *management* account before it appears
# (see deploy/providers/AWS/README.md, "MLflow cost monitoring"). Activation is
# not retroactive, so tag first, activate, then create.
TAG_COMPONENT="mlflow"
TAG_ISSUE="FLIP-745"

echo "==> account ${ACCOUNT_ID}, region ${REGION}, app ${NAME}"

echo "==> artifact bucket: s3://${BUCKET}"
if ! aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  aws s3api create-bucket \
    --bucket "${BUCKET}" \
    --region "${REGION}" \
    --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
fi
aws s3api put-public-access-block --bucket "${BUCKET}" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
aws s3api put-bucket-encryption --bucket "${BUCKET}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-tagging --bucket "${BUCKET}" \
  --tagging "TagSet=[{Key=flip:component,Value=${TAG_COMPONENT}},{Key=flip:issue,Value=${TAG_ISSUE}}]"

echo "==> artifact-store role: ${NAME}"
# The App assumes this role to read/write the artifact bucket. FLIP registers
# result zips by S3 *reference*, so in practice almost nothing is written here.
TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"sagemaker.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
if ! aws iam get-role --role-name "${NAME}" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "${NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" \
    --description "Artifact-store role for the FLIP MLflow App (FLIP-745)" \
    --tags "Key=flip:component,Value=${TAG_COMPONENT}" "Key=flip:issue,Value=${TAG_ISSUE}" >/dev/null
  sleep 10 # IAM propagation before SageMaker validates the role
fi
aws iam put-role-policy --role-name "${NAME}" --policy-name s3-artifacts --policy-document "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::${BUCKET}/*"},
    {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": "arn:aws:s3:::${BUCKET}"}
  ]
}
JSON
)"
ROLE_ARN="$(aws iam get-role --role-name "${NAME}" --query Role.Arn --output text)"

echo "==> MLflow App"
# The AWS CLI only grew the mlflow-app commands recently; a stale CLI fails here
# with "Invalid choice: create-mlflow-app" — upgrade rather than work around it.
if ! aws sagemaker list-mlflow-apps --region "${REGION}" \
      --query "MlflowApps[?Name=='${NAME}'].Arn" --output text | grep -q .; then
  aws sagemaker create-mlflow-app \
    --region "${REGION}" \
    --name "${NAME}" \
    --artifact-store-uri "s3://${BUCKET}/artifacts" \
    --role-arn "${ROLE_ARN}" \
    --tags "Key=flip:component,Value=${TAG_COMPONENT}" "Key=flip:issue,Value=${TAG_ISSUE}" >/dev/null
fi
APP_ARN="$(aws sagemaker list-mlflow-apps --region "${REGION}" \
  --query "MlflowApps[?Name=='${NAME}'].Arn" --output text)"

echo "==> waiting for the App to reach Created (~2 min)"
for _ in $(seq 1 40); do
  STATUS="$(aws sagemaker describe-mlflow-app --region "${REGION}" --arn "${APP_ARN}" --query Status --output text)"
  [[ "${STATUS}" == "Created" ]] && break
  [[ "${STATUS}" == *Failed* ]] && { echo "App creation failed: ${STATUS}" >&2; exit 1; }
  sleep 10
done

cat <<EOF

✅ MLflow App ready.

   MLFLOW_TRACKING_URI=${APP_ARN}

Next:
  1. Put that line in the environment file for this account (.env.stag / .env.prod).
  2. make plan && make apply   # attaches the ARN-gated IAM policies, updates task defs
  3. Redeploy the hub services.
  4. Open the UI:
     aws sagemaker create-presigned-mlflow-app-url --region ${REGION} --arn ${APP_ARN} \\
       --query AuthorizedUrl --output text

Tear down (the App is free to run, so only do this when retiring the integration):
  aws sagemaker delete-mlflow-app --region ${REGION} --arn ${APP_ARN}
  aws iam delete-role-policy --role-name ${NAME} --policy-name s3-artifacts
  aws iam delete-role --role-name ${NAME}
  aws s3 rb s3://${BUCKET} --force
EOF
