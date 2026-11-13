# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`xnat-init` must be a no-op on a trust that has no XNAT.

`templates/xnat-init-job.yaml` is guarded on `and .Values.xnat.enabled
.Values.xnat.initJob.enabled`, so with XNAT turned off the chart renders no Job at all. The target
piped that render straight into `kubectl apply --selector app.kubernetes.io/component=xnat-init`,
and kubectl fails an apply with no matching objects ("no objects passed to apply") — so `make
deploy` on such a trust reached its final step and exited 3, reporting a failed XNAT configuration
for a trust that has no XNAT to configure.

These run the target for real against stubs, because the defect is in runtime control flow: a
`make -n` assertion cannot tell whether the wait is skipped or whether `exit 0` escaped one recipe
line and let the next one run.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")

JOB_OBJECT = "apiVersion: batch/v1\nkind: Job\nmetadata:\n  labels:\n    app.kubernetes.io/component: xnat-init\n"

# Records every kubectl invocation, so a test can assert what did (and did not) reach the cluster.
# `apply` reproduces the real failure this target has to avoid: given an input with no objects, kubectl
# exits 1 with "no objects passed to apply". A stub that always exits 0 would let the regression
# through on the exit code and only be caught by the message.
KUBECTL_STUB = """#!/bin/sh
printf '%s\n' "$*" >> "$KUBECTL_LOG"
if [ "$1" = "apply" ]; then
  src=/dev/stdin
  prev=""
  for a in "$@"; do
    [ "$prev" = "-f" ] && [ "$a" != "-" ] && src="$a"
    prev="$a"
  done
  if ! grep -q '^kind:' "$src" 2>/dev/null; then
    echo 'error: no objects passed to apply' >&2
    exit 1
  fi
fi
exit 0
"""

HELM_STUB = """#!/bin/sh
cat "$HELM_RENDER"
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run_xnat_init(tmp_path: Path, render: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    render_file = tmp_path / "render.yaml"
    render_file.write_text(render)
    log = tmp_path / "kubectl.log"
    log.write_text("")

    env = dict(os.environ)
    env["HELM_RENDER"] = str(render_file)
    env["KUBECTL_LOG"] = str(log)

    helm = _stub(tmp_path / "helm", HELM_STUB)
    kubectl = _stub(tmp_path / "kubectl", KUBECTL_STUB)

    result = subprocess.run(
        [
            "make",
            "-C",
            str(CHART_DIR),
            "xnat-init",
            f"HELM_BIN={helm}",
            f"KUBECTL={kubectl}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    calls = [line for line in log.read_text().splitlines() if line.strip()]
    return result, calls


@needs_make
def test_a_trust_without_xnat_succeeds_without_touching_the_cluster(tmp_path):
    """The regression: zero rendered objects used to exit 3 via kubectl's apply error."""
    result, calls = _run_xnat_init(tmp_path, "# XNAT disabled: the chart renders no init Job.\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to initialise" in result.stdout
    assert calls == [], f"kubectl was called on a trust with no XNAT: {calls}"


@needs_make
def test_a_trust_with_xnat_still_runs_the_init(tmp_path):
    """Guard against "fixing" it by skipping the step unconditionally."""
    result, calls = _run_xnat_init(tmp_path, JOB_OBJECT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert any(call.startswith("delete job") for call in calls), calls
    assert any(call.startswith("apply") for call in calls), calls
    assert any(call.startswith("wait job") for call in calls), calls
    assert "nothing to initialise" not in result.stdout


@needs_make
def test_the_apply_is_scoped_to_the_init_job(tmp_path):
    """The render holds every resource in the chart; only the Job may be applied."""
    _, calls = _run_xnat_init(tmp_path, JOB_OBJECT)

    apply = next(call for call in calls if call.startswith("apply"))
    assert "--selector app.kubernetes.io/component=xnat-init" in apply
