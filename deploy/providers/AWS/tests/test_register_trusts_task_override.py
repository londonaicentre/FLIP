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
"""The one-off ``register_trust`` ECS task must not depend on the image entrypoint.

``register-trusts.sh`` runs ``flip_api.scripts.register_trust`` as a Fargate ``run-task``
with a ``containerOverrides[].command``. The flip-api image declares no ``ENTRYPOINT`` —
only ``CMD ["/app/entrypoint.sh"]`` — so that override replaces the entrypoint wholesale
and inherits only what the image itself declares. Two variables are load-bearing there:
``UV_NO_SYNC`` (otherwise ``uv run`` re-resolves the project on start and fetches the
setuptools build backend from PyPI, which an egress-less LZA account cannot reach —
FLIP#749) and ``PYTHONPATH=/app/src`` (what makes ``flip_api`` importable once the
boot-time sync no longer installs it). They are therefore image ``ENV`` in the flip-api
Dockerfile — the one place every way of running the image inherits from — and the
override carries no environment of its own. The guard runs the script's real ``jq``
program rather than pattern-matching prose, so a rewrite that keeps the comment but
breaks the override fails.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "register-trusts.sh"
DOCKERFILE = SCRIPT.parents[4] / "flip-api" / "Dockerfile"
IMAGE_ENV = {"UV_NO_SYNC": "1", "PYTHONPATH": "/app/src"}


def _override_jq_program() -> str:
    """Return the jq program the script feeds to ``jq -n`` for the container override."""
    source = SCRIPT.read_text()
    match = re.search(r"""overrides="\$\(jq -n '([^']+)' --args -- "\$\{cmd_args\[@\]\}"\)\"""", source)
    assert match, "register-trusts.sh no longer builds containerOverrides with `jq -n '<program>' --args --`"
    return match.group(1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_override_carries_the_command_and_no_environment_of_its_own() -> None:
    cmd = ["uv", "run", "python", "-m", "flip_api.scripts.register_trust", "--name", "A Trust", "--code", "AT"]
    rendered = subprocess.run(
        ["jq", "-n", _override_jq_program(), "--args", "--", *cmd],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    overrides = json.loads(rendered)["containerOverrides"]
    assert [o["name"] for o in overrides] == ["flip-api"]
    (override,) = overrides
    # Dash-prefixed args must survive as positionals — the reason for `--args --`.
    assert override["command"] == cmd
    # Runtime env comes from image ENV (below), not from the override — a re-added
    # per-call copy would be a second place to keep in step with the Dockerfile.
    assert "environment" not in override, "the override must not carry its own environment entries"


def test_image_env_carries_the_runtime_variables() -> None:
    """The variables the override relies on are declared as image ENV in the flip-api Dockerfile."""
    dockerfile = DOCKERFILE.read_text()
    env_lines = " ".join(line.removeprefix("ENV ") for line in dockerfile.splitlines() if line.startswith("ENV "))
    declared = dict(re.findall(r'([A-Z_]+)="?([^"\s]+)"?', env_lines))
    for name, value in IMAGE_ENV.items():
        assert declared.get(name) == value, f"flip-api/Dockerfile no longer declares ENV {name}={value}"


def test_the_ecs_waiter_status_is_checked() -> None:
    """errexit is off inside register_one_kit, so an expired waiter must be caught by hand.

    An unchecked `ecs wait` lets the script read SSM while the task is still running, report
    the rotation as failed, and delete the parameter the task is about to write the new
    credentials into.
    """
    source = SCRIPT.read_text()
    assert "if ! aws_cmd ecs wait tasks-stopped" in source, (
        "the `ecs wait tasks-stopped` call must be status-checked, not fire-and-forget"
    )


def test_a_failed_ssm_read_does_not_delete_the_parameter() -> None:
    """A transient SSM error must not be read as 'the task wrote no kit'.

    The absent-kit branch deletes the parameter, and on a rotation that parameter holds the
    only copy of the live new credentials. Only ParameterNotFound may fall through to it.
    """
    source = SCRIPT.read_text()
    assert "--query 'Parameter.Value' --output text 2>/dev/null || true" not in source, (
        "the SSM read must not swallow its exit status with `|| true`"
    )
    assert 'grep -q "ParameterNotFound"' in source, (
        "only ParameterNotFound may be treated as an absent kit; any other error must keep the parameter"
    )


def test_rotation_refuses_more_than_one_kit_file() -> None:
    """Rotation is one trust at a time: each new kit has to reach its site before the cutover."""
    source = SCRIPT.read_text()
    assert "ROTATE_KEYS=1 requires KIT=<CODE>" in source
    assert "set -- ${KIT_FILES}" in source, "the guard must count KIT_FILES, the only kit input the Makefile passes"
