#!/usr/bin/env python3
#
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

"""Update SSH config entries to use SSH-over-SSM Session Manager.

This script updates Host entries for `flip` and `flip-trust` in ~/.ssh/config.
The HostName is set to the EC2 instance ID and ProxyCommand is configured to
tunnel SSH via AWS Systems Manager Session Manager.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import click


@dataclass(frozen=True)
class HostConfig:
    """Desired SSH config for a managed host."""

    alias: str
    instance_output: str


HOST_CONFIGS: tuple[HostConfig, ...] = (
    HostConfig(alias="flip", instance_output="Ec2InstanceId"),
    HostConfig(alias="flip-trust", instance_output="TrustEc2InstanceId"),
)


def _run(cmd: list[str], timeout: int = 20) -> str:
    """Run a shell command and return stripped stdout."""
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except FileNotFoundError as exc:
        cmd_name = cmd[0] if cmd else "<unknown>"
        raise click.ClickException(
            f"Required command '{cmd_name}' was not found. Please ensure it is installed and on your PATH."
        ) from exc


def _terraform_output(name: str) -> str:
    """Read a required Terraform output value."""
    value = _run(["terraform", "output", "-raw", name])
    if not value or value == "null":
        raise click.ClickException(f"Terraform output {name} is empty or null.")
    return value


def _build_host_block(alias: str, instance_id: str, region: str, profile: str | None = None) -> str:
    """Return a managed SSH config block for one host alias."""
    profile_arg = f" --profile {profile}" if profile else ""
    proxy_command = (
        "    ProxyCommand aws ssm start-session --target %h --document-name "
        "AWS-StartSSHSession --parameters 'portNumber=%p' --region "
        f"{region}{profile_arg}"
    )
    return (
        "# Managed by FLIP - SSH over SSM Session Manager\n"
        f"Host {alias}\n"
        f"    HostName {instance_id}\n"
        "    User ubuntu\n"
        "    IdentitiesOnly yes\n"
        "    IdentityFile ~/.ssh/host-aws\n"
        "    StrictHostKeyChecking accept-new\n"
        f"{proxy_command}\n"
        "    ControlMaster auto\n"
        f"    ControlPath ~/.ssh/cm-{alias}-%r@%h:%p\n"
        "    ControlPersist 10m\n"
    )


def _replace_or_append_host_block(content: str, alias: str, new_block: str) -> str:
    """Replace Host block for alias if present; otherwise append it.

    Matches the optional comment line (# Managed by FLIP...), the Host line, and all
    indented configuration lines that follow.
    """
    # Match: optional comment line + Host line + indented config lines
    # (?:^# Managed by FLIP.*\n)? - optional comment line
    # ^Host\s+{alias}\n - Host line
    # (?:^[ \t][^\n]*\n)* - indented config lines
    host_regex = re.compile(rf"(?m)(?:^# Managed by FLIP[^\n]*\n)?^Host\s+{re.escape(alias)}\n(?:^[ \t][^\n]*\n)*")
    if host_regex.search(content):
        return host_regex.sub(new_block, content, count=1)
    suffix = "" if content.endswith("\n") else "\n"
    return f"{content}{suffix}\n{new_block}"


@click.command()
@click.option(
    "--ssh-config",
    type=click.Path(path_type=Path),
    default=Path.home() / ".ssh" / "config",
    show_default=True,
    help="Path to the SSH config file.",
)
@click.option(
    "--terraform-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd(),
    show_default=True,
    help="Directory containing Terraform state/outputs.",
)
@click.option("--dry-run", is_flag=True, help="Print proposed changes without writing files.")
@click.option(
    "--aws-profile",
    default=None,
    type=str,
    help="AWS profile to use. Defaults to current AWS_PROFILE env var when set.",
)
def main(ssh_config: Path, terraform_dir: Path, dry_run: bool, aws_profile: str | None) -> None:
    """Update ~/.ssh/config for SSH-over-SSM access to FLIP EC2 instances."""
    resolved_profile = aws_profile or os.environ.get("AWS_PROFILE")
    if aws_profile:
        os.environ["AWS_PROFILE"] = aws_profile
    region = os.environ.get("AWS_REGION", "eu-west-2")

    original_cwd = Path.cwd()
    try:
        os.chdir(terraform_dir)
        _run(["terraform", "version"], timeout=10)
        _run(["aws", "--version"], timeout=10)

        ssh_config.parent.mkdir(parents=True, exist_ok=True)
        current_content = ssh_config.read_text(encoding="utf-8") if ssh_config.exists() else ""

        updated_content = current_content
        for host in HOST_CONFIGS:
            instance_id = _terraform_output(host.instance_output)
            new_block = _build_host_block(host.alias, instance_id, region, resolved_profile)
            updated_content = _replace_or_append_host_block(updated_content, host.alias, new_block)

        if updated_content == current_content:
            click.echo("SSH config already up to date.")
            return

        if dry_run:
            click.echo(updated_content)
            return

        if ssh_config.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = ssh_config.parent / f"{ssh_config.name}.backup.{timestamp}"
            shutil.copy2(ssh_config, backup)
            click.echo(f"Backup created: {backup}")

        ssh_config.write_text(updated_content, encoding="utf-8")
        click.echo(f"Updated SSH config: {ssh_config}")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        raise click.ClickException(stderr) from exc
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException(f"Command timed out: {exc.cmd}") from exc
    finally:
        os.chdir(original_cwd)


if __name__ == "__main__":
    try:
        main()
    except click.ClickException as exc:
        click.echo(f"ERROR: {exc.message}", err=True)
        sys.exit(1)
