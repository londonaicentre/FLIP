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
"""Static guards on aws-ec2.yml, the play for a node from trust/deploy/terraform/aws/ec2 (FLIP#1528).

Its inventory comes from that root's outputs, never the hub's, and the host itself is provisioned by
aws-ec2-host.yml, the plays the hub's site.yml imports too. How those plays compose the roles is
pinned in deploy/providers/AWS/tests/test_site_roles.py.
"""

from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).resolve().parent.parent
PLAYBOOK = ANSIBLE_DIR / "aws-ec2.yml"
HOST_PLAYS = ANSIBLE_DIR / "aws-ec2-host.yml"
ROLES_DIR = ANSIBLE_DIR / "roles"


def _plays() -> list[dict]:
    return yaml.safe_load(PLAYBOOK.read_text())


def test_inventory_comes_from_the_aws_ec2_roots_outputs_only():
    inventory = _plays()[0]
    assert inventory["hosts"] == "localhost"
    assert inventory["tags"] == "always", "the host must be in the inventory under any --tags filter"
    assert inventory["vars"]["aws_ec2_root"] == "{{ playbook_dir }}/../terraform/aws/ec2"
    text = PLAYBOOK.read_text()
    for hub_only in ("TrustEc2InstanceId", "FLIP_TFSTATE_BUCKET_NAME", "deploy/providers"):
        assert hub_only not in text, f"aws-ec2.yml reads {hub_only}: the node must not depend on the hub"


def test_the_node_joins_the_trust_ec2_group_the_host_plays_target():
    [add_host] = [t["add_host"] for t in _plays()[0]["tasks"] if "add_host" in t]
    assert add_host["groups"] == ["trust_ec2"]
    assert add_host["name"] == "{{ node_alias }}", "the alias the Makefile's ssh-config writes"
    hosts = {p["hosts"] for p in yaml.safe_load(HOST_PLAYS.read_text())}
    assert hosts == {"trust_ec2"}, "aws-ec2-host.yml provisions the trust host only"


def test_it_imports_the_shared_host_plays():
    assert _plays()[-1] == {"import_playbook": "aws-ec2-host.yml"}


# The host's own upkeep (upgrade, reboot) and role includes are the only tasks a play may carry.
UPKEEP_MODULES = {"include_role", "apt", "stat", "reboot"}


def test_every_host_play_composes_roles_only():
    for play in yaml.safe_load(HOST_PLAYS.read_text()):
        tasks = play.get("tasks") or []
        roles = play.get("roles") or []
        inline = [t for t in tasks if not UPKEEP_MODULES & set(t)]
        assert roles or tasks, play["name"]
        assert not inline, f"{play['name']}: move these tasks into a role: {inline}"
        for role in roles:
            assert (ROLES_DIR / role / "tasks/main.yml").is_file(), role


def test_the_s3_kit_source_is_required_only_while_it_is_used():
    [assert_s3] = [t for t in _plays()[0]["tasks"] if t["name"] == "ensure the S3 kit source is named"]
    assert assert_s3["when"] == "(fl_kit_source | default('s3')) == 's3'"
