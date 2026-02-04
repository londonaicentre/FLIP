#!/usr/bin/env python3
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Update SSH config with EC2 Public IPs from Terraform.

This script updates the ~/.ssh/config file with the current EC2 public IPs
from Terraform outputs. It finds the "Host flip" and "Host flip-trust" sections
and updates their HostName values.
"""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import click

FILE_PATH = Path(__file__).resolve()


# Color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"  # No Color


def print_status(status: str, message: str) -> None:
    """Print a status message with color coding.

    Args:
        status: Status type (PASS, FAIL, INFO, WARN)
        message: Message to display
    """
    symbols = {
        "PASS": f"{Colors.GREEN}✓{Colors.NC}",
        "FAIL": f"{Colors.RED}✗{Colors.NC}",
        "INFO": f"{Colors.BLUE}ℹ{Colors.NC}",
        "WARN": f"{Colors.YELLOW}⚠{Colors.NC}",
    }
    symbol = symbols.get(status, "")
    click.echo(f"{symbol} {message}")


def check_aws_cli() -> bool:
    """Check if aws cli is installed and available.

    Returns:
        True if aws cli is available, False otherwise
    """
    try:
        subprocess.run(
            ["aws", "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_terraform() -> bool:
    """Check if terraform is installed and available.

    Returns:
        True if terraform is available, False otherwise
    """
    try:
        subprocess.run(
            ["terraform", "--version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_terraform_output(output_name: str) -> str:
    """Get a specific output value from Terraform.

    Args:
        output_name: Name of the Terraform output variable

    Returns:
        The output value or None if not found
    """
    try:
        result = subprocess.run(
            ["terraform", "output", "-raw", output_name],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        output = result.stdout.strip()
        # Use regular expression to extract IP address only
        ip_match = re.search(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", output)
        if ip_match:
            return ip_match.group(0)
        if output and output != "null":
            return output
        print_status("FAIL", f"Could not retrieve required IP {output_name} from Terraform outputs")
        exit(1)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print_status("FAIL", "Could not retrieve required IP Ec2PublicIp from Terraform outputs")
        exit(1)


def find_host_section(content: str, host_name: str, identity_file: str) -> Optional[Tuple[int, int, Optional[str]]]:
    """Find a specific host section in SSH config content.

    Args:
        content: SSH config file content
        host_name: Host name to search for (e.g., "flip")
        identity_file: Identity file to look for in the section

    Returns:
        Tuple of (start_pos, end_pos, current_hostname) or None if not found
    """
    pattern = rf"^Host {re.escape(host_name)}$"

    for match in re.finditer(pattern, content, re.MULTILINE):
        start = match.start()

        # Find the next "Host " line or end of file
        next_host = re.search(r"\n(Host \S+|\Z)", content[start + len(match.group()) :])
        if next_host:
            end = start + len(match.group()) + next_host.start() + 1
        else:
            end = len(content)

        section = content[start:end]

        # Check if this section has the correct identity file
        if identity_file in section:
            # Find HostName in this section
            hostname_match = re.search(r"^\s*HostName\s+(\S+)", section, re.MULTILINE)
            current_hostname = hostname_match.group(1) if hostname_match else None
            return (start, end, current_hostname)

    return None


def update_hostname_in_section(section: str, new_hostname: str) -> str:
    """Update or add HostName in a host section.

    Args:
        section: The host section content
        new_hostname: New hostname to set

    Returns:
        Updated section content
    """
    # Check if HostName already exists in this section
    hostname_match = re.search(r"^(\s*)HostName\s+\S+", section, re.MULTILINE)

    if hostname_match:
        # Replace existing HostName
        new_section = re.sub(
            r"^(\s*)HostName\s+\S+",
            rf"\1HostName {new_hostname}",
            section,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        # Add HostName after "Host" line
        lines = section.split("\n", 1)
        if len(lines) > 1:
            new_section = f"{lines[0]}\n    HostName {new_hostname}\n{lines[1]}"
        else:
            new_section = f"{lines[0]}\n    HostName {new_hostname}\n"

    return new_section


def add_ssh_host_key(hostname: str) -> bool:
    """Add SSH host key to known_hosts.

    Args:
        hostname: Hostname or IP address

    Returns:
        True if successful, False otherwise
    """
    try:
        known_hosts = Path.home() / ".ssh" / "known_hosts"
        result = subprocess.run(
            ["ssh-keyscan", "-H", hostname],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        if result.stdout:
            with open(known_hosts, "a") as f:
                f.write(result.stdout)
            return True
        return False
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IOError):
        return False


def verify_instance_is_running(hostname: str) -> bool:
    """Verify if an EC2 instance is running using its public IP.

    Args:
        hostname: Public IP address of the EC2 instance

    Returns:
        True if the instance is running, False otherwise
    """
    if not hostname:
        return False
    try:
        result = subprocess.run(
            [
                "aws",
                "ec2",
                "describe-instances",
                "--filters",
                f"Name=ip-address,Values={hostname}",
                "--query",
                "Reservations[*].Instances[*].State.Name",
                "--output",
                "text",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return "running" in result.stdout.strip().lower()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def verify_if_ssh_can_connect(hostname: str) -> bool:
    """Verify if SSH can connect to the given hostname.

    Args:
        hostname: Hostname or IP address
    Returns:
        True if SSH can connect, False otherwise
    """
    try:
        result = subprocess.run(
            ["ssh", f"ubuntu@{hostname}", "echo", "connected"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return "connected" in result.stdout.strip().lower()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@click.command()
@click.option(
    "--ssh-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path.home() / ".ssh" / "config",
    help="Path to SSH config file",
)
@click.option(
    "--terraform-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd(),
    help="Directory containing terraform.tfstate",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be changed without making changes",
)
@click.option(
    "--aws-profile",
    type=str,
    default="default",
    help="AWS CLI profile to use",
)
def main(
    ssh_config: Path,
    terraform_dir: Path,
    dry_run: bool,
    aws_profile: str,
) -> None:
    """Update SSH config with EC2 Public IPs from Terraform outputs.

    This script updates the ~/.ssh/config file with current EC2 public IPs
    from Terraform outputs. It finds "Host flip" and "Host flip-trust" sections
    and updates their HostName values.
    """
    # Set AWS_PROFILE environment variable
    os.environ["AWS_PROFILE"] = aws_profile

    # Change to terraform directory
    original_dir = Path.cwd()
    try:
        os.chdir(terraform_dir)
    except OSError as e:
        print_status("FAIL", f"Could not change to terraform directory: {e}")
        sys.exit(1)

    # Check prerequisites
    if not check_terraform():
        print_status("FAIL", "terraform not found. Please install it.")
        sys.exit(1)

    if not check_aws_cli():
        print_status("FAIL", "aws cli not found. Please install it.")
        sys.exit(1)

    if not ssh_config.exists():
        print_status("FAIL", f"SSH config file not found at {ssh_config}")
        sys.exit(1)

    EC2_INSTANCES: dict[str, str] = {
        "flip": "Ec2PublicIp",
        "flip-trust": "TrustEc2PublicIp",
    }
    for service_name, instance_ip_name in EC2_INSTANCES.items():
        public_ip = get_terraform_output(instance_ip_name)

        # Read SSH config
        try:
            content = ssh_config.read_text()
        except IOError as e:
            print_status("FAIL", f"Could not read SSH config: {e}")
            sys.exit(1)

        # Track changes
        changes_made = False
        new_content = content

        if not re.search(rf"^Host {service_name}$", content, re.MULTILINE):
            print_status("WARN", f"'Host {service_name}' section not found in SSH config")
            # Append section to the new_content
            click.echo(f"\nAdding a new section for '{service_name}':")
            new_section_content = (
                f"\nHost {service_name}\n"
                f"    HostName {public_ip}\n"
                "    User ubuntu\n"
                "    IdentitiesOnly yes\n"
                "    IdentityFile ~/.ssh/host-aws\n"
            )
            new_content += new_section_content
            print_status("PASS", f"'{service_name}' section will be added to SSH config: {ssh_config}")
            changes_made = True

        flip_section = find_host_section(content, service_name, "host-aws")
        if flip_section:
            start, end, current_ip = flip_section
            if current_ip:
                print_status("INFO", f"Current '{service_name}' HostName: {current_ip}")
                if current_ip == public_ip:
                    print_status("PASS", f"'{service_name}' SSH config is already up to date!")
                else:
                    section_content = content[start:end]
                    updated_section = update_hostname_in_section(section_content, public_ip)
                    new_content = new_content[:start] + updated_section + new_content[end:]
                    print_status("INFO", f"Updating '{service_name}' HostName from {current_ip} to {public_ip}")
                    changes_made = True
            else:
                print_status("WARN", "HostName not found in 'Host flip' section, adding it")
                section_content = content[start:end]
                updated_section = update_hostname_in_section(section_content, public_ip)
                new_content = new_content[:start] + updated_section + new_content[end:]
                changes_made = True
        else:
            print_status("WARN", f"'Host {service_name}' section found but no matching IdentityFile ~/.ssh/host-aws")

        # Apply changes if needed
        if changes_made:
            if dry_run:
                print_status("INFO", "Dry run mode - no changes made")
                click.echo("\nProposed changes:")
                click.echo("=" * 60)
                click.echo(new_content)
                click.echo("=" * 60)
            else:
                # Create backup
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = ssh_config.parent / f"{ssh_config.name}.backup.{timestamp}"
                try:
                    shutil.copy2(ssh_config, backup_file)
                    print_status("PASS", f"Created backup: {backup_file}")
                except IOError as e:
                    print_status("WARN", f"Could not create backup: {e}")

                # Write updated content
                try:
                    ssh_config.write_text(new_content)
                    print_status("PASS", "Successfully updated SSH config!")
                except IOError as e:
                    print_status("FAIL", f"Could not write SSH config: {e}")
                    sys.exit(1)
        add_ssh_host_key(public_ip)

        # Wait until instances are running
        # TODO: Review why this is not working as expected - currently it times out for the trust instance unnecessarily
        # click.echo("Verifying instances are running...")
        # first_try_attempt = datetime.now()
        # click.echo(f"Waiting for instance with IP {service_name} at {public_ip} to be in 'running' state...")
        # while not verify_instance_is_running(public_ip) or not verify_if_ssh_can_connect(public_ip):
        #     click.echo(f"Waiting for instance with IP {service_name} at {public_ip} to be in 'running' state...")
        #     sleep(10)
        #     # Timeout after 10 minutes
        #     if (datetime.now() - first_try_attempt).total_seconds() > 600:
        #         print_status("FAIL", f"Timeout waiting for instance {service_name} {public_ip} to be running")
        #         sys.exit(1)
        # print_status("PASS", f"Instance with IP {public_ip} is running!")
        # click.echo(f"Waiting for instance with IP {public_ip} to be in 'running' state...")

    # Summary
    click.echo()
    click.echo(f"{Colors.GREEN}✓ SSH config update complete!{Colors.NC}")
    click.echo(f"{Colors.BLUE}You can now connect with:{Colors.NC}")
    for service_name in EC2_INSTANCES.keys():
        click.echo(f"  ssh {service_name}")
    click.echo()

    # Restore original directory
    os.chdir(original_dir)


if __name__ == "__main__":
    main()
