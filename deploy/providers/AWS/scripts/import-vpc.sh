#!/bin/bash
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Import existing VPC infrastructure into Terraform state.
# Run after import-persistent when the VPC already exists (e.g. re-deploying
# to an environment where the VPC was not destroyed between migrations).

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

check_aws_profile

tf_import() {
    local addr="$1"
    local id="$2"
    terraform import "$addr" "$id" 2>/dev/null && log_success "Imported $addr" || log_success "$addr (already in state)"
}

log_info "Importing VPC infrastructure..."

# --- VPC ---
VPC_ID=$(aws_cmd ec2 describe-vpcs \
    --filters "Name=tag:Name,Values=${VPC_NAME}" \
    --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "")

if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
    log_warn "VPC '${VPC_NAME}' not found — nothing to import"
    exit 0
fi
log_success "Found VPC: $VPC_ID"

tf_import 'module.flip_vpc.aws_vpc.this[0]' "$VPC_ID"

# --- Subnets (map by CIDR to preserve AZ index order) ---
echo ""
log_info "Subnets..."
PRIVATE_CIDRS=("10.0.101.0/24" "10.0.102.0/24")
PUBLIC_CIDRS=("10.0.1.0/24" "10.0.2.0/24")

for i in 0 1; do
    cidr="${PRIVATE_CIDRS[$i]}"
    subnet_id=$(aws_cmd ec2 describe-subnets \
        --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidrBlock,Values=$cidr" \
        --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo "")
    [ -n "$subnet_id" ] && [ "$subnet_id" != "None" ] && \
        tf_import "module.flip_vpc.aws_subnet.private[$i]" "$subnet_id" || \
        log_warn "Private subnet $cidr not found"
done

for i in 0 1; do
    cidr="${PUBLIC_CIDRS[$i]}"
    subnet_id=$(aws_cmd ec2 describe-subnets \
        --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidrBlock,Values=$cidr" \
        --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo "")
    [ -n "$subnet_id" ] && [ "$subnet_id" != "None" ] && \
        tf_import "module.flip_vpc.aws_subnet.public[$i]" "$subnet_id" || \
        log_warn "Public subnet $cidr not found"
done

# --- Internet Gateway ---
echo ""
log_info "Internet Gateway..."
IGW_ID=$(aws_cmd ec2 describe-internet-gateways \
    --filters "Name=attachment.vpc-id,Values=$VPC_ID" \
    --query 'InternetGateways[0].InternetGatewayId' --output text 2>/dev/null || echo "")
[ -n "$IGW_ID" ] && [ "$IGW_ID" != "None" ] && \
    tf_import 'module.flip_vpc.aws_internet_gateway.this[0]' "$IGW_ID" || \
    log_warn "Internet Gateway not found"

# --- EIP for NAT Gateway ---
echo ""
log_info "Elastic IP (NAT)..."
NAT_ID=$(aws_cmd ec2 describe-nat-gateways \
    --filter "Name=vpc-id,Values=$VPC_ID" "Name=state,Values=available" \
    --query 'NatGateways[0].NatGatewayId' --output text 2>/dev/null || echo "")
if [ -n "$NAT_ID" ] && [ "$NAT_ID" != "None" ]; then
    EIP_ALLOC=$(aws_cmd ec2 describe-nat-gateways \
        --nat-gateway-ids "$NAT_ID" \
        --query 'NatGateways[0].NatGatewayAddresses[0].AllocationId' --output text 2>/dev/null || echo "")
    [ -n "$EIP_ALLOC" ] && [ "$EIP_ALLOC" != "None" ] && \
        tf_import 'module.flip_vpc.aws_eip.nat[0]' "$EIP_ALLOC" || \
        log_warn "EIP allocation not found"

    # --- NAT Gateway ---
    echo ""
    log_info "NAT Gateway..."
    tf_import 'module.flip_vpc.aws_nat_gateway.this[0]' "$NAT_ID"
else
    log_warn "NAT Gateway not found"
fi

# --- Route Tables ---
echo ""
log_info "Route Tables..."
RTB_PRIVATE=$(aws_cmd ec2 describe-route-tables \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*private*" \
    --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null || echo "")
RTB_PUBLIC=$(aws_cmd ec2 describe-route-tables \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*public*" \
    --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null || echo "")

[ -n "$RTB_PRIVATE" ] && [ "$RTB_PRIVATE" != "None" ] && \
    tf_import 'module.flip_vpc.aws_route_table.private[0]' "$RTB_PRIVATE" || \
    log_warn "Private route table not found"
[ -n "$RTB_PUBLIC" ] && [ "$RTB_PUBLIC" != "None" ] && \
    tf_import 'module.flip_vpc.aws_route_table.public[0]' "$RTB_PUBLIC" || \
    log_warn "Public route table not found"

# --- Routes ---
echo ""
log_info "Routes..."
[ -n "$RTB_PUBLIC" ] && [ "$RTB_PUBLIC" != "None" ] && \
    tf_import 'module.flip_vpc.aws_route.public_internet_gateway[0]' "${RTB_PUBLIC}_0.0.0.0/0" || true
[ -n "$RTB_PRIVATE" ] && [ "$RTB_PRIVATE" != "None" ] && \
    tf_import 'module.flip_vpc.aws_route.private_nat_gateway[0]' "${RTB_PRIVATE}_0.0.0.0/0" || true

# --- Route Table Associations ---
echo ""
log_info "Route Table Associations..."
if [ -n "$RTB_PRIVATE" ] && [ "$RTB_PRIVATE" != "None" ]; then
    PRIVATE_ASSOCS=($(aws_cmd ec2 describe-route-tables \
        --route-table-ids "$RTB_PRIVATE" \
        --query 'RouteTables[0].Associations[?SubnetId!=null].RouteTableAssociationId' \
        --output text 2>/dev/null || echo ""))
    for i in "${!PRIVATE_ASSOCS[@]}"; do
        tf_import "module.flip_vpc.aws_route_table_association.private[$i]" "${PRIVATE_ASSOCS[$i]}"
    done
fi
if [ -n "$RTB_PUBLIC" ] && [ "$RTB_PUBLIC" != "None" ]; then
    PUBLIC_ASSOCS=($(aws_cmd ec2 describe-route-tables \
        --route-table-ids "$RTB_PUBLIC" \
        --query 'RouteTables[0].Associations[?SubnetId!=null].RouteTableAssociationId' \
        --output text 2>/dev/null || echo ""))
    for i in "${!PUBLIC_ASSOCS[@]}"; do
        tf_import "module.flip_vpc.aws_route_table_association.public[$i]" "${PUBLIC_ASSOCS[$i]}"
    done
fi

echo ""
log_success "VPC infrastructure imported!"
