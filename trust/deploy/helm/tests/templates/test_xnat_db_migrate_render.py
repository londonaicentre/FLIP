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

"""The pre-upgrade xnat-db dump hook, rendered (FLIP#2071).

What is pinned here is what makes the hook safe rather than merely present: it is a *pre-upgrade*
hook (an install has nothing to dump and no xnat-db to reach), it runs the release's own xnat-db
image so a pin makes it a no-op, its identity can patch exactly one Deployment, its NetworkPolicy
selects nothing but its own pod, and it can write the volume as the uid that owns it.

Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
KIT = "flClient.kitHostPath=/opt/flip/fl-kit"
#: `helm template trust-release` makes the fullname `trust-release-flip-trust` (the release name
#: does not contain the chart name), and every object below is named off it.
FULLNAME = "trust-release-flip-trust"
NAME = f"{FULLNAME}-xnat-db-migrate"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(*sets: str) -> list[dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--namespace", "flip-trust", "--set", KIT]
    for item in sets:
        args += ["--set", item]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _hook(*sets: str) -> dict[str, dict]:
    """The hook's objects by kind, keyed off the one name they share."""
    docs = _render(*sets)
    return {d["kind"]: d for d in docs if str(d["metadata"]["name"]).startswith(NAME)}


def _job(*sets: str) -> dict:
    return _hook(*sets)["Job"]


