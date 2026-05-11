#!/bin/bash
# Import persistent AWS resources to Terraform state
# Prevents replacement of pre-existing resources on subsequent applies

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

log_info "Importing persistent resources..."

# 1. FLIP application S3 buckets
#
# The single legacy `flip_bucket` has been split into three purpose-built
# buckets (model file uploads / FL results / app bundles). Imports below match the
# `module.flip_*_bucket.aws_s3_bucket.this` addresses emitted by
# modules/flip_s3_bucket. Each block is independent so a stack that already
# created two of the three (e.g. mid-rollout) still picks up the missing one.
#
# `aws_s3_bucket_cors_configuration` and friends use `<bucket-name>` as their
# import ID — same as the bare bucket — because every sub-resource is
# bucket-keyed in S3.
echo ""
log_info "1️⃣  FLIP application S3 buckets..."
# Snapshot of terraform state addresses, refreshed lazily inside the helper.
# `terraform state list` is moderately expensive; we capture it once per
# `import_flip_bucket` call so multiple sub-resource probes share the read.
_tf_state_list=""

_refresh_tf_state_list() {
    _tf_state_list="$(terraform state list 2>/dev/null || true)"
}

# Probe state first, then import only on miss. Real `terraform import` errors
# (wrong address, AWS permission denied, expired credentials, state-lock
# contention) propagate via `set -e` — the previous `2>/dev/null || log_success`
# pattern silently swallowed all of those and made the script's idempotent-import
# contract a lie.
_import_if_missing() {
    local addr="$1"
    local import_id="$2"
    local description="$3"

    if printf '%s\n' "$_tf_state_list" | grep -qxF "$addr"; then
        log_success "$description already in state"
        return
    fi
    log_info "Importing $description ..."
    terraform import "$addr" "$import_id"
    log_success "$description imported"
}

import_flip_bucket() {
    # $1 = module label (e.g. flip_model_files_uploads_bucket)
    # $2 = bucket name (from env)
    # $3 = friendly description for logs
    # $4 = "yes" if the module renders a CORS configuration (uploads + fl-results), else "no"
    local module_label="$1"
    local bucket_name="$2"
    local description="$3"
    local has_cors="$4"

    if [ -z "$bucket_name" ]; then
        log_warn "$description: environment variable not set, skipping import"
        return
    fi

    # Distinguish "bucket does not exist" (404) from "I can't see it" (403).
    # Without this, a permissions gap looks identical to a never-created bucket
    # and the script would skip the import, after which `terraform apply` would
    # try to create the existing bucket and fail on BucketAlreadyOwnedByYou.
    local head_stderr
    head_stderr="$(aws_cmd s3api head-bucket --bucket "$bucket_name" 2>&1 >/dev/null)" || {
        if printf '%s' "$head_stderr" | grep -qE "Not Found|404"; then
            log_warn "$description ($bucket_name): does not exist in AWS yet — Terraform will create it"
            return
        fi
        log_error "$description ($bucket_name): head-bucket failed — fix this before re-running:"
        printf '%s\n' "$head_stderr" >&2
        return 1
    }

    log_success "$description: found $bucket_name"
    _refresh_tf_state_list
    _import_if_missing "module.${module_label}.aws_s3_bucket.this" "$bucket_name" "$description bucket"
    _import_if_missing "module.${module_label}.aws_s3_bucket_public_access_block.this" "$bucket_name" "$description public access block"
    _import_if_missing "module.${module_label}.aws_s3_bucket_server_side_encryption_configuration.this" "$bucket_name" "$description SSE config"
    _import_if_missing "module.${module_label}.aws_s3_bucket_versioning.this" "$bucket_name" "$description versioning"
    if [ "$has_cors" = "yes" ]; then
        _import_if_missing "module.${module_label}.aws_s3_bucket_cors_configuration.this[0]" "$bucket_name" "$description CORS"
    fi
}

import_flip_bucket flip_model_files_uploads_bucket "${FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME}" "Model file uploads" yes
import_flip_bucket flip_fl_results_bucket "${FLIP_FL_RESULTS_BUCKET_NAME}" "FL results" yes
import_flip_bucket flip_app_bundles_bucket "${FLIP_APP_BUNDLES_BUCKET_NAME}" "App bundles" no

