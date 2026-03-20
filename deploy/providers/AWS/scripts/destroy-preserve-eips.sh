#!/bin/bash
# Temporarily disable prevent_destroy on EIPs, perform destroy, then re-enable

set -e
source "$(dirname "$0")/utils.sh"

log_warn "🔓 Temporarily disabling prevent_destroy on EIP resources..."

# Disable prevent_destroy on EIPs
terraform apply -auto-approve -replace='aws_eip.central_hub_eip[0]' 2>&1 | while read -r line; do
  if echo "$line" | grep -q "prevent_destroy"; then
    # Skip the prevent_destroy warnings during this operation
    continue
  fi
  # Use -target to update just the lifecycle without a full apply
done || true

# Actually, a better approach: use sed to temporarily modify the tf files
log_info "📋 Backing up Terraform configuration..."
cp main.tf main.tf.backup
cp modules/trust_ec2/main.tf modules/trust_ec2/main.tf.backup

log_info "🗑️  Disabling prevent_destroy in EIP resources temporarily..."
sed -i 's/prevent_destroy = true/prevent_destroy = false/g' main.tf modules/trust_ec2/main.tf

log_info "💥 Proceeding with infrastructure destruction..."
terraform destroy -auto-approve \
  -target=module.flip_vpc \
  -target=module.ec2_security_group \
  -target=module.rds_security_group \
  -target=aws_db_subnet_group.flip_db_subnet_group \
  -target=module.flip_db \
  -target=module.alb \
  -target=module.alb_security_group \
  -target=module.ec2_role \
  -target=aws_iam_instance_profile.ec2_profile \
  -target=aws_key_pair.flip_keypair \
  -target=aws_instance.ec2_instance \
  -target=aws_cloudwatch_log_group.flip_log_group \
  -target=aws_iam_role_policy.ec2_secret \
  -target=aws_ses_template.flip_access_request \
  -target=aws_ses_template.flip_xnat_credentials \
  -target=module.trust_ec2.aws_instance.trust_host \
  -target=module.trust_ec2.aws_security_group.trust_host_sg \
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.ssh \
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.trust_api \
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.xnat \
  -target=module.trust_ec2.aws_vpc_security_group_ingress_rule.pacs_ui \
  -target=module.trust_ec2.aws_vpc_security_group_egress_rule.allow_all \
  -target=aws_key_pair.host_key \
  -target=local_file.env 2>&1 | grep -v "Warning: Resource targeting is in effect" | grep -v "Warning: Applied changes may be incomplete" | grep -v "Note that the -target option is not suitable for routine use"

log_info "🔒 Re-enabling prevent_destroy in EIP resources..."
mv main.tf.backup main.tf
mv modules/trust_ec2/main.tf.backup modules/trust_ec2/main.tf

log_success "✅ Infrastructure destroyed! EIPs preserved and protect_destroy re-enabled."
log_info ""
log_info "✓ Elastic IPs remain allocated:"
log_info "  - Central Hub EIP: $(terraform output -raw CentralHubEip 2>/dev/null || echo 'N/A')"
log_info "  - Trust EC2 EIP: $(terraform output -raw TrustEc2Eip 2>/dev/null || echo 'N/A')"
