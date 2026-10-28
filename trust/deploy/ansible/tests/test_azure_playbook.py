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
"""Static guard: trust/deploy/ansible/azure.yml, the play an Azure node runs on itself (FLIP#1390).

The node has no AWS credentials and no inbound access, so the play must compose only the
AWS-free roles, never grant the docker group, pin the uv it installs, and leave a swarm
running for XNAT.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ANSIBLE_DIR = Path(__file__).resolve().parent.parent
PLAY = ANSIBLE_DIR / "azure.yml"


def _play() -> dict:
    plays = yaml.safe_load(PLAY.read_text())
    assert isinstance(plays, list), "azure.yml holds a list of plays"
    assert len(plays) == 1, "azure.yml holds exactly one play"
    return plays[0]


def test_runs_locally_as_root():
    play = _play()
    assert play["connection"] == "local"
    assert play["become"] is True


def test_composes_only_aws_free_roles():
    roles = [r if isinstance(r, str) else r["role"] for r in _play()["roles"]]
    assert roles == [
        "flip_base_packages",
        "flip_docker",
        "flip_trust_dirs",
        "flip_fl_kit",
        "flip_observability_config",
    ]
    assert "flip_omop_vocab" not in roles, "the OMOP vocab role fetches from S3"


def test_fl_kit_is_not_fetched_from_s3():
    assert _play()["vars"]["fl_kit_source"] in {"none", "precreate"}


def test_no_aws_anywhere():
    text = PLAY.read_text().lower()
    for needle in ("aws ", "s3://", "aws_", "amazonaws"):
        assert needle not in text, f"azure.yml mentions {needle!r}"


def test_no_docker_group_grant():
    play_vars = _play()["vars"]
    assert "flip_docker_users" not in play_vars
    assert "docker_users" not in play_vars


def test_uv_is_pinned():
    play_vars = _play()["vars"]
    version = str(play_vars["uv_version"])
    assert version.count(".") == 2, version
    assert version.replace(".", "").isdigit(), version
    names = [t["name"] for t in _play()["tasks"]]
    assert any("uv" in n for n in names)


def test_uv_install_enforces_the_pin_on_reprovision():
    """`creates:` would skip the task once any uv exists, so a uv_version bump never landed."""
    task = next(t for t in _play()["tasks"] if t["name"].startswith("Install uv"))
    assert "creates" not in task.get("args", {}), "the pin must be checked by version, not by file presence"
    script = task["ansible.builtin.shell"]
    assert "/usr/local/bin/uv --version" in script
    assert "{{ uv_version | regex_escape }}" in script
    assert task.get("changed_when"), "an already-pinned uv must not report a change"


def test_swarm_is_initialised_idempotently():
    tasks = _play()["tasks"]
    swarm = [t for t in tasks if "swarm init" in str(t.get("ansible.builtin.command", t.get("command", "")))]
    assert len(swarm) == 1, "exactly one swarm init task"
    assert "when" in swarm[0], "swarm init must be guarded so a reprovision does not fail"


def test_images_trees_are_the_selftest_slot_and_the_joined_trust():
    """flip-node join points the kit's BASE_IMAGES_DOWNLOAD_DIR at data/images: made by the play, not root."""
    assert _play()["vars"]["flip_images_base_dirs"] == ["{{ flip_dir }}/data/trust-1", "{{ flip_dir }}/data/images"]
    flip_node = ANSIBLE_DIR.parents[2] / "deploy" / "providers" / "azure" / "vm" / "templates" / "flip-node.sh"
    assert "BASE_IMAGES_DOWNLOAD_DIR=${FLIP_DIR}/data/images\n" in flip_node.read_text()


def test_no_fl_kit_provisioning_tooling_on_the_node():
    # A trust node receives its slot's FL kit (the kit drop); it never provisions one, so the
    # development tooling provisioning needs (mikefarah's yq) has no place on it (FLIP#1390).
    play = _play()
    assert not [k for k in play.get("vars", {}) if k.startswith("yq_")]
    assert not [t for t in play["tasks"] if "yq" in t["name"].lower()]