# 2. AI Centre S3 Bucket
echo ""
log_info "2️⃣  AI Centre S3 Bucket..."
if [ -n "$AICENTRE_BUCKET_NAME" ]; then
    if aws_cmd s3api head-bucket --bucket "$AICENTRE_BUCKET_NAME" 2>/dev/null; then
        log_success "Found bucket: $AICENTRE_BUCKET_NAME"
        terraform import aws_s3_bucket.aicentre_bucket "$AICENTRE_BUCKET_NAME" 2>/dev/null || log_success "AI Centre S3 bucket (already in state)"
        terraform import aws_s3_bucket_cors_configuration.aicentre_bucket_cors "$AICENTRE_BUCKET_NAME" 2>/dev/null || log_success "AI Centre S3 CORS"
    else
        log_warn "Bucket $AICENTRE_BUCKET_NAME does not exist in AWS - will be created"
    fi
else
    log_warn "AICENTRE_BUCKET_NAME not set in environment"
fi

# 3. Secrets Manager
echo ""
log_info "3️⃣  Secrets Manager..."
SECRET_ARN=$(aws_cmd secretsmanager list-secrets --include-planned-deletion --query 'SecretList[?Name==`FLIP_API`].ARN' --output text 2>/dev/null || echo "")
if [ -n "$SECRET_ARN" ]; then
    DELETION_DATE=$(aws_cmd secretsmanager describe-secret --secret-id FLIP_API --query 'DeletedDate' --output text 2>/dev/null || echo "")
    if [ -n "$DELETION_DATE" ] && [ "$DELETION_DATE" != "None" ]; then
        log_info "Restoring scheduled-for-deletion secret..."
        aws_cmd secretsmanager restore-secret --secret-id FLIP_API 2>/dev/null && log_success "Restored!"
    fi
    terraform import 'module.flip_api_secret.aws_secretsmanager_secret.this[0]' "$SECRET_ARN" 2>/dev/null || log_success "Secret"
    
    VERSION_ID=$(aws_cmd secretsmanager describe-secret --secret-id FLIP_API --query 'VersionIdsToStages | keys(@) | [0]' --output text 2>/dev/null || echo "")
    if [ -n "$VERSION_ID" ]; then
        terraform import 'module.flip_api_secret.aws_secretsmanager_secret_version.this[0]' "$SECRET_ARN|$VERSION_ID" 2>/dev/null || log_success "Secret version"
    fi
fi

# 4. Cognito (now lives under module.cognito.* — addresses match the modules/cognito layout)
echo ""
log_info "4️⃣  Cognito..."
POOL_ID=$(aws_cmd cognito-idp list-user-pools --max-results 20 --query 'UserPools[?Name==`flip-user-pool`].Id' --output text 2>/dev/null || echo "")
if [ -n "$POOL_ID" ]; then
    terraform import 'module.cognito.aws_cognito_user_pool.flip_user_pool' "$POOL_ID" 2>/dev/null || log_success "User pool"

    DOMAIN=$(aws_cmd cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --query 'UserPool.Domain' --output text 2>/dev/null || echo "")
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "None" ]; then
        terraform import 'module.cognito.aws_cognito_user_pool_domain.main' "$DOMAIN" 2>/dev/null || log_success "Domain"
        terraform import 'module.cognito.random_string.cognito_domain' "$DOMAIN" 2>/dev/null || log_success "Random string"
    fi

    CLIENT_ID=$(aws_cmd cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --query 'UserPoolClients[0].ClientId' --output text 2>/dev/null || echo "")
    if [ -n "$CLIENT_ID" ]; then
        terraform import 'module.cognito.aws_cognito_user_pool_client.client' "$POOL_ID/$CLIENT_ID" 2>/dev/null || log_success "Client"
    fi

    terraform import 'module.cognito.aws_cognito_user.admin_user' "$POOL_ID/aicentreflip@gmail.com" 2>/dev/null || log_success "Admin user"
    # researcher_user is wrapped in count = var.researcher_email == "" ? 0 : 1 inside the module.
    terraform import 'module.cognito.aws_cognito_user.researcher_user[0]' "$POOL_ID/rafaelagd@gmail.com" 2>/dev/null || log_success "Researcher user"
fi

