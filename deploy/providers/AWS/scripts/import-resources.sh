#!/bin/bash
# Import persistent AWS resources to Terraform state
# Prevents replacement of pre-existing resources on subsequent applies

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

log_info "Importing persistent resources..."

# 1. EC2 Key Pairs
echo ""
log_info "1️⃣  EC2 Key Pairs..."
terraform import aws_key_pair.flip_keypair flip-keypair 2>/dev/null || log_success "flip-keypair (already imported)"
terraform import aws_key_pair.host_key host-aws 2>/dev/null || log_success "host-key (already imported)"

# 2. FLIP S3 Bucket
echo ""
log_info "2️⃣  FLIP S3 Bucket..."
BUCKET_NAME="${FLIP_BUCKET_NAME}"
if [ -n "$BUCKET_NAME" ]; then
    if aws_cmd s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
        log_success "Found bucket: $BUCKET_NAME"
        terraform import aws_s3_bucket.flip_bucket "$BUCKET_NAME" 2>/dev/null || log_success "FLIP S3 bucket (already in state)"
        terraform import aws_s3_bucket_cors_configuration.flip_bucket_cors "$BUCKET_NAME" 2>/dev/null || log_success "FLIP S3 CORS"
        terraform import aws_s3_object.app_destination_bucket "$BUCKET_NAME/app_destination_bucket/" 2>/dev/null || log_success "app_destination_bucket folder"
        terraform import aws_s3_object.model_files "$BUCKET_NAME/model_files/" 2>/dev/null || log_success "model_files folder"
        terraform import aws_s3_object.uploaded_federated_data "$BUCKET_NAME/uploaded_federated_data/" 2>/dev/null || log_success "uploaded_federated_data folder"
    else
        log_warn "Bucket $BUCKET_NAME does not exist in AWS"
    fi
else
    log_warn "FLIP_BUCKET_NAME not set in environment"
fi

# 3. AI Centre S3 Bucket
echo ""
log_info "3️⃣  AI Centre S3 Bucket..."
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

# 4. Secrets Manager
echo ""
log_info "4️⃣  Secrets Manager..."
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

# 5. Cognito
echo ""
log_info "5️⃣  Cognito..."
POOL_ID=$(aws_cmd cognito-idp list-user-pools --max-results 20 --query 'UserPools[?Name==`flip-user-pool`].Id' --output text 2>/dev/null || echo "")
if [ -n "$POOL_ID" ]; then
    terraform import aws_cognito_user_pool.flip_user_pool "$POOL_ID" 2>/dev/null || log_success "User pool"
    
    DOMAIN=$(aws_cmd cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --query 'UserPool.Domain' --output text 2>/dev/null || echo "")
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "None" ]; then
        terraform import aws_cognito_user_pool_domain.main "$DOMAIN" 2>/dev/null || log_success "Domain"
        terraform import random_string.cognito_domain "$DOMAIN" 2>/dev/null || log_success "Random string"
    fi
    
    CLIENT_ID=$(aws_cmd cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --query 'UserPoolClients[0].ClientId' --output text 2>/dev/null || echo "")
    if [ -n "$CLIENT_ID" ]; then
        terraform import aws_cognito_user_pool_client.client "$POOL_ID/$CLIENT_ID" 2>/dev/null || log_success "Client"
    fi
    
    terraform import aws_cognito_user.admin_user "$POOL_ID/aicentreflip@gmail.com" 2>/dev/null || log_success "Admin user"
    terraform import aws_cognito_user.researcher_user "$POOL_ID/rafaelagd@gmail.com" 2>/dev/null || log_success "Researcher user"
fi

# 6. SES Email Identity
echo ""
log_info "6️⃣  SES Email Identity..."
EXISTING_SES_EMAIL=$(aws_cmd ses list-identities --query 'Identities[0]' --output text 2>/dev/null || echo "")
if [ -n "$EXISTING_SES_EMAIL" ] && [ "$EXISTING_SES_EMAIL" != "None" ]; then
    if [ "$EXISTING_SES_EMAIL" = "${SES_VERIFIED_EMAIL}" ]; then
        log_success "Existing SES identity matches configuration: $EXISTING_SES_EMAIL"
        terraform import aws_ses_email_identity.flip_sender "$EXISTING_SES_EMAIL" 2>/dev/null || log_success "SES Email Identity (already imported)"
    else
        log_warn "Existing SES identity ($EXISTING_SES_EMAIL) does NOT match configuration (${SES_VERIFIED_EMAIL})"
        log_info "Terraform will destroy and recreate with correct email"
    fi
else
    log_info "No existing SES identity found - Terraform will create ${SES_VERIFIED_EMAIL}"
fi

# 7. ACM Certificate
echo ""
log_info "7️⃣  ACM Certificate..."
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

# 8. Route53 NLB Record
echo ""
log_info "8️⃣  Route53 NLB Record..."
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
log_info "Note: Terraform state bucket (${FLIP_TFSTATE_BUCKET_NAME}) is managed externally via create_backend.sh"
log_success "Persistent resources imported!"
