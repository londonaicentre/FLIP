#!/bin/bash
# Backup EIP allocation IDs before destruction so we can reassociate them later

set -e
source "$(dirname "$0")/utils.sh"

check_aws_profile

# Create backups directory if it doesn't exist
BACKUPS_DIR="$(dirname "$0")/../.backups"
mkdir -p "$BACKUPS_DIR"

BACKUP_FILE="$BACKUPS_DIR/eips-backup-$(date +%s).json"

log_info "📦 Backing up Elastic IP allocation IDs..."

# Describe all EIPs and save to file
aws_cmd ec2 describe-addresses \
  --query 'Addresses[*].[AllocationId,PublicIp,AssociationId,InstanceId,NetworkInterfaceId]' \
  --output json > "$BACKUP_FILE"

log_success "✅ EIP backup saved to: $BACKUP_FILE"
log_info ""
log_info "📋 Current EIPs:"
jq -r '.[] | "\(.AllocationId) -> \(.PublicIp) (Associated: \(if .InstanceId then .InstanceId else "None" end))"' "$BACKUP_FILE" || true

echo ""
cat "$BACKUP_FILE"
