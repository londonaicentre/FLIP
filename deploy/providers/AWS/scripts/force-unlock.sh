#!/bin/bash
# Force-release a stuck Terraform state lock.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

lock_id="${1:?Lock ID is required: force-unlock.sh <lock-id>}"

log_info "Force unlocking Terraform state (ID: $lock_id)..."
if terraform force-unlock -force "$lock_id"; then
    log_success "Lock released"
else
    log_error "Failed to release lock"
    exit 1
fi
