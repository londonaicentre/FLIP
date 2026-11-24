#!/bin/bash
# Force-release a stuck Terraform state lock.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

lock_id="${1:?Lock ID is required: force-unlock.sh <lock-id>}"

log_info "Force unlocking Terraform state (ID: $lock_id)..."
# Unset static AWS credential env vars that would override the profile-based
# SSO chain (invalid security token).
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
if terraform force-unlock -force "$lock_id"; then
    log_success "Lock released"
else
    log_error "Failed to release lock"
    exit 1
fi
