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
and is taken as stale, so crash recovery runs over a live database. ``deploy-trust`` used to
reach the play that way on every re-deploy (``seed-trust-data`` was a prerequisite, the stack
came down only in the recipe).

Two things now hold it, and both are asserted over the source since nothing in CI runs the
Makefile or the play against a host:

* ``deploy-trust`` seeds in its recipe, after ``down-trust-ec2`` and before ``up-trust-ec2``,
  and no longer lists ``seed-trust-data`` as a prerequisite.
* The seed play refuses, before its first ``docker run``, while any container on the host has
  the data directory open.
"""

import re
from pathlib import Path

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
MAKEFILE = AWS_PROVIDER_DIR / "Makefile"
SITE_YML = AWS_PROVIDER_DIR / "site.yml"

SEED_PLAY = "- name: seed the trust's OMOP database and Orthanc from the canonical dataset on Trust EC2\n"
HOLDERS_TASK = "- name: check nothing on this host has the OMOP data directory open\n"
REFUSAL_TASK = "- name: refuse to seed while the trust's own cluster has the data directory open\n"


def _deploy_trust_rule() -> tuple[str, list[str]]:
    """Return ``deploy-trust``'s prerequisite line and its recipe lines (comments dropped)."""
    text = MAKEFILE.read_text()
    match = re.search(r"^deploy-trust:(?P<prereqs>[^\n]*)\n(?P<recipe>(?:\t[^\n]*\n)+)", text, re.MULTILINE)
    assert match, "no deploy-trust rule in the AWS Makefile — this guard has drifted"
    recipe = [line for line in match.group("recipe").splitlines() if not line.lstrip("\t").startswith("@#")]
    return match.group("prereqs"), recipe


def _first_index(lines: list[str], needle: str) -> int:
    for index, line in enumerate(lines):
        if needle in line:
            return index
    raise AssertionError(f"{needle!r} is not in deploy-trust's recipe")


def test_seed_is_not_a_prerequisite_of_deploy_trust():
    """As a prerequisite it ran before the recipe, i.e. before the stack was stopped."""
    prereqs, _ = _deploy_trust_rule()
    assert "seed-trust-data" not in prereqs.split("##")[0].split()


def test_deploy_trust_seeds_between_stopping_and_starting_the_stack():
    _, recipe = _deploy_trust_rule()
    down = _first_index(recipe, "down-trust-ec2")
    seed = _first_index(recipe, "$(MAKE) seed-trust-data")
    up = _first_index(recipe, "up-trust-ec2")
    assert down < seed < up, f"deploy-trust order is down={down}, seed={seed}, up={up}"


def _seed_play() -> str:
    text = SITE_YML.read_text()
    start = text.find(SEED_PLAY)
    assert start != -1, "the seed play is not in site.yml — this guard has drifted"
    rest = text[start + len(SEED_PLAY) :]
    next_play = re.search(r"^- name: ", rest, re.MULTILINE)
    return rest[: next_play.start()] if next_play else rest


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
    task = task[: task.find("\n    - name: ")]
    assert "assert:" in task
    assert re.search(r"omop_dir_holders\.stdout\s*\|\s*trim\s*\|\s*length\s*==\s*0", task)
    assert "down-trust-ec2" in task, "the fail_msg must tell the operator what to run"
