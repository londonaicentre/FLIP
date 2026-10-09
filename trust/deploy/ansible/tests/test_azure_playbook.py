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


def test_swarm_is_initialised_idempotently():
    tasks = _play()["tasks"]
    swarm = [t for t in tasks if "swarm init" in str(t.get("ansible.builtin.command", t.get("command", "")))]
    assert len(swarm) == 1, "exactly one swarm init task"
    assert "when" in swarm[0], "swarm init must be guarded so a reprovision does not fail"


def test_images_tree_matches_the_selftest_slot():
    assert _play()["vars"]["flip_images_base_dirs"] == ["{{ flip_dir }}/data/trust-1"]


def test_yq_is_pinned_and_checksummed():
    # The NVFLARE dev-kit provisioning (generate-project-yaml.sh, restructure-lib.sh) runs
    # mikefarah's Go yq v4 on the host; the first live self-test failed without it.
    play = _play()
    assert str(play["vars"]["yq_version"]).count(".") == 2
    sha = str(play["vars"]["yq_sha256"])
    assert len(sha) == 64, "a full SHA-256"
    assert all(c in "0123456789abcdef" for c in sha)
    tasks = [t for t in play["tasks"] if "yq" in t["name"]]
    assert len(tasks) == 1, "exactly one yq install task"
    get_url = tasks[0].get("ansible.builtin.get_url") or {}
    assert "mikefarah/yq/releases/download/v{{ yq_version }}/yq_linux_amd64" in get_url.get("url", "")
    assert get_url.get("checksum") == "sha256:{{ yq_sha256 }}"
