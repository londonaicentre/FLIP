#!/bin/bash
# Selectively destroy FLIP infrastructure while preserving persistent resources
# (Cognito, Secrets Manager, S3 buckets, ACM certificates, Route53 records,
# and — in production — the VPC with its Transit Gateway attachment).

set -eo pipefail
source "$(dirname "$0")/utils.sh"

is_true() {
  case "${1:-}" in
    true|TRUE|1|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

PRESERVE_VPC_FLAG="${PRESERVE_VPC:-false}"
if [ "$PROD" = "true" ]; then
  PRESERVE_VPC_FLAG="true"
fi

if is_true "$PRESERVE_VPC_FLAG"; then
  log_warn "VPC preservation enabled — VPC will NOT be destroyed"
fi

log_info "💥 Proceeding with infrastructure destruction..."
log_info "ℹ️  This process may take several minutes. Please be patient..."

cleanup_interface_endpoints_for_vpc() {
  local vpc_id="$1"
  if [ -z "$vpc_id" ]; then
    log_warn "No VPC ID provided for endpoint cleanup"
    return 0
  fi

  log_info "🧹 Cleaning up interface VPC endpoints in $vpc_id (if any)..."
  log_info "🔍 Checking for existing VPC endpoints..."

  local endpoint_ids
  endpoint_ids=$(aws_cmd ec2 describe-vpc-endpoints \
    --filters Name=vpc-id,Values="$vpc_id" Name=vpc-endpoint-type,Values=Interface \
    --query 'VpcEndpoints[].VpcEndpointId' \
    --output text 2>/dev/null || true)

  if [ -z "$endpoint_ids" ] || [ "$endpoint_ids" = "None" ]; then
    log_info "  ✅ No interface VPC endpoints found"
    return 0
  fi

  log_info "  📦 Found $endpoint_ids interface VPC endpoints to delete"
  for endpoint_id in $endpoint_ids; do
    log_info "  🔪 Deleting VPC endpoint: $endpoint_id"
    aws_cmd ec2 delete-vpc-endpoints --vpc-endpoint-ids "$endpoint_id" >/dev/null 2>&1 || true
    log_info "  ⏳ Waiting for endpoint deletion: $endpoint_id"
    aws_cmd ec2 wait vpc-endpoint-deleted --vpc-endpoint-ids "$endpoint_id" 2>/dev/null || \
      log_warn "  ⏰ Timeout waiting for endpoint deletion: $endpoint_id"
  done
}

wait_for_vpc_cleanup_dependencies() {
  local vpc_id="$1"
  local timeout_seconds=600
  local poll_interval=10
  local elapsed=0

  if [ -z "$vpc_id" ]; then
    return 0
  fi

  log_info "⏳ Waiting for VPC dependency cleanup in $vpc_id..."
  
  while [ "$elapsed" -lt "$timeout_seconds" ]; do
    local remaining_endpoints
    local remaining_enis

    remaining_endpoints=$(aws_cmd ec2 describe-vpc-endpoints \
      --filters Name=vpc-id,Values="$vpc_id" Name=vpc-endpoint-type,Values=Interface \
      --query 'length(VpcEndpoints)' \
      --output text 2>/dev/null || echo "0")
    remaining_enis=$(aws_cmd ec2 describe-network-interfaces \
      --filters Name=vpc-id,Values="$vpc_id" \
      --query 'length(NetworkInterfaces)' \
      --output text 2>/dev/null || echo "0")

    [ "$remaining_endpoints" = "None" ] && remaining_endpoints="0"
    [ "$remaining_enis" = "None" ] && remaining_enis="0"

    if [ "$remaining_endpoints" = "0" ] && [ "$remaining_enis" = "0" ]; then
      log_info "  ✅ No remaining interface endpoints or ENIs in $vpc_id"
      return 0
    fi

    log_info "  ⏳ Waiting for VPC dependency cleanup (endpoints=$remaining_endpoints, enis=$remaining_enis)..."
    sleep "$poll_interval"
    elapsed=$((elapsed + poll_interval))
  done

  log_warn "  ⏰ Timed out waiting for full VPC dependency cleanup"
  return 0
}

# Step 1: Delete RDS database instance directly using AWS CLI.
# Terraform cannot destroy the DB instance cleanly due to parameter group
# dependencies; deleting it via the AWS CLI first avoids those cycles.
log_info "🔄 Step 1: Deleting RDS database instance via AWS CLI..."
log_info "🔍 Checking for existing RDS database instance..."

DB_INSTANCE_ID=$(aws_cmd rds describe-db-instances \
  --query 'DBInstances[?DBInstanceIdentifier==`flip-database`].[DBInstanceIdentifier]' \
  --output text 2>/dev/null || echo "")

if [ -n "$DB_INSTANCE_ID" ]; then
  log_info "  📦 Found database instance: $DB_INSTANCE_ID"
  
  # Check if database is already in a deleting or deleted state
  DB_STATUS=$(aws_cmd rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --query 'DBInstances[0].DBInstanceStatus' \
    --output text 2>/dev/null || echo "unknown")
  
  if [ "$DB_STATUS" = "deleting" ] || [ "$DB_STATUS" = "deleted" ]; then
    log_info "  ⏳ Database instance $DB_INSTANCE_ID is already in $DB_STATUS state"
    log_info "  🔄 Proceeding with cleanup..."
  else
    log_info "  🔥 Initiating database deletion: $DB_INSTANCE_ID"
    aws_cmd rds delete-db-instance \
      --db-instance-identifier "$DB_INSTANCE_ID" \
      --skip-final-snapshot \
      2>&1 | grep -E "DBInstance|Status|Error" || true
  fi

  log_info "  ⏳ Waiting for database deletion (this may take a few minutes)..."
  log_info "  ⏱️  Timeout set to 20 minutes for database deletion"
  timeout 1200 aws rds wait db-instance-deleted \
    --region "${AWS_REGION}" --profile "${AWS_PROFILE}" \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    2>&1 || log_warn "  ⏰ Timeout waiting for DB deletion - proceeding anyway"

  log_info "  🧹 Giving AWS a moment to fully cleanup..."
  sleep 5  # Give AWS a moment to fully cleanup
else
  log_info "  ✅ No RDS database instance found"
fi

# Step 2: Now terraform can successfully destroy parameter group and subnet group
log_info "🔄 Step 2: Destroying Terraform-managed RDS resources..."
log_info "🔍 Checking for RDS resources in Terraform state..."

# First check if the resources exist in state
if terraform state list | grep -q "aws_db_subnet_group.flip_db_subnet_group\|module.flip_db"; then
  log_info "  📦 RDS resources found in state - proceeding with targeted destruction"
  log_info "  ⏱️  Timeout set to 5 minutes for RDS resource destruction"
  # Improve the RDS destruction by checking if resources actually exist before targeting
  timeout 300 terraform destroy -auto-approve \
    -target='module.flip_db.module.db_parameter_group.aws_db_parameter_group.this[0]' \
    -target=aws_db_subnet_group.flip_db_subnet_group \
    2>&1 | grep -v "Warning: Resource targeting is in effect" | grep -v "Warning: Applied changes may be incomplete" | grep -v "Note that the -target option is not suitable for routine use" || true
else
  log_info "  ✅ No RDS resources found in Terraform state - skipping Terraform RDS destruction"
fi

# Step 3: Destroy remaining infrastructure (EC2, security groups, ALB/NLB, IAM, etc.)
log_info "🔄 Step 3: Destroying remaining infrastructure..."
log_info "⏰ This step may take several minutes depending on resources..."

# Improved approach: Check if resources actually exist before trying to destroy them
# This prevents hanging when Terraform tries to destroy non-existent resources
DESTROY_TARGETS=()

# Only add targets if they exist in the state
if terraform state list | grep -q "module.ec2_security_group"; then
  DESTROY_TARGETS+=(-target=module.ec2_security_group)
fi

if terraform state list | grep -q "module.rds_security_group"; then
  DESTROY_TARGETS+=(-target=module.rds_security_group)
fi

if terraform state list | grep -q "module.alb"; then
  DESTROY_TARGETS+=(-target=module.alb)
fi

if terraform state list | grep -q "module.alb_security_group"; then
  DESTROY_TARGETS+=(-target=module.alb_security_group)
fi

if terraform state list | grep -q "module.ec2_role"; then
  DESTROY_TARGETS+=(-target=module.ec2_role)
fi

if terraform state list | grep -q "aws_iam_instance_profile.ec2_profile"; then
  DESTROY_TARGETS+=(-target=aws_iam_instance_profile.ec2_profile)
fi

if terraform state list | grep -q "aws_key_pair.flip_keypair"; then
  DESTROY_TARGETS+=(-target=aws_key_pair.flip_keypair)
fi

if terraform state list | grep -q "aws_instance.ec2_instance"; then
  DESTROY_TARGETS+=(-target=aws_instance.ec2_instance)
fi

if terraform state list | grep -q "aws_cloudwatch_log_group.flip_log_group"; then
  DESTROY_TARGETS+=(-target=aws_cloudwatch_log_group.flip_log_group)
fi

if terraform state list | grep -q "aws_iam_role_policy.ec2_secret"; then
  DESTROY_TARGETS+=(-target=aws_iam_role_policy.ec2_secret)
fi

if terraform state list | grep -q "aws_ses_template.flip_access_request"; then
  DESTROY_TARGETS+=(-target=aws_ses_template.flip_access_request)
fi

if terraform state list | grep -q "aws_ses_template.flip_xnat_credentials"; then
  DESTROY_TARGETS+=(-target=aws_ses_template.flip_xnat_credentials)
fi

if terraform state list | grep -q "module.trust_ec2.aws_instance.trust_host"; then
  DESTROY_TARGETS+=(-target=module.trust_ec2.aws_instance.trust_host)
fi

if terraform state list | grep -q "module.trust_security_group"; then
  DESTROY_TARGETS+=(-target=module.trust_security_group)
fi

if terraform state list | grep -q "aws_key_pair.host_key"; then
  DESTROY_TARGETS+=(-target=aws_key_pair.host_key)
fi

if terraform state list | grep -q "local_file.env"; then
  DESTROY_TARGETS+=(-target=local_file.env)
fi

# Only proceed if we have targets to destroy
if [ ${#DESTROY_TARGETS[@]} -gt 0 ]; then
  log_info "  📦 Found ${#DESTROY_TARGETS[@]} targets to destroy"
  log_info "  ⏱️  Timeout set to 10 minutes for infrastructure destruction"
  timeout 600 terraform destroy -auto-approve \
    "${DESTROY_TARGETS[@]}" \
    2>&1 | grep -v "Warning: Resource targeting is in effect" | grep -v "Warning: Applied changes may be incomplete" | grep -v "Note that the -target option is not suitable for routine use"
else
  log_info "  ✅ No infrastructure targets found in Terraform state - skipping infrastructure destruction"
fi

# Step 4: In non-production, delete interface endpoints and then destroy VPC.
# Some service-managed endpoints (e.g. GuardDuty) are not Terraform-managed and
# leave ENIs attached to subnets, which blocks subnet/VPC deletion.
if ! is_true "$PRESERVE_VPC_FLAG"; then
  log_info "🔄 Step 4: Destroying VPC..."
  log_info "🔍 Checking for VPC resources..."

  VPC_ID=$(terraform state show -no-color 'module.flip_vpc.aws_vpc.this[0]' 2>/dev/null | grep -E '^[[:space:]]*id[[:space:]]*=' | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)
  if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
    VPC_ID=$(aws_cmd ec2 describe-vpcs \
      --filters Name=tag:Name,Values=flip-vpc \
      --query 'Vpcs[0].VpcId' \
      --output text 2>/dev/null || true)
    [ "$VPC_ID" = "None" ] && VPC_ID=""
  fi

  if [ -n "$VPC_ID" ]; then
    cleanup_interface_endpoints_for_vpc "$VPC_ID"
    wait_for_vpc_cleanup_dependencies "$VPC_ID"

    log_info "  🔥 Destroying VPC: $VPC_ID"
    terraform destroy -auto-approve \
      -target=module.flip_vpc \
      2>&1 | grep -v "Warning: Resource targeting is in effect" | grep -v "Warning: Applied changes may be incomplete" | grep -v "Note that the -target option is not suitable for routine use"
  else
    log_info "  ✅ No VPC found to destroy"
  fi
fi

log_success "✅ Infrastructure destroyed successfully!"
if is_true "$PRESERVE_VPC_FLAG"; then
  log_info ""
  log_info "✓ VPC preserved"
fi
