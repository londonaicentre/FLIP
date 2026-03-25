#!/bin/bash
# Manage Elastic IPs and other state/lock operations

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

action="${1:?Action required: list-eips, release-unused-eips, delete-keypair, force-unlock}"

case "$action" in
    list-eips)
        log_info "Elastic IPs in ${AWS_REGION}:"
        aws_cmd ec2 describe-addresses --query 'Addresses[*].[PublicIp,AllocationId,AssociationId,NetworkInterfaceId]' --output table
        ;;
    
    release-unused-eips)
        log_info "Releasing unassociated Elastic IPs..."
        UNASSOC_EIPS=$(aws_cmd ec2 describe-addresses --filters "Name=association-id,Values=none" --query 'Addresses[*].AllocationId' --output text)
        
        if [ -z "$UNASSOC_EIPS" ]; then
            log_success "No unassociated EIPs found"
        else
            echo "$UNASSOC_EIPS" | tr ' ' '\n' | while read -r alloc_id; do
                [ -z "$alloc_id" ] && continue
                log_info "Releasing $alloc_id..."
                aws_cmd ec2 release-address --allocation-id "$alloc_id" \
                    && log_success "Released $alloc_id" \
                    || log_warn "Failed to release $alloc_id"
            done
        fi
        ;;
    
    delete-keypair)
        keypair="${2:-flip-keypair}"
        log_info "Deleting key pair: $keypair"
        if aws_cmd ec2 delete-key-pair --key-name "$keypair" 2>/dev/null; then
            log_success "Key pair $keypair deleted"
        else
            log_warn "Key pair $keypair doesn't exist or already deleted"
        fi
        ;;
    
    force-unlock)
        lock_id="${2:?Lock ID is required: force-unlock <lock-id>}"
        log_info "Force unlocking Terraform state (ID: $lock_id)..."
        if terraform force-unlock -force "$lock_id"; then
            log_success "Lock released"
        else
            log_error "Failed to release lock"
            exit 1
        fi
        ;;
    
    *)
        log_error "Unknown action: $action"
        echo "Usage: $(basename "$0") {list-eips|release-unused-eips|delete-keypair|force-unlock} [args]"
        exit 1
        ;;
esac
