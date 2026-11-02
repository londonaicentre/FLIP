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

"""Static guards on the EC2 trust seed running against a stopped stack (FLIP#1190 review).

The seed play starts a throwaway omop-db on ``/opt/flip/omop/db_data``. If the trust stack is
still up, its omop-db has that directory open — and Postgres does not refuse the second
postmaster, because the first one's PID is invisible from the new container's PID namespace
and is taken as stale, so crash recovery runs over a live database.

Two things hold it. The EC2 root's ``deploy`` seeds with the stack stopped (pinned in
trust/deploy/terraform/aws/ec2/tests/test_makefile.py), and the seed role refuses, before its first
``docker run``, while any container on the host has the data directory open. The second is asserted
here over the source, since nothing in CI runs the play against a host.
"""

import re
from pathlib import Path

SEED_TASKS = Path(__file__).resolve().parent.parent / "roles/flip_trust_seed/tasks/main.yml"

HOLDERS_TASK = "- name: check nothing on this host has the OMOP data directory open\n"
REFUSAL_TASK = "- name: refuse to seed while the trust's own cluster has the data directory open\n"


def _seed_play() -> str:
    """The seed play's tasks: the flip_trust_seed role, which aws-ec2-host.yml composes."""
    return SEED_TASKS.read_text()


def test_seed_play_refuses_before_its_first_docker_run():
    play = _seed_play()
    holders = play.find(HOLDERS_TASK)
    refusal = play.find(REFUSAL_TASK)
    first_run = play.find("docker run")
    assert holders != -1, "the holders check is gone from the seed play"
    assert refusal != -1, "the refusal is gone from the seed play"
    assert holders < refusal < first_run, "the guard must run before the throwaway containers start"


def test_the_guard_looks_at_every_omop_db_on_the_host():
    """The data directory is host-global: a stack under another compose project holds it too."""
    play = _seed_play()
    task = play[play.find(HOLDERS_TASK) : play.find(REFUSAL_TASK)]
    assert "label=com.docker.compose.service=omop-db" in task
    assert "com.docker.compose.project=" not in task, "scoping the filter to one project reopens the hole"
    assert "changed_when: false" in task


def test_the_refusal_fails_on_any_holder():
    play = _seed_play()
    task = play[play.find(REFUSAL_TASK) :]
    task = task[: task.find("\n- name: ")]
    assert "assert:" in task
    assert re.search(r"omop_dir_holders\.stdout\s*\|\s*trim\s*\|\s*length\s*==\s*0", task)
    assert "down-trust-ec2" in task, "the fail_msg must tell the operator what to run"
