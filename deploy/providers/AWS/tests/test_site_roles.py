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
"""Static guard: how deploy/providers/AWS/site.yml composes the shared trust-host roles (FLIP#1213 review).

The roles default to the on-prem shape; site.yml's play vars are what make an EC2 trust
different. Dropping one of them changes a real host silently, so each is pinned here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

AWS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AWS_DIR.parent.parent.parent
SITE = AWS_DIR / "site.yml"


def _play_with_role(role: str) -> dict:
    plays = [p for p in yaml.safe_load(SITE.read_text()) if role in (p.get("roles") or [])]
    assert len(plays) == 1, f"exactly one site.yml play composes {role}"
    return plays[0]


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


def test_galaxy_installs_land_outside_the_checkout() -> None:
    cfg = (AWS_DIR / "ansible.cfg").read_text()
    roles_path = next(line for line in cfg.splitlines() if line.strip().startswith("roles_path"))
    first = roles_path.split("=", 1)[1].strip().split(":")[0]
    # ansible-galaxy installs into the first roles_path entry.
    assert first.startswith("~"), f"first roles_path entry {first!r} would put galaxy roles in the checkout"


def test_galaxy_roles_are_gitignored_in_the_shared_roles_dir() -> None:
    assert "trust/deploy/ansible/roles/*.*/" in (REPO_ROOT / ".gitignore").read_text()
