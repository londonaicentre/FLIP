#!/bin/bash
# Shared utilities for AWS deployment scripts

set -eo pipefail

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ️  $*${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $*${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠️  $*${NC}"
}

log_error() {
    echo -e "${RED}❌ ERROR: $*${NC}"
}

# AWS wrapper with common args
aws_cmd() {
    aws "$@" --region "${AWS_REGION}" --profile "${AWS_PROFILE}"
}

# Check if AWS profile is set
check_aws_profile() {
    if [ -z "$AWS_PROFILE" ]; then
        log_error "AWS_PROFILE not set"
        exit 1
    fi
    if [ -z "$AWS_REGION" ]; then
        log_error "AWS_REGION not set"
        exit 1
    fi
}

# Verify resource exists in AWS
aws_resource_exists() {
    local resource_type="$1"
    local resource_id="$2"
    
    case "$resource_type" in
        bucket)
            aws s3api head-bucket --bucket "$resource_id" --profile "$AWS_PROFILE" 2>/dev/null
            ;;
        keypair)
            aws_cmd ec2 describe-key-pairs --key-names "$resource_id" 2>/dev/null | grep -q "KeyName"
            ;;
        *)
            log_error "Unknown resource type: $resource_type"
            return 1
            ;;
    esac
}

# Safely read from dotenv file
read_env_var() {
    local var_name="$1"
    local env_file="${2:-.env.stag}"
    grep "^${var_name}=" "$env_file" 2>/dev/null | cut -d'=' -f2- | tr -d '"'
}

# Wait for condition with timeout
wait_for() {
    local condition="$1"
    local timeout="${2:-300}"
    local interval="${3:-5}"
    local elapsed=0
    
    while [ $elapsed -lt $timeout ]; do
        if eval "$condition"; then
            return 0
        fi
        sleep $interval
        elapsed=$((elapsed + interval))
    done
    
    log_error "Timeout waiting for: $condition"
    return 1
}
