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
"""Write the ~/.ssh/config entry that reaches a trust node over SSM Session Manager (FLIP#1528).

The node has no inbound rule, so SSH, Ansible and the Docker ssh context all go through
``aws ssm start-session``. The entry is keyed on the node's alias and replaced in place on a re-run;
other entries, including the hub's own (deploy/providers/AWS/update_ssm_ssh_config.py, same block
format), are left alone.

Usage:
    python3 ssh_config.py --alias flip-stag-gstt --instance-id i-0abc --region eu-west-2 \
        --key ~/.ssh/host-aws [--profile lza-stag] [--config ~/.ssh/config]
"""

import argparse
import os
import re
from pathlib import Path


def host_block(alias: str, instance_id: str, region: str, key: str, profile: str = "") -> str:
    """Return the managed SSH config block for one node."""
    profile_arg = f" --profile {profile}" if profile else ""
    return (
        "# Managed by FLIP - SSH over SSM Session Manager\n"
        f"Host {alias}\n"
        f"    HostName {instance_id}\n"
        "    User ubuntu\n"
        "    IdentitiesOnly yes\n"
        f"    IdentityFile {key}\n"
        "    StrictHostKeyChecking accept-new\n"
        "    ProxyCommand aws ssm start-session --target %h --document-name AWS-StartSSHSession"
        f" --parameters 'portNumber=%p' --region {region}{profile_arg}\n"
        "    ControlMaster auto\n"
        f"    ControlPath ~/.ssh/cm-{alias}-%r@%h:%p\n"
        "    ControlPersist 10m\n"
    )


def upsert(content: str, alias: str, block: str) -> str:
    """Replace the alias's block (with its managed comment) if present, else append it."""
    pattern = re.compile(rf"(?m)(?:^# Managed by FLIP[^\n]*\n)?^Host\s+{re.escape(alias)}\n(?:^[ \t][^\n]*\n)*")
    if pattern.search(content):
        return pattern.sub(lambda _: block, content, count=1)
    if not content:
        return block
    return f"{content}{'' if content.endswith(chr(10)) else chr(10)}\n{block}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--alias", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--key", required=True, help="private key for the node's key pair")
    parser.add_argument("--profile", default="")
    parser.add_argument("--config", type=Path, default=Path.home() / ".ssh" / "config")
    args = parser.parse_args()

    if not re.fullmatch(r"i-[0-9a-f]{8,17}", args.instance_id):
        parser.error(f"--instance-id {args.instance_id!r} is not an EC2 instance id (has the root been applied?)")
    args.config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = args.config.read_text() if args.config.exists() else ""
    updated = upsert(current, args.alias, host_block(args.alias, args.instance_id, args.region, args.key, args.profile))
    if updated != current:
        args.config.write_text(updated)
        os.chmod(args.config, 0o600)
    print(f"✅ {args.config}: Host {args.alias} -> {args.instance_id}")


if __name__ == "__main__":
    main()
