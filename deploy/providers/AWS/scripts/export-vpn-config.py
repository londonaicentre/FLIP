#!/usr/bin/env python3

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

"""
VPN Configuration Export Script

Exports Site-to-Site VPN connection details from AWS to a configuration file
suitable for on-premises IT teams to configure their VPN endpoint device
(e.g., Palo Alto PA-5410).

Usage:
    python3 export-vpn-config.py --vpn-id vpn-xxxxxxxxx --output palo-alto-vpn-config.txt
    python3 export-vpn-config.py --vpn-id vpn-xxxxxxxxx --format json --output vpn-config.json
"""

import argparse
import json
import sys
from datetime import datetime

import boto3
from botocore.exceptions import ClientError


def get_vpn_connection_details(vpn_id: str, region: str) -> dict:
    """Retrieve VPN connection details from AWS."""
    ec2_client = boto3.client("ec2", region_name=region)

    try:
        response = ec2_client.describe_vpn_connections(VpnConnectionIds=[vpn_id])
    except ClientError as e:
        print(f"Error: Failed to retrieve VPN connection: {e}", file=sys.stderr)
        sys.exit(1)

    if not response["VpnConnections"]:
        print(f"Error: VPN connection {vpn_id} not found", file=sys.stderr)
        sys.exit(1)

    return response["VpnConnections"][0]


def get_customer_gateway(cgw_id: str, region: str) -> dict:
    """Retrieve Customer Gateway details from AWS."""
    ec2_client = boto3.client("ec2", region_name=region)

    try:
        response = ec2_client.describe_customer_gateways(CustomerGatewayIds=[cgw_id])
    except ClientError as e:
        print(f"Error: Failed to retrieve Customer Gateway: {e}", file=sys.stderr)
        sys.exit(1)

    if not response["CustomerGateways"]:
        print(f"Error: Customer Gateway {cgw_id} not found", file=sys.stderr)
        sys.exit(1)

    return response["CustomerGateways"][0]


def format_palo_alto_config(vpn_details: dict, cgw_details: dict) -> str:
    """Format VPN configuration for Palo Alto PA-5410."""
    output = []
    output.append("=" * 80)
    output.append("FLIP SITE-TO-SITE VPN CONFIGURATION FOR PALO ALTO PA-5410")
    output.append("=" * 80)
    output.append("")
    output.append(f"Export Date: {datetime.now().isoformat()}")
    output.append(f"VPN Connection ID: {vpn_details['VpnConnectionId']}")
    output.append(f"Customer Gateway ID: {cgw_details['CustomerGatewayId']}")
    output.append(f"On-Premises Public IP: {cgw_details['PublicIp']}")
    output.append("")

    output.append("TUNNEL 1 CONFIGURATION")
    output.append("-" * 80)
    tunnel1 = vpn_details["VpnTunnelOptions"][0]
    output.append(f"AWS Tunnel Endpoint (VGW): {tunnel1['TunnelAddress']}")
    output.append(f"AWS Inside IP (BGP): {tunnel1['VgwInsideAddress']}")
    output.append(f"On-Premises Inside IP (BGP): {tunnel1['CustomerGatewayAddress']}")
    output.append(f"Pre-Shared Key (PSK): {tunnel1['PreSharedKey']}")
    output.append("")
    output.append("Phase 1 (IKE) Configuration:")
    phase1 = tunnel1.get("Phase1Details", {})
    output.append(f"  - Encryption: {phase1.get('EncryptionAlgorithms', ['AES-128', 'AES-256'])}")
    output.append(f"  - Integrity: {phase1.get('IntegrityAlgorithms', ['SHA1', 'SHA2-256'])}")
    output.append(f"  - DH Group: {phase1.get('DHGroupNumbers', [2, 14])}")
    output.append(f"  - Lifetime: {phase1.get('Lifetime', 28800)} seconds")
    output.append("")
    output.append("Phase 2 (ESP) Configuration:")
    phase2 = tunnel1.get("Phase2Details", {})
    output.append(f"  - Encryption: {phase2.get('EncryptionAlgorithms', ['AES-128', 'AES-256'])}")
    output.append(f"  - Integrity: {phase2.get('IntegrityAlgorithms', ['SHA1', 'SHA2-256'])}")
    output.append(f"  - Lifetime: {phase2.get('Lifetime', 3600)} seconds")
    output.append("")

    output.append("TUNNEL 2 CONFIGURATION (REDUNDANT)")
    output.append("-" * 80)
    tunnel2 = vpn_details["VpnTunnelOptions"][1]
    output.append(f"AWS Tunnel Endpoint (VGW): {tunnel2['TunnelAddress']}")
    output.append(f"AWS Inside IP (BGP): {tunnel2['VgwInsideAddress']}")
    output.append(f"On-Premises Inside IP (BGP): {tunnel2['CustomerGatewayAddress']}")
    output.append(f"Pre-Shared Key (PSK): {tunnel2['PreSharedKey']}")
    output.append("")
    output.append("Phase 1 (IKE) Configuration:")
    phase1 = tunnel2.get("Phase1Details", {})
    output.append(f"  - Encryption: {phase1.get('EncryptionAlgorithms', ['AES-128', 'AES-256'])}")
    output.append(f"  - Integrity: {phase1.get('IntegrityAlgorithms', ['SHA1', 'SHA2-256'])}")
    output.append(f"  - DH Group: {phase1.get('DHGroupNumbers', [2, 14])}")
    output.append(f"  - Lifetime: {phase1.get('Lifetime', 28800)} seconds")
    output.append("")
    output.append("Phase 2 (ESP) Configuration:")
    phase2 = tunnel2.get("Phase2Details", {})
    output.append(f"  - Encryption: {phase2.get('EncryptionAlgorithms', ['AES-128', 'AES-256'])}")
    output.append(f"  - Integrity: {phase2.get('IntegrityAlgorithms', ['SHA1', 'SHA2-256'])}")
    output.append(f"  - Lifetime: {phase2.get('Lifetime', 3600)} seconds")
    output.append("")

    output.append("PA-5410 FIREWALL CONFIGURATION STEPS")
    output.append("-" * 80)
    output.append("1. Access PA-5410 Web Interface (https://<management-ip>)")
    output.append("2. Navigate to Network → IKE Gateway → Add New")
    output.append("   Name: flip-aws-tunnel1")
    output.append("   IKE Version: IKEv1")
    output.append(f"   Peer IP: {tunnel1['TunnelAddress']}")
    output.append(f"   Pre-shared key: {tunnel1['PreSharedKey']}")
    output.append("   DH Group: Group 14 (2048-bit MODP)")
    output.append("3. Configure IPsec Crypto Profile (Network → Crypto → IPsec Crypto)")
    output.append("   Name: flip-ipsec-profile-1")
    output.append("   Encryption: AES-256")
    output.append("   Authentication: SHA2-256")
    output.append("4. Configure IPsec Tunnel (Network → IPsec Tunnel → Add New)")
    output.append("   Name: flip-vpn-tunnel1")
    output.append("   Tunnel Interface: tunnel.1")
    output.append("   IKE Gateway: flip-aws-tunnel1")
    output.append("   IPsec Crypto Profile: flip-ipsec-profile-1")
    output.append("5. Configure Static Route (Network → Virtual Router → Static Routes)")
    output.append("   Destination: 10.0.0.0/16 (AWS VPC CIDR)")
    output.append("   Next Hop: tunnel.1")
    output.append("6. Configure Security Rule (Policies → Security → Add)")
    output.append("   Source Zone: trust")
    output.append("   Source Address: any")
    output.append("   Destination Zone: untrust")
    output.append("   Destination Address: any")
    output.append("   Service: any")
    output.append("   Action: Allow")
    output.append("")
    output.append("IMPORTANT NOTES")
    output.append("-" * 80)
    output.append("- Dual tunnels are configured for redundancy (automatic failover)")
    output.append("- Both tunnels should be configured on PA-5410 for full HA capability")
    output.append("- Pre-Shared Keys (PSKs) are sensitive - store securely")
    output.append("- Test ping from on-premises to AWS VPC CIDR after configuration")
    output.append("- Monitor tunnel status in PA-5410: Monitor → IPsec Tunnels")
    output.append("")

    return "\n".join(output)


