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

"""Static guards on cloud-trust FL participant-kit staging (FLIP#965).

FLIP#965 was two defects that hid each other: ``trust-ec2-role`` could not
``kms:Decrypt`` the SSE-KMS bucket, and the Flower sync built a ``net-1//`` source
that matched no key, copied nothing and exited 0. The play reported ``ok`` and the
trust was left with no kit — surfacing much later as an FL client that cannot
establish mTLS. Its third acceptance criterion is that **the silent no-op must not be
reachable again**.

Nothing else in CI reads this material. ``validate_terraform`` runs ``fmt``/``validate``,
which cannot see policy content; no job runs the Ansible playbook. So the properties the
fix rests on are asserted here, over the source, the way the Kubernetes half of the same
change is covered by ``trust/deploy/helm/tests/``.

The properties, all of them regressions that would otherwise leave CI green:

* Each staging shell aborts on the first failure rather than running on to its guard.
* The wipe runs **before** the fetch and is scoped to ``net-1/``, so a sync that copies
  nothing leaves nothing to mistake for success, and the host holds exactly one slot's kit.
* Each shell asserts the *result* of the sync and exits non-zero, rather than trusting an
  exit code that is 0 for an empty source.
* ``trust_s3_source`` already ends in ``/`` and is consumed bare — appending another
  reproduces the original ``net-1//`` no-op.
* The Flower sync fetches this host's own SuperNode credential and nothing else; the
  prefix also holds every other slot's private key and the SuperLink's server key.
* The FLARE stage proves the staged kit belongs to *this* slot, not merely that some
  kit arrived.

Shell comment lines are stripped before matching, so no assertion here can be satisfied
by a comment that merely mentions the thing.
"""

import re
from pathlib import Path

# The two S3 stages live in the shared flip_fl_kit role (FLIP#1213); aws-ec2-host.yml composes it
# with fl_kit_source=s3. The role's task list sits at the top level of its file.
KIT_STAGE_TASKS = Path(__file__).resolve().parent.parent / "roles" / "flip_fl_kit" / "tasks" / "s3.yml"

NVFLARE_SYNC_TASK = "sync FLARE participant kit from S3"
FLOWER_SYNC_TASK = "sync Flower net-1 certificates and keys from S3"

TASK_BODY_INDENT = 4


def _task_shell(task_name: str) -> str:
    """Return the ``shell:`` script of one Ansible task in the kit-staging role.

    Args:
        task_name (str): The task's ``name:``, matched exactly.

    Returns:
        str: The script, with comment-only lines removed so an assertion cannot be
            satisfied by a comment.
    """
    playbook = KIT_STAGE_TASKS.read_text()
    start = playbook.find(f"- name: {task_name}\n")
    assert start != -1, f"no task named {task_name!r} in {KIT_STAGE_TASKS.name} — this guard has drifted from the role"

    rest = playbook[start:]
    marker = "shell: |\n"
    shell_at = rest.find(marker)
    assert shell_at != -1, f"task {task_name!r} no longer runs a shell: block"

    lines = []
    for line in rest[shell_at + len(marker) :].splitlines():
        if line.strip() and not line.startswith(" " * TASK_BODY_INDENT):
            break
        lines.append(line[TASK_BODY_INDENT:])

    script = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert "aws s3 sync" in script, f"extracted no sync from {task_name!r} — the extractor is broken, not the play"
    return script


def _both_shells() -> dict[str, str]:
    """Return both kit-staging scripts, keyed by backend."""
    return {"nvflare": _task_shell(NVFLARE_SYNC_TASK), "flower": _task_shell(FLOWER_SYNC_TASK)}


def test_both_kit_syncs_abort_on_the_first_failure():
    """``set -euo pipefail`` is what turns an AccessDenied or a partial transfer into a failed task."""
    for backend, script in _both_shells().items():
        first = next(line for line in script.splitlines() if line.strip())
        assert first.strip() == "set -euo pipefail", f"{backend} staging does not start with set -euo pipefail"


def test_both_kit_syncs_wipe_the_net_1_tree_before_fetching():
    """The wipe is what makes a no-op sync visible, and scoping it to net-1 leaves one slot's kit.

    Ordering is the load-bearing half: a wipe *after* the fetch would delete the kit it
    just staged, and no wipe at all lets a sync that copies nothing pass the guards below
    on the previous run's files.
    """
    for backend, script in _both_shells().items():
        wipe = re.search(r"^\s*find\s+\"([^\"]+)\"\s+-mindepth 1 -delete\s*$", script, re.MULTILINE)
        assert wipe is not None, f"{backend} staging no longer wipes its destination before fetching"
        assert wipe.group(1).endswith("/net-1"), (
            f"{backend} wipes {wipe.group(1)!r}, not the net-1 tree — a host that re-registers to another "
            "slot would keep the previously staged slot's FL identity on disk"
        )
        assert wipe.start() < script.index("aws s3 sync"), f"{backend} wipes after the fetch, not before it"


def test_both_kit_syncs_fail_when_the_fetch_produces_nothing():
    """Assert the result, not the exit code: ``aws s3 sync`` exits 0 on an empty source (FLIP#965)."""
    for backend, script in _both_shells().items():
        after_sync = script[script.index("aws s3 sync") :]
        assert re.search(r"if\s+\[\s+!\s+-s\s+", after_sync), (
            f"{backend} staging has no non-empty-file test after the sync — a zero-byte or absent object "
            "would report success"
        )
        assert re.search(r"^\s*exit 1\s*$", after_sync, re.MULTILINE), (
            f"{backend} staging never exits non-zero, so a failed stage would still report ok"
        )


def test_kit_sources_are_consumed_bare():
    """``trust_s3_source`` ends in ``/``; appending another gives ``net-1//``, which matches no key."""
    playbook = KIT_STAGE_TASKS.read_text()

    definitions = re.findall(r"^\s*trust_s3_source:\s*\"([^\"]+)\"\s*$", playbook, re.MULTILINE)
    assert len(definitions) == 2, f"expected one trust_s3_source per backend stage, found {len(definitions)}"
    for source in definitions:
        assert source.endswith("/"), f"trust_s3_source {source!r} does not end in a slash"

    assert "trust_s3_source }}/" not in playbook, (
        "a slash is appended to trust_s3_source — that is the FLIP#965 double-slash: the sync then matches "
        "no key, copies nothing and exits 0"
    )


def test_nvflare_stage_proves_the_kit_belongs_to_this_slot():
    """A kit for the wrong slot would register this host under another trust's identity."""
    script = _task_shell(NVFLARE_SYNC_TASK)
    assert "fqsn" in script, "the FLARE stage no longer checks fed_client.json's site name"
    assert "fl_kit_slot" in script.split("fqsn")[1], "the fqsn check no longer compares against the staged slot"


def test_flower_sync_fetches_only_this_hosts_credential():
    """The Flower kit prefix holds every slot's SuperNode key and the SuperLink's server key."""
    script = _task_shell(FLOWER_SYNC_TASK)
    includes = re.findall(r"--include \"([^\"]+)\"", script)
    assert includes == ["certificates/ca.crt", "keys/supernode_credentials_{{ trust_num }}"], (
        f"Flower sync fetches {includes} — it must fetch this host's own credential and the CA, nothing else"
    )
    assert '--exclude "*"' in script, "the --include patterns only narrow if everything is excluded first"
    assert script.index('--exclude "*"') < script.index("--include"), "--exclude must precede the --include list"
