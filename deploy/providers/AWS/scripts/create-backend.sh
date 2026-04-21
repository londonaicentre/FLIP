#!/bin/bash
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

set -euo pipefail

REGION="${TFSTATE_REGION:-${AWS_REGION:-eu-west-2}}"
BUCKET_NAME="${TFSTATE_BUCKET_NAME:-}"
STATE_KEY="${TFSTATE_KEY:-terraform.tfstate}"

if [ -z "$BUCKET_NAME" ]; then
    echo "❌ TFSTATE_BUCKET_NAME must be set." >&2
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "🚀 Bootstrapping Terraform backend resources..."
echo "   Account: ${ACCOUNT_ID}"
echo "   Region:  ${REGION}"
echo "   Bucket:  ${BUCKET_NAME}"
echo "   Key:     ${STATE_KEY}"

# 1. Create S3 Bucket if it doesn't exist, and configure it for Terraform state storage.
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    echo "✅ S3 bucket '$BUCKET_NAME' already exists."
else
    echo "📦 Creating S3 bucket '$BUCKET_NAME'..."
    aws s3api create-bucket \
        --bucket "$BUCKET_NAME" \
        --region "$REGION" \
        --create-bucket-configuration LocationConstraint="$REGION"

    echo "🔒 Enabling versioning..."
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled

    echo "🛡️  Blocking public access..."
    aws s3api put-public-access-block \
        --bucket "$BUCKET_NAME" \
        --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

    echo "✅ S3 bucket created."
fi

# 2. Check if the state object already exists. If not, it will be created automatically by Terraform on first write.
if aws s3api head-object --bucket "$BUCKET_NAME" --key "$STATE_KEY" 2>/dev/null; then
    echo "✅ State object '$STATE_KEY' already exists in S3."
else
    echo "ℹ️  State object '$STATE_KEY' does not exist yet."
    echo "   Terraform will create it automatically on the first state write."
fi

echo "🎉 Backend infrastructure is ready!"
