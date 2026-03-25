#!/bin/bash
# Manage Secrets Manager operations

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

action="${1:?Action required: set-local-trust-endpoint}"

case "$action" in
    set-local-trust-endpoint)
        local_trust_ip="${2:?Local trust IP is required}"
        secret_id="${AWS_SECRET_NAME:-FLIP_API}"
        
        log_info "Updating $secret_id Trust_1-endpoint to https://${local_trust_ip}:${TRUST_API_PORT}..."
        
        # Get current secret
        secret_value=$(aws_cmd secretsmanager get-secret-value --secret-id "$secret_id" --query 'SecretString' --output text)
        
        # Update endpoint using python (more portable than jq)
        updated_secret=$(python3 - <<EOF "$secret_value" "$local_trust_ip" "$TRUST_API_PORT"
import json, sys
s = json.loads(sys.argv[1])
s['Trust_1-endpoint'] = f"https://{sys.argv[2]}:{sys.argv[3]}"
print(json.dumps(s))
EOF
)
        
        if [ $? -ne 0 ]; then
            log_error "Failed to update secret value"
            exit 1
        fi
        
        # Update secret in AWS
        echo "$updated_secret" | aws_cmd secretsmanager update-secret \
            --secret-id "$secret_id" \
            --secret-string file:///dev/stdin \
            >/dev/null 2>&1
        
        log_success "$secret_id Trust_1-endpoint updated to https://${local_trust_ip}:${TRUST_API_PORT}"
        ;;
    
    *)
        log_error "Unknown action: $action"
        echo "Usage: $(basename "$0") {set-local-trust-endpoint} <ip>"
        exit 1
        ;;
esac
