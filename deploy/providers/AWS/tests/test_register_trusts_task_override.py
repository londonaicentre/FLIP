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
and inherits none of the variables it exports before exec'ing the API. Two of those are
load-bearing: ``UV_NO_SYNC`` (otherwise ``uv run`` re-resolves the project on start and
fetches the setuptools build backend from PyPI, which an egress-less LZA account cannot
reach — FLIP#749) and ``PYTHONPATH=/app/src`` (what makes ``flip_api`` importable once
the boot-time sync no longer installs it). The override therefore carries both itself.

The guard runs the script's real ``jq`` program rather than pattern-matching prose, so
a rewrite that keeps the comment but drops the entries fails.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "register-trusts.sh"
REQUIRED_ENVIRONMENT = {"UV_NO_SYNC": "1", "PYTHONPATH": "/app/src"}


def _override_jq_program() -> str:
    """Return the jq program the script feeds to ``jq -n`` for the container override."""
    source = SCRIPT.read_text()
    match = re.search(r"""overrides="\$\(jq -n '([^']+)' --args -- "\$\{cmd_args\[@\]\}"\)\"""", source)
    assert match, "register-trusts.sh no longer builds containerOverrides with `jq -n '<program>' --args --`"
    return match.group(1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_override_carries_the_entrypoint_environment_and_the_command() -> None:
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
    environment = {e["name"]: e["value"] for e in override["environment"]}
    for name, value in REQUIRED_ENVIRONMENT.items():
        assert environment.get(name) == value, f"{name} missing from the run-task override"


def test_required_variables_match_the_entrypoint() -> None:
    """The override mirrors entrypoint.sh; if the entrypoint changes, so must this list."""
    entrypoint = (SCRIPT.parents[4] / "flip-api" / "entrypoint.sh").read_text()
    assert re.search(r"^UV_NO_SYNC=1$", entrypoint, re.M), "entrypoint.sh no longer sets UV_NO_SYNC=1"
    assert re.search(r'^PYTHONPATH="/app/src', entrypoint, re.M), "entrypoint.sh no longer prepends /app/src"
