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

set -e

# Configuration
REGION="eu-west-2"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="${FLIP_TFSTATE_BUCKET_NAME}"

echo "🚀 Bootstrapping Terraform Backend resources..."
echo "   Region: ${REGION}"
echo "   Bucket: ${BUCKET_NAME}"
echo "   Table:  ${DYNAMODB_TABLE}"

# 1. Create S3 Bucket
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

# 2. Push empty state file to S3 if not exists
STATE_FILE="terraform.tfstate"
if aws s3api head-object --bucket "$BUCKET_NAME" --key "$STATE_FILE" 2>/dev/null; then
    echo "✅ State file '$STATE_FILE' already exists in S3."
else
    echo "📤 Uploading empty state file to S3..."
    echo "{}" | aws s3api put-object --bucket "$BUCKET_NAME" --key "$STATE_FILE"
    echo "✅ State file uploaded."
fi

echo "🎉 Backend infrastructure is ready!"
