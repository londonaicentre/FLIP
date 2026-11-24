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
# `get values` prints the release's values, or fails with HELM_GET_ERR on stderr; `template` logs a
# line when one of its arguments is a file holding those values.
HELM_STUB = """#!/bin/sh
printf 'helm %s\\n' "$*" >> "$CALL_LOG"
case "$1" in
  get)
    [ -z "$HELM_GET_ERR" ] || { echo "$HELM_GET_ERR" >&2; exit 1; }
    printf 'pacs:\\n  host: site-pacs\\n' ;;
  template)
    for a in "$@"; do
      if grep -qs site-pacs -- "$a"; then echo "template read the release values from $a" >> "$CALL_LOG"; fi
    done
    cat "$HELM_RENDER" ;;
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


def _make(tmp_path: Path, target: str, *args: str, **env_overrides: str) -> tuple[subprocess.CompletedProcess, str]:
    render = tmp_path / "render.yaml"
    render.write_text(JOB_RENDER)
    log = tmp_path / "calls.log"
    log.write_text("")
    env = dict(os.environ, HELM_RENDER=str(render), CALL_LOG=str(log), **env_overrides)
    helm = _stub(tmp_path / "helm", HELM_STUB)
    kubectl = _stub(tmp_path / "kubectl", KUBECTL_STUB)
    result = subprocess.run(
        ["make", "-o", "preflight", "-C", str(CHART_DIR), target, f"HELM_BIN={helm}", f"KUBECTL={kubectl}", *args],
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
    failing_helm = (
        '#!/bin/sh\nprintf \'helm %s\\n\' "$*" >> "$CALL_LOG"\n'
        "[ \"$1\" = template ] || exit 0\necho 'Error: failed to parse' >&2\nexit 1\n"
    )
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


# ---------------------------------------------------------------- the values xnat-init renders with (FLIP#2047)


def test_a_bare_xnat_init_renders_the_job_from_the_releases_own_values(tmp_path):
    """The failure hints print `make -C trust/deploy/helm xnat-init` with no values. Rendered from
    the chart defaults, that Job re-pointed XNAT at the mocked PACS on the default image tag and
    reported "XNAT init complete"."""
    result, calls = _make(tmp_path, "xnat-init")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "helm get values trust-release --namespace flip-trust -o yaml" in calls
    assert "template read the release values" in calls
    assert calls.index("helm get values") < calls.index("helm template")


def test_an_override_file_handed_to_xnat_init_is_what_it_renders_with(tmp_path):
    """deploy-trust-k8s and upgrade-trust-k8s pass the override they just deployed."""
    result, calls = _make(tmp_path, "xnat-init", "OVERRIDES_FILE=k8s-trust-ABC.yaml", "TAG=v1.2.3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "helm get values" not in calls
    assert "template read the release values" not in calls


def _template_args(calls: str) -> tuple[list[str], str]:
    """The `helm template` arguments, and the file among them that holds the release's values."""
    template = next(line for line in calls.splitlines() if line.startswith("helm template ")).split()
    release_values = next(line for line in calls.splitlines() if line.startswith("template read")).split()[-1]
    return template, release_values


@pytest.mark.parametrize(
    ("given", "rendered_after"),
    [
        ("KIT=ABC", None),
        ("TAG=v1.2.3", "global.image.tag=v1.2.3"),
        ("PLATFORM_VALUES=values-aks.yaml", "values-aks.yaml"),
        ("VALUES_FILE=site-values.yaml", "site-values.yaml"),
    ],
    ids=["kit", "tag", "platform-values", "values-file"],
)
def test_without_an_override_file_the_release_is_read_and_what_the_caller_passed_wins(tmp_path, given, rendered_after):
    """`xnat-init KIT=<CODE> HUB=<env>` names no override file, so it too rendered the Job from the
    chart defaults. Only an override file stands in for the release's values; anything else the
    caller passes is applied on top of them."""
    result, calls = _make(tmp_path, "xnat-init", given)

    assert result.returncode == 0, result.stdout + result.stderr
    template, release_values = _template_args(calls)
    if rendered_after:
        assert template.index(release_values) < template.index(rendered_after), template


def test_the_charts_default_values_file_is_not_laid_over_the_releases_values(tmp_path):
    """`-f values.yaml` after the release's values would reset every key to the chart default."""
    _, calls = _make(tmp_path, "xnat-init")

    template, release_values = _template_args(calls)
    assert not any(arg.endswith("/values.yaml") for arg in template[template.index(release_values) :])


def test_xnat_init_with_no_release_keeps_the_chart_defaults(tmp_path):
    result, calls = _make(tmp_path, "xnat-init", HELM_GET_ERR="Error: release: not found")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "helm template" in calls
    assert "template read the release values" not in calls
    assert "no release trust-release" in result.stdout


def test_xnat_init_refuses_to_guess_when_the_release_cannot_be_read(tmp_path):
    """An unreachable cluster is not "no release": falling back to the defaults there is the same
    wrong Job, so nothing is rendered or applied."""
    result, calls = _make(tmp_path, "xnat-init", HELM_GET_ERR="Error: Kubernetes cluster unreachable")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "could not read the values of release trust-release" in result.stdout
    assert "Kubernetes cluster unreachable" in result.stdout
    assert "helm template" not in calls
    assert "kubectl" not in calls


def _dry_run(*args: str) -> str:
    return subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), *args], capture_output=True, text=True, check=True, timeout=120
    ).stdout


@pytest.mark.parametrize(
    "args",
    [("deploy",), ("deploy-trust-k8s", "KIT=ABC", "HUB=stag"), ("_upgrade-trust-k8s-apply", "KIT=ABC", "HUB=stag")],
    ids=["deploy", "deploy-trust-k8s", "upgrade-trust-k8s"],
)
def test_the_xnat_init_hint_is_a_command_that_reaches_the_same_release(args):
    """Printed where xnat-init did not complete. It names the cluster, namespace and release the
    caller chose, and no values: the target reads those from the release."""
    hint = "make -C trust/deploy/helm xnat-init KUBE_CONTEXT=kind-x NAMESPACE=ns RELEASE_NAME=rel"
    assert hint in _dry_run(*args, "KUBE_CONTEXT=kind-x", "NAMESPACE=ns", "RELEASE_NAME=rel")

    bare = _dry_run(*args)
    assert "make -C trust/deploy/helm xnat-init" in bare
    assert "xnat-init KUBE_CONTEXT" not in bare
    assert "xnat-init NAMESPACE" not in bare


def test_the_xnat_init_messages_point_at_the_readme_section_not_an_unrelated_issue():
    """#565 is "Wire dcm2niix Job execution on Kubernetes deployment" and says nothing about a
    timed-out init job."""
    text = (CHART_DIR / "Makefile").read_text()
    assert "did not complete (see #565)" not in text
    assert text.count("did not complete (see $(XNAT_INIT_HELP))") == 3
    assert "XNAT_INIT_HELP := trust/deploy/helm/README.md, Running the XNAT init job by hand\n" in text
    assert "### Running the XNAT init job by hand" in (CHART_DIR / "README.md").read_text()
