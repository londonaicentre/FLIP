#!/bin/bash
# AWS state/lock management operations (keypair deletion, terraform state unlock)

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

action="${1:?Action required: delete-keypair, force-unlock}"

case "$action" in
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
        echo "Usage: $(basename "$0") {delete-keypair|force-unlock} [args]"
        exit 1
        ;;
esac
