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

"""The rendered chart's governance wiring (FLIP#1259), on both backends.

The Kubernetes trust was the deployment shape that silently ignored a trust's governance
document: the Compose stack mounts it and points ``ACCESS_POLICY_FILE`` at it, the chart did
that for nothing, and the two shapes looked equally healthy. These tests render the chart and
assert the whole contract — the ConfigMap carrying the document verbatim, a READ-ONLY mount at
the same path on both the data-access-api and the fl-client pods, the env var that makes the
services read it, and a pod-template annotation that changes with the document so an edit
rolls the pods rather than leaving the previous rules in force.

They also pin the other half: with no document configured, none of it renders — the feature is
opt-in and a default install's pod specs must be the ones they were before it existed.

Rendered rather than text-parsed because this is what a cluster actually receives, and because
"the mount is read-only" and "the annotation tracks the document" are properties only the
rendered pod template has. Skipped without helm; the chart workflow's helm-template job runs it,
and tests/test_governance_wiring.py covers the template text where helm is unavailable.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
KIT_HOST_PATH = "/opt/flip/fl-kit"
MOUNT_PATH = "/app/governance.toml"
VOLUME_NAME = "governance"
CONFIGMAP_SUFFIX = "-governance"
CHECKSUM_ANNOTATION = "checksum/governance"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

#: A minimal but valid document: every section the feature defines, so the render proves the
#: TOML survives as one opaque string (the chart never parses it — the services do).
DOCUMENT = """# Rendering-test governance document
[disclosure]
min_cohort_size = 25

[[access.rule]]
id = "no-raw-export"
action = "cohort.dataframe"
effect = "deny"

