#!/bin/bash
# Selectively destroy FLIP infrastructure while preserving persistent resources
# (Cognito, Secrets Manager, S3 buckets, ACM certificates, Route53 records,
# and — in production — the VPC with its Transit Gateway attachment).

set -eo pipefail
source "$(dirname "$0")/utils.sh"

if [ "$PROD" = "true" ]; then
  log_warn "PRODUCTION environment detected — VPC will NOT be destroyed (Transit Gateway attachment)"
fi

log_info "💥 Proceeding with infrastructure destruction..."

# Step 1: Delete RDS database instance directly using AWS CLI.
# Terraform cannot destroy the DB instance cleanly due to parameter group
# dependencies; deleting it via the AWS CLI first avoids those cycles.
log_info "Step 1: Deleting RDS database instance via AWS CLI..."
DB_INSTANCE_ID=$(aws_cmd rds describe-db-instances \
  --query 'DBInstances[?DBInstanceIdentifier==`flip-database`].[DBInstanceIdentifier]' \
  --output text 2>/dev/null || echo "")

if [ -n "$DB_INSTANCE_ID" ]; then
  log_info "  Found database instance: $DB_INSTANCE_ID - Deleting..."
  aws_cmd rds delete-db-instance \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --skip-final-snapshot \
    2>&1 | grep -E "DBInstance|Status|Error" || true

  log_info "  Waiting for database deletion (this may take a few minutes)..."
  aws_cmd rds wait db-instance-deleted \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    2>&1 || log_warn "  Timeout waiting for DB deletion - proceeding anyway"

  sleep 5  # Give AWS a moment to fully cleanup
else
  log_info "  No RDS database instance found"
fi

# Step 2: Now terraform can successfully destroy parameter group and subnet group
log_info "Step 2: Destroying Terraform-managed RDS resources..."
terraform destroy -auto-approve \
  -target='module.flip_db.module.db_parameter_group.aws_db_parameter_group.this[0]' \
  -target=aws_db_subnet_group.flip_db_subnet_group \
  2>&1 | grep -v "Warning: Resource targeting is in effect" | grep -v "Warning: Applied changes may be incomplete" | grep -v "Note that the -target option is not suitable for routine use" || true

# Step 3: Destroy remaining infrastructure (EC2, security groups, ALB/NLB, IAM, etc.)
log_info "Step 3: Destroying remaining infrastructure..."

DESTROY_TARGETS=(
  -target=module.ec2_security_group
  -target=module.rds_security_group
  -target=module.alb
  -target=module.alb_security_group
  -target=module.ec2_role
  -target=aws_iam_instance_profile.ec2_profile
  -target=aws_key_pair.flip_keypair
  -target=aws_instance.ec2_instance
  -target=aws_cloudwatch_log_group.flip_log_group
  -target=aws_iam_role_policy.ec2_secret
  -target=aws_ses_template.flip_access_request
  -target=aws_ses_template.flip_xnat_credentials
  -target=module.trust_ec2.aws_instance.trust_host
  -target=module.trust_security_group
  -target=aws_key_pair.host_key
  -target=local_file.env
)

# Only destroy VPC in non-production — production VPC has Transit Gateway attachment
if [ "$PROD" != "true" ]; then
  DESTROY_TARGETS+=(-target=module.flip_vpc)
fi

terraform destroy -auto-approve \
  "${DESTROY_TARGETS[@]}" \
  2>&1 | grep -v "Warning: Resource targeting is in effect" | grep -v "Warning: Applied changes may be incomplete" | grep -v "Note that the -target option is not suitable for routine use"

log_success "✅ Infrastructure destroyed!"
if [ "$PROD" = "true" ]; then
  log_info ""
  log_info "✓ VPC preserved (Transit Gateway attachment)"
fi
