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
    printf '%b\n' "${BLUE}ℹ️  $*${NC}"
}

log_success() {
    printf '%b\n' "${GREEN}✅ $*${NC}"
}

log_warn() {
    printf '%b\n' "${YELLOW}⚠️  $*${NC}"
}

log_error() {
    printf '%b\n' "${RED}❌ ERROR: $*${NC}"
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
