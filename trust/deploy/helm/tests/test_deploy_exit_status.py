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

"""Which deploy step failed is told by which command failed, run for real (FLIP#1645).

GNU make exits 2 whenever a recipe fails, whatever code the recipe itself returned, so an exit
code of 3 for "upgraded, but XNAT not configured" never reached a caller: the earlier tests only
found the string `exit 3` in `make -n` output. The deploy is now two targets, `deploy-release`
(helm upgrade + the rollout wait) and `xnat-init`, and a caller that must tell the outcomes apart
runs them in turn. These tests run the real targets against stub `helm` and `kubectl` binaries
and assert on the real exit status of each.

`-o preflight` skips the preflight prerequisite (it checks tools, a cluster and a kit file, none
of which these tests have); everything after it runs as written.
"""

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("python3") is None, reason="make and python3 are required"
)

JOB_RENDER = "apiVersion: batch/v1\nkind: Job\nmetadata:\n  labels:\n    app.kubernetes.io/component: xnat-init\n"

# helm: `template` prints the render, `upgrade` exits HELM_UPGRADE_RC, `status` prints an empty release.
HELM_STUB = """#!/bin/sh
printf 'helm %s\\n' "$*" >> "$CALL_LOG"
case "$1" in
  template) cat "$HELM_RENDER" ;;
  upgrade) exit "${HELM_UPGRADE_RC:-0}" ;;
  status) echo '{}' ;;
esac
exit 0
"""

# kubectl: the rollout wait's `get <kind> -o json` finds one core Deployment; `rollout status`
# succeeds; `wait job` (the xnat-init step) exits KUBECTL_WAIT_RC. Everything else succeeds quietly.
KUBECTL_STUB = """#!/bin/sh
printf 'kubectl %s\\n' "$*" >> "$CALL_LOG"
case " $* " in
  *" -f - "*) cat > /dev/null ;;
esac
case "$1" in
  get)
    if [ "$2" = deployment ] && [ "$(eval echo \\${$#})" = json ]; then
      echo '{"items":[{"metadata":{"name":"t-trust-api","labels":{"app.kubernetes.io/component":"trust-api"}}}]}'
    elif [ "$(eval echo \\${$#})" = json ]; then
      echo '{"items":[]}'
    fi ;;
  wait)
    # JOB_FAILED: the Job is past its backoffLimit, so Failed is set at once and Complete never comes.
    if [ -n "$JOB_FAILED" ]; then
      case "$*" in *condition=failed*) exit 0 ;; *) exec sleep 60 ;; esac
    fi
    # COMPLETES_AFTER: Complete comes after that many seconds; the Failed-waiter blocks, or errors with WATCH_ERROR.
    if [ -n "$COMPLETES_AFTER" ]; then
      case "$*" in
        *condition=failed*) [ -z "$WATCH_ERROR" ] || { echo 'error: watch closed' >&2; exit 1; }; exec sleep 60 ;;
        *) sleep "$COMPLETES_AFTER"; exit 0 ;;
      esac
    fi
    exit "${KUBECTL_WAIT_RC:-0}" ;;
esac
exit 0
"""


