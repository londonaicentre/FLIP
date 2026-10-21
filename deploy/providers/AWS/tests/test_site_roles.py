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
"""Static guard: how the EC2 trust-host plays compose the shared roles (FLIP#1213 review, FLIP#1528).

The plays live in trust/deploy/ansible/aws-ec2-host.yml, which site.yml (the hub's own trust EC2)
and aws-ec2.yml (a node from the standalone aws-ec2 root) both import. The roles default to the
on-prem shape; the play vars are what make an EC2 trust different. Dropping one of them changes a
real host silently, so each is pinned here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

AWS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AWS_DIR.parent.parent.parent
SITE = AWS_DIR / "site.yml"
HOST_PLAYS = REPO_ROOT / "trust/deploy/ansible/aws-ec2-host.yml"


def _play_with_role(role: str) -> dict:
    plays = [p for p in yaml.safe_load(HOST_PLAYS.read_text()) if role in (p.get("roles") or [])]
    assert len(plays) == 1, f"exactly one aws-ec2-host.yml play composes {role}"
    return plays[0]


def test_site_yml_imports_the_trust_host_plays_and_has_none_of_its_own() -> None:
    plays = yaml.safe_load(SITE.read_text())
    assert {"import_playbook": "../../../trust/deploy/ansible/aws-ec2-host.yml"} in plays
    own = [p["name"] for p in plays if p.get("hosts") in ("trust_ec2", "ec2")]
    assert not own, f"site.yml provisions the trust host itself again: {own}"


def test_ec2_login_user_gets_the_docker_group() -> None:
    # deploy-trust drives the stack over an ssh docker context as ubuntu.
    assert _play_with_role("flip_docker")["vars"]["flip_docker_users"] == ["ubuntu"]


def test_ec2_stages_the_fl_kit_from_s3_under_the_flkit_tag() -> None:
    play = _play_with_role("flip_fl_kit")
    assert play["vars"]["fl_kit_source"] == "s3", "without it the role stages no kit at all"
    tags = play.get("tags") or []
    assert "flkit" in (tags if isinstance(tags, list) else [tags]), "make stage-fl-kit runs --tags flkit"


def test_ec2_dirs_cover_every_slot_and_the_observability_volumes() -> None:
    play_vars = _play_with_role("flip_trust_dirs")["vars"]
    assert play_vars["flip_observability_volume_dirs"] is True
    assert "trust_num" in str(play_vars["flip_images_base_dirs"]), "the registered slot's images tree"
    assert "/opt/flip/trust" in play_vars["flip_app_dirs"]


def test_base_packages_precede_the_unzip_consumers() -> None:
    # flip_aws_cli installs no unzip of its own: it relies on flip_base_packages having run in an
    # earlier trust_ec2 play, or its `unarchive` of the installer zip fails mid-play.
    plays = yaml.safe_load(HOST_PLAYS.read_text())
    roles_dir = REPO_ROOT / "trust/deploy/ansible/roles"

    def unarchives(play: dict) -> bool:
        tasks = list(play.get("tasks") or [])
        for role in play.get("roles") or []:
            tasks += yaml.safe_load((roles_dir / role / "tasks/main.yml").read_text()) or []
        return any("unarchive" in task for task in tasks)

    [base] = [i for i, p in enumerate(plays) if "flip_base_packages" in (p.get("roles") or [])]
    assert plays[base]["hosts"] == "trust_ec2"
    consumers = [i for i, p in enumerate(plays) if unarchives(p)]
    assert consumers, "no aws-ec2-host.yml play unarchives anything — this guard has drifted"
    assert all(base < i for i in consumers), "flip_base_packages must run before every play that unarchives"
    role_defaults = REPO_ROOT / "trust/deploy/ansible/roles/flip_base_packages/defaults/main.yml"
    defaults = yaml.safe_load(role_defaults.read_text())
    assert "unzip" in defaults["flip_base_packages"], "unarchive needs the unzip binary on the host"


def test_galaxy_installs_land_outside_the_checkout() -> None:
    cfg = (AWS_DIR / "ansible.cfg").read_text()
    roles_path = next(line for line in cfg.splitlines() if line.strip().startswith("roles_path"))
    first = roles_path.split("=", 1)[1].strip().split(":")[0]
    # ansible-galaxy installs into the first roles_path entry.
    assert first.startswith("~"), f"first roles_path entry {first!r} would put galaxy roles in the checkout"


def test_galaxy_roles_are_gitignored_in_the_shared_roles_dir() -> None:
    assert "trust/deploy/ansible/roles/*.*/" in (REPO_ROOT / ".gitignore").read_text()