def test_the_hook_is_a_pre_upgrade_hook_only():
    """An install has no database to dump and no xnat-db Service to dump it from."""
    for kind, doc in _hook().items():
        annotations = doc["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-upgrade", kind
        assert "before-hook-creation" in annotations["helm.sh/hook-delete-policy"], kind


def test_the_identity_is_created_before_the_job_that_uses_it():
    """None of these exist in a release made before this hook, so the same run must create them."""
    hook = _hook()
    weights = {kind: int(doc["metadata"]["annotations"]["helm.sh/hook-weight"]) for kind, doc in hook.items()}
    for kind in ("ConfigMap", "ServiceAccount", "Role", "RoleBinding", "NetworkPolicy"):
        assert weights[kind] < weights["Job"], f"{kind} must be created before the Job"


def test_the_job_is_deleted_once_it_has_succeeded():
    annotations = _job()["metadata"]["annotations"]
    assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"


@pytest.mark.parametrize(
    "sets",
    [
        pytest.param(("xnat.enabled=false",), id="no xnat"),
        pytest.param(("xnat.db.enabled=false",), id="no xnat-db"),
        pytest.param(("xnat.db.migrateDump.enabled=false",), id="switched off"),
        pytest.param(("xnat.db.persistence.enabled=false",), id="no volume to dump beside"),
    ],
)
def test_nothing_renders_when_there_is_nothing_to_dump(sets):
    assert _hook(*sets) == {}


def test_the_deploy_paths_xnat_init_switch_does_not_disable_it():
    """`make deploy-release` passes xnat.initJob.enabled=false; that is the post-upgrade hook only."""
    assert _job("xnat.initJob.enabled=false")["metadata"]["name"] == NAME


def test_it_runs_the_releases_own_xnat_db_image_and_honours_the_pin():
    pod = _job()["spec"]["template"]["spec"]
    images = {c["name"]: c["image"] for c in pod["containers"] + pod["initContainers"]}
    assert images["decide"] == images["dump"] == "ghcr.io/londonaicentre/xnat-db:stag"
    pinned = _job("xnat.image.pin=v0.15.0", "global.image.tag=v0.16.0")["spec"]["template"]["spec"]
    assert pinned["initContainers"][0]["image"] == "ghcr.io/londonaicentre/xnat-db:v0.15.0", (
        "a pinned xnat must dump for the image it is pinned to — which makes the hook a no-op"
    )


def test_the_scaler_runs_the_mirrored_uv_image_by_digest():
    (container,) = [c for c in _job()["spec"]["template"]["spec"]["containers"] if c["name"] == "stop-xnat-web"]
    assert container["image"].startswith("ghcr.io/londonaicentre/mirror/astral-sh/uv@sha256:")


def test_the_pod_writes_the_volume_as_the_uid_that_owns_it():
    pod = _job()["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == 70
    assert pod["securityContext"]["runAsGroup"] == 70


def test_the_pod_mounts_the_xnat_db_claim_where_the_xnat_db_pod_runs():
    pod = _job()["spec"]["template"]["spec"]
    (volume,) = [v for v in pod["volumes"] if v["name"] == "data"]
    assert volume["persistentVolumeClaim"]["claimName"] == f"data-{FULLNAME}-xnat-db-0"
    (affinity,) = pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert affinity["topologyKey"] == "kubernetes.io/hostname"
    assert affinity["labelSelector"]["matchLabels"]["app.kubernetes.io/component"] == "xnat-db"


def test_the_mount_follows_pgdata_subdir():
    """On AKS/EKS xnat-db mounts a subPath of the volume; the hook must read the same directory."""
    pod = _job("xnat.db.persistence.pgdataSubdir=pgdata")["spec"]["template"]["spec"]
    mounts = [m for c in pod["containers"] + pod["initContainers"] for m in c["volumeMounts"] if m["name"] == "data"]
    assert mounts
    assert all(m["subPath"] == "pgdata" for m in mounts)


def test_the_role_can_patch_only_xnat_web_and_never_read_secrets():
    role = _hook()["Role"]
    (deployments,) = [r for r in role["rules"] if r["resources"] == ["deployments"]]
    assert deployments["resourceNames"] == [f"{FULLNAME}-xnat-web"]
    assert set(deployments["verbs"]) == {"get", "patch"}
    (pods,) = [r for r in role["rules"] if r["resources"] == ["pods"]]
    assert pods["verbs"] == ["list"]
    assert "secrets" not in str(role).lower()


def test_the_binding_names_the_hooks_own_service_account():
    hook = _hook()
    assert hook["RoleBinding"]["roleRef"]["name"] == NAME
    assert hook["RoleBinding"]["subjects"][0]["name"] == hook["ServiceAccount"]["metadata"]["name"] == NAME
    assert _job()["spec"]["template"]["spec"]["serviceAccountName"] == NAME


def test_the_api_server_policy_selects_this_pod_alone():
    policy = _hook()["NetworkPolicy"]
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "xnat-db-migrate"
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert [p["port"] for p in policy["spec"]["egress"][0]["ports"]] == [443, 6443]
    ports = _hook("autoUpgrade.apiServerPorts={8443}")["NetworkPolicy"]["spec"]["egress"][0]["ports"]
    assert [p["port"] for p in ports] == [8443], "one value names the API server's port for both hooks"
    assert "NetworkPolicy" not in _hook("networkPolicies.enabled=false")


def test_the_dump_reads_the_same_secret_keys_as_xnat_db():
    (container,) = [c for c in _job()["spec"]["template"]["spec"]["containers"] if c["name"] == "dump"]
    keys = {e["name"]: e["valueFrom"]["secretKeyRef"]["key"] for e in container["env"] if "valueFrom" in e}
    assert keys["POSTGRES_PASSWORD"] == "xnat-datasource-admin-password"
    assert keys["XNAT_DATASOURCE_PASSWORD"] == "xnat-datasource-password"
    names = {e["valueFrom"]["secretKeyRef"]["name"] for e in container["env"] if "valueFrom" in e}
    assert names == {"flip-trust-secrets"}  # pragma: allowlist secret


def test_the_job_deadline_leaves_the_scaler_time_to_put_xnat_web_back():
    job = _job("xnat.db.migrateDump.timeoutSeconds=600")
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 900
    pod = job["spec"]["template"]["spec"]
    (scaler,) = [c for c in pod["containers"] if c["name"] == "stop-xnat-web"]
    assert scaler["command"][scaler["command"].index("--timeout-seconds") + 1] == "600"
    (dump,) = [c for c in pod["containers"] if c["name"] == "dump"]
    assert {e["name"]: e["value"] for e in dump["env"] if "value" in e}["TIMEOUT_SECONDS"] == "600"


def test_the_scripts_travel_with_the_chart():
    data = _hook()["ConfigMap"]["data"]
    assert set(data) == {"xnat-db-migrate-decide.sh", "xnat-db-migrate-dump.sh", "xnat_db_migrate_scale.py"}
    assert "pg_dump -Fc" in data["xnat-db-migrate-dump.sh"]
    assert "def run(" in data["xnat_db_migrate_scale.py"]


def test_the_scaler_is_told_which_pods_to_wait_for():
    (scaler,) = [c for c in _job()["spec"]["template"]["spec"]["containers"] if c["name"] == "stop-xnat-web"]
    command = scaler["command"]
    assert command[command.index("--deployment") + 1] == f"{FULLNAME}-xnat-web"
    assert command[command.index("--selector") + 1] == (
        "app.kubernetes.io/instance=trust-release,app.kubernetes.io/component=xnat-web"
    )
    docs = _render()
    (deployment,) = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-xnat-web")]
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    for pair in command[command.index("--selector") + 1].split(","):
        key, value = pair.split("=")
        assert labels[key] == value, "the selector must match the pods the Deployment actually makes"
    assert deployment["spec"]["replicas"] == 1, (
        "the hook leaves xnat-web at 0 on success; the upgrade only brings it back because the "
        "Deployment renders an explicit replica count"
    )