def _stub(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make(
    tmp_path: Path, target: str, render_text: str = JOB_RENDER, make_args: tuple[str, ...] = (), **env_overrides: str
) -> tuple[subprocess.CompletedProcess, str]:
    render = tmp_path / "render.yaml"
    render.write_text(render_text)
    log = tmp_path / "calls.log"
    log.write_text("")
    env = dict(os.environ, HELM_RENDER=str(render), CALL_LOG=str(log), **env_overrides)
    helm = _stub(tmp_path / "helm", HELM_STUB)
    kubectl = _stub(tmp_path / "kubectl", KUBECTL_STUB)
    result = subprocess.run(
        ["make", "-o", "preflight", "-C", str(CHART_DIR), target, f"HELM_BIN={helm}", f"KUBECTL={kubectl}", *make_args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    return result, log.read_text()


def test_a_healthy_deploy_exits_zero(tmp_path):
    result, calls = _make(tmp_path, "deploy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rollout status deployment/t-trust-api" in calls
    assert "wait job" in calls


def test_a_rejected_upgrade_fails_the_release_step_and_never_reaches_xnat_init(tmp_path):
    result, calls = _make(tmp_path, "deploy-release", HELM_UPGRADE_RC="1")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "rollout status" not in calls
    assert "wait job" not in calls


def test_a_failed_xnat_init_leaves_the_release_step_green(tmp_path):
    """The distinction a caller needs: release landed (deploy-release 0), init did not (xnat-init != 0)."""
    release, _ = _make(tmp_path, "deploy-release", KUBECTL_WAIT_RC="1")
    assert release.returncode == 0, release.stdout + release.stderr

    init, _ = _make(tmp_path, "xnat-init", KUBECTL_WAIT_RC="1")
    assert init.returncode != 0, init.stdout + init.stderr
    assert "XNAT init failed" in init.stdout


def test_deploy_says_the_release_is_live_when_only_xnat_init_failed(tmp_path):
    result, _ = _make(tmp_path, "deploy", KUBECTL_WAIT_RC="1")
    assert result.returncode != 0
    assert "the release is live and rolled out, but xnat-init did not complete" in result.stdout + result.stderr


def test_a_failed_xnat_init_job_ends_the_wait_at_once(tmp_path):
    """A Job past its backoffLimit never completes; the step used to wait out HELM_TIMEOUT (FLIP#1958)."""
    started = time.monotonic()
    result, calls = _make(tmp_path, "xnat-init", JOB_FAILED="1")

    assert result.returncode != 0, result.stdout + result.stderr
    assert time.monotonic() - started < 30, "the step waited for a Complete that cannot come"
    assert "XNAT init failed (the Job failed)" in result.stdout
    assert "--for=condition=failed" in calls
    assert "--for=condition=complete" in calls


@pytest.mark.parametrize("watch_error", ["", "1"], ids=["failed-waiter-blocks", "failed-waiter-errors"])
def test_a_job_that_completes_later_passes_even_if_the_failed_waiter_errors(tmp_path, watch_error):
    started = time.monotonic()
    result, _ = _make(tmp_path, "xnat-init", COMPLETES_AFTER="3", WATCH_ERROR=watch_error)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "XNAT init complete" in result.stdout
    assert time.monotonic() - started < 30, "the step waited on the Failed-waiter after Complete"


def test_an_unrenderable_chart_fails_xnat_init_instead_of_reporting_xnat_disabled(tmp_path):
    """`helm template` failing used to leave an empty render, read as "XNAT is disabled", exit 0."""
    failing_helm = "#!/bin/sh\nprintf 'helm %s\\n' \"$*\" >> \"$CALL_LOG\"\necho 'Error: failed to parse' >&2\nexit 1\n"
    render = tmp_path / "render.yaml"
    render.write_text("")
    log = tmp_path / "calls.log"
    log.write_text("")
    helm = _stub(tmp_path / "helm", failing_helm)
    kubectl = _stub(tmp_path / "kubectl", KUBECTL_STUB)
    result = subprocess.run(
        ["make", "-C", str(CHART_DIR), "xnat-init", f"HELM_BIN={helm}", f"KUBECTL={kubectl}"],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ, HELM_RENDER=str(render), CALL_LOG=str(log)),
    )
    assert result.returncode != 0, result.stdout
    assert "could not render the chart" in result.stdout
    assert "XNAT is disabled" not in result.stdout
    assert "kubectl" not in log.read_text()


SEED_RENDER = JOB_RENDER.replace("xnat-init", "trust-seed")


def _reseed(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess, str]:
    """`make reseed KIT=` with the trust's override in place (OVERRIDES_FILE names a scratch one)."""
    override = tmp_path / "k8s-trust-TESTKIT.yaml"
    override.write_text("trustNumber: 2\n")
    return _make(tmp_path, "reseed", SEED_RENDER, ("KIT=TESTKIT", f"OVERRIDES_FILE={override}"), **env)


def test_reseed_runs_the_seed_job_once_with_reseed_on_and_the_trusts_override(tmp_path):
    """The hook seeds only an empty store (FLIP#2111), so a pin bump reaches a seeded trust only this way."""
    result, calls = _reseed(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    template = next(line for line in calls.splitlines() if line.startswith("helm template"))
    assert f"-f {tmp_path / 'k8s-trust-TESTKIT.yaml'}" in template, "the partition comes from the trust's override"
    assert "--set trustData.seed.reseed=true" in template, template
    assert "delete job -n flip-trust --selector app.kubernetes.io/component=trust-seed" in calls, calls
    assert "wait job --selector app.kubernetes.io/component=trust-seed" in calls, calls
    assert "Trust re-seed complete" in result.stdout


def test_a_failed_reseed_fails_and_names_itself(tmp_path):
    result, _ = _reseed(tmp_path, KUBECTL_WAIT_RC="1")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Trust re-seed failed" in result.stdout


def test_reseed_refuses_without_a_kit_or_its_override(tmp_path):
    """Without the override the Job renders trustNumber 1 and would load slot 1's data into any trust."""
    no_kit, calls = _make(tmp_path, "reseed", SEED_RENDER)
    assert no_kit.returncode != 0, no_kit.stdout
    assert "KIT=<CODE> is required" in no_kit.stdout
    missing, _ = _make(tmp_path, "reseed", SEED_RENDER, ("KIT=TESTKIT", f"OVERRIDES_FILE={tmp_path}/absent.yaml"))
    assert missing.returncode != 0, missing.stdout
    assert "run make sync-kit KIT=TESTKIT first" in missing.stdout
    assert "kubectl" not in calls


def test_reseed_with_seeding_off_does_nothing(tmp_path):
    override = tmp_path / "k8s-trust-TESTKIT.yaml"
    override.write_text("trustNumber: 2\n")
    result, calls = _make(tmp_path, "reseed", JOB_RENDER, ("KIT=TESTKIT", f"OVERRIDES_FILE={override}"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to re-seed" in result.stdout
    assert "kubectl" not in calls