# 5. SES Email Identity (now lives under module.ses.*)
echo ""
log_info "5️⃣  SES Email Identity..."
EXISTING_SES_EMAIL=$(aws_cmd ses list-identities --query 'Identities[0]' --output text 2>/dev/null || echo "")
if [ -n "$EXISTING_SES_EMAIL" ] && [ "$EXISTING_SES_EMAIL" != "None" ]; then
    if [ "$EXISTING_SES_EMAIL" = "${SES_VERIFIED_EMAIL}" ]; then
        log_success "Existing SES identity matches configuration: $EXISTING_SES_EMAIL"
        terraform import 'module.ses.aws_ses_email_identity.flip_sender' "$EXISTING_SES_EMAIL" 2>/dev/null || log_success "SES Email Identity (already imported)"
    else
        log_warn "Existing SES identity ($EXISTING_SES_EMAIL) does NOT match configuration (${SES_VERIFIED_EMAIL})"
        log_info "Terraform will destroy and recreate with correct email"
    fi
else
    log_info "No existing SES identity found - Terraform will create ${SES_VERIFIED_EMAIL}"
fi

# 6. ACM Certificate
echo ""
log_info "6️⃣  ACM Certificate..."
CERT_ARN=$(aws_cmd acm list-certificates --query "CertificateSummaryList[?DomainName=='${ALB_SUBDOMAIN}'].CertificateArn" --output text 2>/dev/null || echo "")
if [ -n "$CERT_ARN" ]; then
    terraform import aws_acm_certificate.flip "$CERT_ARN" 2>/dev/null || log_success "ACM certificate"
    terraform import aws_acm_certificate_validation.flip "$CERT_ARN" 2>/dev/null || log_success "Certificate validation"
    
    VALIDATION_RECORDS=$(aws_cmd acm describe-certificate --certificate-arn "$CERT_ARN" --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Name' --output text 2>/dev/null || echo "")
    if [ -n "$VALIDATION_RECORDS" ]; then
        HOSTED_ZONE_ID=$(aws_cmd route53 list-hosted-zones --query "HostedZones[?Name=='${ALB_SUBDOMAIN}.'].Id" --output text 2>/dev/null | cut -d'/' -f3 || echo "")
        if [ -n "$HOSTED_ZONE_ID" ]; then
            RECORD_NAME=$(aws_cmd acm describe-certificate --certificate-arn "$CERT_ARN" --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Name' --output text 2>/dev/null || echo "")
            terraform import "aws_route53_record.cert_validation[\"${ALB_SUBDOMAIN}\"]" "${HOSTED_ZONE_ID}_${RECORD_NAME}_CNAME" 2>/dev/null || log_success "Route53 validation record"
        fi
    fi
fi

# 7. Route53 NLB Record
echo ""
log_info "7️⃣  Route53 NLB Record..."
if [ -n "$NLB_SUBDOMAIN" ]; then
    HOSTED_ZONE_ID=$(aws_cmd route53 list-hosted-zones --query "HostedZones[?Name=='${ALB_SUBDOMAIN}.'].Id" --output text 2>/dev/null | cut -d'/' -f3 || echo "")
    if [ -n "$HOSTED_ZONE_ID" ]; then
        NLB_RECORD=$(aws_cmd route53 list-resource-record-sets \
            --hosted-zone-id "$HOSTED_ZONE_ID" \
            --query "ResourceRecordSets[?Name=='${NLB_SUBDOMAIN}.'][0].Name" \
            --output text 2>/dev/null || echo "")
        if [ -n "$NLB_RECORD" ] && [ "$NLB_RECORD" != "None" ]; then
            terraform import aws_route53_record.fl_server_nlb "${HOSTED_ZONE_ID}_${NLB_SUBDOMAIN}_A" 2>/dev/null || log_success "Route53 NLB record (already imported)"
        else
            log_info "No existing Route53 NLB record found for ${NLB_SUBDOMAIN} - will be created"
        fi
    else
        log_warn "Could not find hosted zone for ${ALB_SUBDOMAIN}"
    fi
else
    log_warn "NLB_SUBDOMAIN not set in environment"
fi

echo ""
log_info "Note: Terraform state bucket (${FLIP_TFSTATE_BUCKET_NAME}) is managed externally via scripts/create-backend.sh"
log_success "Persistent resources imported!"
