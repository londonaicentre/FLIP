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

"""The preflight and the deploy for an account limited to the trust's namespace (FLIP#1986).

Such an account holds the built-in `admin` role in one namespace. It may read /version and its own
Namespace, but not list Services in kube-system (`kubectl cluster-info`), patch the Namespace, or
write ResourceQuotas. The preflight called a healthy cluster unreachable, and the deploy stopped
part-way at the chart's ResourceQuota. These run preflight.sh against a stub kubectl with exactly
those rights.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT = CHART_DIR / "scripts" / "preflight.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

# $DENY: the resources `auth can-i create|list` refuses. $NAMESPACE_EXISTS: whether `get namespace` finds it.
KUBECTL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1 $2" in
  "version "*) echo 'Client Version: v1.31.0'; echo 'Server Version: v1.31.0' ;;
  "config current-context") echo namespaced ;;
  "cluster-info "*) echo 'services is forbidden' >&2; exit 1 ;;
  "get namespace") [ -n "$NAMESPACE_EXISTS" ] || exit 1 ;;
  "get storageclass") echo 'storageclasses is forbidden' >&2; exit 1 ;;
  "auth can-i") case " $DENY " in *" $4 "*) echo no; exit 1 ;; *) echo yes ;; esac ;;
esac
exit 0
"""


def _preflight(tmp_path: Path, **env: str) -> tuple[str, str]:
    log = tmp_path / "calls.log"
    log.write_text("")
    stub = tmp_path / "kubectl"
    stub.write_text(KUBECTL_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    result = subprocess.run(
        ["bash", str(PREFLIGHT)],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "HOME": str(Path.home()),
            "CALL_LOG": str(log),
            "NAMESPACE": "flip-site",
            "NAMESPACE_EXISTS": "1",
            "DENY": "namespaces storageclasses",
            **env,
        },
    )
    return result.stdout, log.read_text()


def test_a_cluster_whose_kube_system_is_closed_to_the_account_is_reachable(tmp_path):
    out, calls = _preflight(tmp_path)
    assert "Cluster unreachable" not in out
    assert "Server 1.31.0" in out
    assert "cluster-info" not in calls


def test_an_existing_namespace_the_account_cannot_create_passes(tmp_path):
    out, _ = _preflight(tmp_path)
    assert "Namespace 'flip-site' exists" in out


def test_a_missing_namespace_the_account_cannot_create_fails(tmp_path):
    out, _ = _preflight(tmp_path, NAMESPACE_EXISTS="")
    assert "Namespace 'flip-site' does not exist and this account cannot create it" in out


def test_a_kind_the_account_cannot_create_is_named_before_the_deploy(tmp_path):
    """`admin` cannot write ResourceQuotas, and the chart renders one for XNAT's conversion jobs."""
    out, calls = _preflight(
        tmp_path, CHART_KINDS="Deployment NetworkPolicy ResourceQuota", DENY="namespaces storageclasses resourcequotas"
    )
    assert "cannot create ResourceQuota in 'flip-site'" in out
    assert "the deploy would stop part-way" in out
    # Each rendered kind is asked for by its resource name, and the release Secret always is.
    for resource in ("deployments", "networkpolicies", "resourcequotas", "secrets"):
        assert f"auth can-i create {resource} --namespace flip-site" in calls


def test_an_account_with_every_right_passes_the_kinds_check(tmp_path):
    out, _ = _preflight(tmp_path, CHART_KINDS="Deployment ResourceQuota")
    assert "can create every kind the chart renders in 'flip-site'" in out


def test_a_chart_that_does_not_render_fails_the_kinds_check(tmp_path):
    """The Makefile's render discards helm's error, so an empty kinds list is a failed render."""
    out, _ = _preflight(tmp_path, CHART_KINDS="")
    assert "The chart does not render with these values" in out
    assert "can create every kind the chart renders" not in out


def test_storage_classes_the_account_cannot_list_are_not_reported_as_missing(tmp_path):
    out, _ = _preflight(tmp_path)
    assert "cannot list StorageClasses" in out
    assert "No default StorageClass found" not in out


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
class TestMakefile:
    def _dry_run(self, target: str) -> str:
        return subprocess.run(
            ["make", "-n", "-C", str(CHART_DIR), target, "NAMESPACE=flip-site"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def test_the_preflight_is_told_the_kinds_this_deploy_renders(self):
        recipe = self._dry_run("preflight")
        assert 'CHART_KINDS="$(helm template' in recipe
        assert "sed -n 's/^kind: //p'" in recipe

    def test_a_deploy_never_patches_a_namespace_that_exists(self):
        """Applying the Namespace needs patch on it; reading it is all `admin` in the namespace may do."""
        recipe = self._dry_run("deploy-release")
        assert "get namespace flip-site >/dev/null 2>&1 || kubectl create namespace flip-site" in recipe
        assert "create namespace flip-site --dry-run" not in recipe
