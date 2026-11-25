# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""The EFS provisioning step survives the apply that creates its task role (FLIP#1878).

A role created seconds earlier is not assumable everywhere yet, and ``run-task`` answers "ECS was
unable to assume the role". The provisioner retries that refusal for a bounded time and fails at
once on any other. Asserted by running its command against a stub ``aws``.
"""

import subprocess
from pathlib import Path

from test_ecs_task_exit_code import INTERPOLATION, _command, _container_name

NOT_ASSUMABLE = "An error occurred (ClientException) when calling RunTask: ECS was unable to assume the role"

# run-task refuses REFUSALS times with REFUSAL, then answers. The count lives in a file: each call is its own process.
STUB_AWS = """#!/usr/bin/env bash
case "$1 $2" in
  "ecs run-task")
    calls=$(($(cat "$CALLS" 2>/dev/null || echo 0) + 1)); echo "$calls" > "$CALLS"
    if [ "$calls" -le "$REFUSALS" ]; then echo "$REFUSAL" >&2; exit 254; fi
    echo "arn:aws:ecs:eu-west-2:000000000000:task/flip/0123" ;;
  "ecs wait") exit 0 ;;
  "ecs describe-tasks") echo 0 ;;
  *) echo "unexpected aws call: $*" >&2; exit 2 ;;
esac
"""


def _run(tmp_path: Path, refusals: int, refusal: str = NOT_ASSUMABLE) -> tuple[subprocess.CompletedProcess, int]:
    """Run the provisioner's command against the stub; return the result and how often run-task was called."""
    container = _container_name("efs")
    script = INTERPOLATION.sub(lambda m: container if m.group(1).startswith("jsondecode(") else "x", _command("efs"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"aws": STUB_AWS, "sleep": "#!/usr/bin/env bash\n"}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls), "REFUSALS": str(refusals), "REFUSAL": refusal}
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
    return result, int(calls.read_text())


def test_a_role_not_yet_assumable_is_retried(tmp_path):
    result, calls = _run(tmp_path, refusals=2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == 3


def test_a_role_never_assumable_fails_after_a_bounded_number_of_tries(tmp_path):
    result, calls = _run(tmp_path, refusals=99)

    assert result.returncode == 1
    assert calls == 6
    assert "could not assume" in result.stdout


def test_any_other_refusal_fails_at_once(tmp_path):
    result, calls = _run(tmp_path, refusals=99, refusal="An error occurred (AccessDeniedException)")

    assert result.returncode == 1
    assert calls == 1
    assert "AccessDeniedException" in result.stderr