[fl_privacy]
policy = "percentile"
percentile = 10
gamma = 0.01
"""


def _render(*extra: str) -> str:
    """Render the chart, with the FL kit path the chart requires whenever the client is on.

    Args:
        *extra (str): Further ``helm template`` arguments (``--set``/``--set-file`` pairs).

    Returns:
        str: The rendered manifests.
    """
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", f"flClient.kitHostPath={KIT_HOST_PATH}"]
    args += list(extra)
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"helm template failed: {result.stderr}"
    return result.stdout


def _document_file(tmp_path: Path, content: str = DOCUMENT, name: str = "governance.toml") -> Path:
    """Write a governance document for ``--set-file`` to pick up.

    Args:
        tmp_path (Path): Per-test scratch directory.
        content (str): Document body.
        name (str): Filename to write.

    Returns:
        Path: The written file.
    """
    path = tmp_path / name
    path.write_text(content)
    return path


def _pods(rendered: str) -> dict[str, dict]:
    """Map each ``app.kubernetes.io/component`` to its Deployment's pod template.

    Args:
        rendered (str): Rendered manifests.

    Returns:
        dict[str, dict]: Component name -> ``spec.template``.
    """
    pods: dict[str, dict] = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        component = doc["metadata"]["labels"].get("app.kubernetes.io/component", "")
        pods[component] = doc["spec"]["template"]
    return pods


def _container(pod: dict, name: str) -> dict:
    """The named container in a pod template.

    Args:
        pod (dict): Pod template (``spec.template``).
        name (str): Container name.

    Returns:
        dict: The container spec.
    """
    for container in pod["spec"]["containers"]:
        if container["name"] == name:
            return container
    raise AssertionError(f"no container {name!r} in the pod; has it been renamed?")


def _governance_configmap(rendered: str) -> dict | None:
    """The chart's governance ConfigMap, or ``None`` when the render produced none.

    Args:
        rendered (str): Rendered manifests.

    Returns:
        dict | None: The ConfigMap document.
    """
    for doc in yaml.safe_load_all(rendered):
        if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"].endswith(CONFIGMAP_SUFFIX):
            return doc
    return None


def _env(container: dict, name: str) -> str | None:
    """The literal value of an env var in a container, or ``None`` when it is absent.

    Args:
        container (dict): Container spec.
        name (str): Env var name.

    Returns:
        str | None: The value.
    """
    for entry in container.get("env", []):
        if entry["name"] == name:
            return entry.get("value")
    return None


def test_without_a_document_no_governance_object_renders() -> None:
    """A default install must be exactly the install it was before the feature existed.

    Asserted positively as well as negatively — the two pod templates and the data-access-api
    ConfigMap have to be in the render, or "no governance anywhere" would pass on an empty
    document and prove nothing.
    """
    rendered = _render()

    # Positive control: the render really is the trust stack.
    assert "flip-trust-data-access-api" in rendered
    assert _governance_configmap(rendered) is None, "the governance ConfigMap rendered with no document configured"
    assert "ACCESS_POLICY_FILE" not in rendered, "ACCESS_POLICY_FILE is set with no document configured"

    pods = _pods(rendered)
    assert "data-access-api" in pods, sorted(pods)
    assert "fl-client" in pods, sorted(pods)

    for component, pod in pods.items():
        assert CHECKSUM_ANNOTATION not in pod["metadata"].get("annotations", {}), (
            f"{component}: carries {CHECKSUM_ANNOTATION} with no document configured — an unrelated edit could roll it"
        )
        assert not [v for v in pod["spec"].get("volumes", []) if v["name"] == VOLUME_NAME], (
            f"{component}: mounts a {VOLUME_NAME} volume that no ConfigMap backs"
        )
        for container in pod["spec"]["containers"]:
            assert not [m for m in container.get("volumeMounts", []) if m["name"] == VOLUME_NAME]
            assert _env(container, "ACCESS_POLICY_FILE") is None


def test_a_document_reaches_both_pods_read_only_as_a_configmap_file(tmp_path: Path) -> None:
    """The whole wiring on one render: ConfigMap content, env var, read-only subPath mount.

    Both pods because the document has two readers — data-access-api for [disclosure]/[access],
    the NVFLARE client for [fl_privacy]. The mount is read-only and the env var points at the
    mounted path (not a host path, which means nothing inside the pod).
    """
    rendered = _render("--set-file", f"governance.document={_document_file(tmp_path)}")
    configmap = _governance_configmap(rendered)

    assert configmap is not None, "a document is configured but no governance ConfigMap rendered"
    assert configmap["data"]["governance.toml"].strip() == DOCUMENT.strip(), (
        "the rendered ConfigMap does not carry the document verbatim — the mounted policy is not the one configured"
    )

    pods = _pods(rendered)
    for component, container_name in (("data-access-api", "data-access-api"), ("fl-client", "fl-client")):
        pod = pods[component]
        container = _container(pod, container_name)

        assert _env(container, "ACCESS_POLICY_FILE") == MOUNT_PATH, (
            f"{component}: ACCESS_POLICY_FILE is not {MOUNT_PATH} — the service would fall back to the platform "
            "defaults while the operator believes the document is in force"
        )
        mounts = [m for m in container.get("volumeMounts", []) if m["name"] == VOLUME_NAME]
        assert len(mounts) == 1, f"{component}: expected one {VOLUME_NAME} volumeMount, found {mounts}"
        mount = mounts[0]
        assert mount["mountPath"] == MOUNT_PATH, mount
        assert mount["subPath"] == "governance.toml", (
            f"{component}: the mount is not a subPath of the ConfigMap key, so it would hide /app or the kit"
        )
        assert mount.get("readOnly") is True, f"{component}: the policy mount is writable — a site could rewrite it"

        volumes = [v for v in pod["spec"].get("volumes", []) if v["name"] == VOLUME_NAME]
        assert len(volumes) == 1, f"{component}: expected one {VOLUME_NAME} volume, found {volumes}"
        assert volumes[0]["configMap"]["name"] == configmap["metadata"]["name"], (
            f"{component}: the volume does not reference the rendered ConfigMap"
        )


def test_editing_the_document_changes_the_rollout_checksum_on_both_pods(tmp_path: Path) -> None:
    """A ConfigMap edit restarts nothing; this annotation is what rolls the pod.

    Without it a trust could edit its access rules, watch the ConfigMap update, and keep
    serving the old policy until something unrelated restarted the pod — the failure the
    FL_SITE_PRIVACY_* comment in templates/fl-client.yaml already documents for its own values.
    """
    first = _render("--set-file", f"governance.document={_document_file(tmp_path, DOCUMENT, 'a.toml')}")
    second = _render(
        "--set-file",
        f"governance.document={_document_file(tmp_path, DOCUMENT.replace('= 25', '= 40'), 'b.toml')}",
    )
    repeat = _render("--set-file", f"governance.document={_document_file(tmp_path, DOCUMENT, 'c.toml')}")

    def checksums(rendered: str) -> dict[str, str]:
        return {
            component: pod["metadata"].get("annotations", {}).get(CHECKSUM_ANNOTATION)
            for component, pod in _pods(rendered).items()
        }

    before, after, again = checksums(first), checksums(second), checksums(repeat)
    for component in ("data-access-api", "fl-client"):
        assert before[component], f"{component}: no {CHECKSUM_ANNOTATION} annotation with a document configured"
        assert re.fullmatch(r"[0-9a-f]{64}", before[component]), before[component]
        assert before[component] != after[component], (
            f"{component}: the annotation did not change with the document — editing the policy would not roll it"
        )
        assert before[component] == again[component], f"{component}: the annotation is not deterministic"


def test_the_flower_client_gets_the_same_mount_and_env(tmp_path: Path) -> None:
    """Both backends carry the document, even though only the NVFLARE client reads it today.

    The Flower SuperNode has no site_policy hook, so the file is inert there — but the shape
    must not differ by backend. A release whose document is present on one backend's pod and
    silently absent on the other is the class of defect this wiring exists to remove, and the
    [disclosure]/[access] halves are enforced for a Flower trust exactly as for an NVFLARE one
    (they are data-access-api's).
    """
    rendered = _render(
        "--set", "flBackend=flower", "--set-file", f"governance.document={_document_file(tmp_path)}"
    )
    pod = _pods(rendered)["fl-client"]
    container = _container(pod, "fl-client")

    # Positive control: the backend switch really happened.
    assert container["image"].endswith("flower-supernode:stag"), container["image"]
    assert _env(container, "ACCESS_POLICY_FILE") == MOUNT_PATH
    assert [m["mountPath"] for m in container["volumeMounts"] if m["name"] == VOLUME_NAME] == [MOUNT_PATH]
    assert pod["metadata"]["annotations"].get(CHECKSUM_ANNOTATION), f"no {CHECKSUM_ANNOTATION} on the flower client"


def test_the_document_does_not_disturb_the_fl_site_privacy_env_wiring(tmp_path: Path) -> None:
    """The two sources stay independent in the chart: the document wins at runtime, not here.

    site_policy.py resolves precedence (the document's [fl_privacy] wins, and the client warns
    which source it used), so the chart must keep rendering FL_SITE_PRIVACY_* for a trust that
    has not migrated — dropping them here would change the meaning of an existing values file.
    """
    rendered = _render(
        "--set", "flClient.nvflare.sitePrivacy.policy=percentile", "--set-file",
        f"governance.document={_document_file(tmp_path)}",
    )
    container = _container(_pods(rendered)["fl-client"], "fl-client")

    assert _env(container, "FL_SITE_PRIVACY_POLICY") == "percentile"
    assert _env(container, "ACCESS_POLICY_FILE") == MOUNT_PATH
