#!/bin/bash
# Temporarily disable prevent_destroy on EIPs, perform destroy, then re-enable

set -e
source "$(dirname "$0")/utils.sh"

if [ "$PROD" = "true" ]; then
  log_warn "PRODUCTION environment detected — VPC will NOT be destroyed (Transit Gateway attachment)"
fi

log_warn "🔓 Temporarily disabling prevent_destroy on EIP resources..."

# Using sed to temporarily modify the tf files
log_info "📋 Backing up Terraform configuration..."
cp main.tf main.tf.backup
cp modules/trust_ec2/main.tf modules/trust_ec2/main.tf.backup

log_info "🗑️  Disabling prevent_destroy in EIP resources temporarily..."
if [[ "$OSTYPE" == "darwin"* ]]; then
  sed -i '' 's/prevent_destroy = true/prevent_destroy = false/g' main.tf modules/trust_ec2/main.tf
else
  sed -i 's/prevent_destroy = true/prevent_destroy = false/g' main.tf modules/trust_ec2/main.tf
fi

log_info "💥 Proceeding with infrastructure destruction..."

# Step 1: Delete RDS database instance directly using AWS CLI
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

# Step 3: Destroy remaining infrastructure (VPC, security groups, etc.)
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
  -target=module.trust_ec2.aws_security_group.trust_host_sg
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.ssh
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.xnat
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.pacs_ui
  -target=module.trust_ec2.aws_vpc_security_group_egress_rule.allow_all
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

log_info "🔒 Re-enabling prevent_destroy in EIP resources..."
mv main.tf.backup main.tf
mv modules/trust_ec2/main.tf.backup modules/trust_ec2/main.tf

log_success "✅ Infrastructure destroyed! EIPs preserved and protect_destroy re-enabled."
log_info ""
log_info "✓ Elastic IPs remain allocated:"
log_info "  - Central Hub EIP: $(terraform output -raw CentralHubEip 2>/dev/null || echo 'N/A')"
log_info "  - Trust EC2 EIP: $(terraform output -raw TrustEc2Eip 2>/dev/null || echo 'N/A')"
if [ "$PROD" = "true" ]; then
  log_info ""
  log_info "✓ VPC preserved (Transit Gateway attachment)"
fi