def format_json_config(vpn_details: dict, cgw_details: dict) -> str:
    """Format VPN configuration as JSON for programmatic use."""
    config = {
        "export_date": datetime.now().isoformat(),
        "vpn_connection_id": vpn_details["VpnConnectionId"],
        "customer_gateway_id": cgw_details["CustomerGatewayId"],
        "on_premises_public_ip": cgw_details["PublicIp"],
        "tunnels": [],
    }

    for i, tunnel_options in enumerate(vpn_details["VpnTunnelOptions"]):
        tunnel = {
            "tunnel_number": i + 1,
            "aws_endpoint": tunnel_options["TunnelAddress"],
            "aws_inside_ip": tunnel_options["VgwInsideAddress"],
            "on_prem_inside_ip": tunnel_options["CustomerGatewayAddress"],
            "preshared_key": tunnel_options["PreSharedKey"],
            "phase1": tunnel_options.get("Phase1Details", {}),
            "phase2": tunnel_options.get("Phase2Details", {}),
        }
        config["tunnels"].append(tunnel)

    return json.dumps(config, indent=2)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Export FLIP VPN configuration from AWS for on-premises firewall setup"
    )
    parser.add_argument("--vpn-id", required=True, help="VPN Connection ID (e.g., vpn-xxxxxxxxx)")
    parser.add_argument("--region", default="eu-west-2", help="AWS region (default: eu-west-2)")
    parser.add_argument(
        "--format",
        choices=["palo-alto", "json"],
        default="palo-alto",
        help="Output format (default: palo-alto)",
    )
    parser.add_argument("--output", required=False, help="Output file (default: stdout)")

    args = parser.parse_args()

    # Retrieve VPN details from AWS
    print(f"Retrieving VPN configuration for {args.vpn_id}...", file=sys.stderr)
    vpn_details = get_vpn_connection_details(args.vpn_id, args.region)

    cgw_id = vpn_details["CustomerGatewayId"]
    cgw_details = get_customer_gateway(cgw_id, args.region)

    # Format configuration
    if args.format == "json":
        config_text = format_json_config(vpn_details, cgw_details)
    else:
        config_text = format_palo_alto_config(vpn_details, cgw_details)

    # Output
    if args.output:
        with open(args.output, "w") as f:
            f.write(config_text)
        print(f"Configuration written to {args.output}", file=sys.stderr)
    else:
        print(config_text)


if __name__ == "__main__":
    main()
