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
assert the whole contract — the ConfigMap carrying the document verbatim, a READ-ONLY mount on
data-access-api, the NVFLARE fl-client reading only the section its init container extracts
(never the whole document, since researcher code runs there), nothing at all on the Flower
client, and pod-template annotations that change with what each pod reads.

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

[fl_privacy.nvflare]
policy = "percentile"
percentile = 10
gamma = 0.01
"""
EXTRACT_PATH = "/app/governance/governance.fl_privacy.toml"


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


def _init_container(pod: dict, name: str) -> dict:
    for container in pod["spec"].get("initContainers", []):
        if container["name"] == name:
            return container
    raise AssertionError(f"no init container {name!r} in the pod")


def test_a_document_reaches_data_access_api_read_only_as_a_configmap_file(tmp_path: Path) -> None:
    """ConfigMap content, env var and read-only subPath mount on the pod that enforces [access]."""
    rendered = _render("--set-file", f"governance.document={_document_file(tmp_path)}")
    configmap = _governance_configmap(rendered)

    assert configmap is not None, "a document is configured but no governance ConfigMap rendered"
    assert configmap["data"]["governance.toml"].strip() == DOCUMENT.strip(), (
        "the rendered ConfigMap does not carry the document verbatim — the mounted policy is not the one configured"
    )

    pod = _pods(rendered)["data-access-api"]
    container = _container(pod, "data-access-api")
    assert _env(container, "ACCESS_POLICY_FILE") == MOUNT_PATH
    (mount,) = [m for m in container.get("volumeMounts", []) if m["name"] == VOLUME_NAME]
    assert mount["mountPath"] == MOUNT_PATH, mount
    assert mount["subPath"] == "governance.toml", (
        "the mount is not a subPath of the ConfigMap key, so it would hide /app"
    )
    assert mount.get("readOnly") is True, "the policy mount is writable — a site could rewrite it"
    (volume,) = [v for v in pod["spec"].get("volumes", []) if v["name"] == VOLUME_NAME]
    assert volume["configMap"]["name"] == configmap["metadata"]["name"]


def test_the_nvflare_client_reads_only_its_extracted_section(tmp_path: Path) -> None:
    """The client container never sees the whole document: researcher code runs there as the
    client's own user. governance-extract reads it and writes the [fl_privacy] table alone."""
    rendered = _render("--set-file", f"governance.document={_document_file(tmp_path)}")
    pod = _pods(rendered)["fl-client"]
    container = _container(pod, "fl-client")
    extract = _init_container(pod, "governance-extract")

    assert _env(container, "ACCESS_POLICY_FILE") == EXTRACT_PATH
    assert not [m for m in container["volumeMounts"] if m["name"] == VOLUME_NAME], (
        "the fl-client container mounts the whole governance document"
    )
    (extract_mount,) = [m for m in container["volumeMounts"] if m["name"] == "governance-extract"]
    assert extract_mount["mountPath"] == "/app/governance"
    assert extract_mount.get("readOnly") is True

    script = " ".join(extract["command"])
    assert "site_policy --extract /app/governance.toml" in script, extract["command"]
    # A document with no [fl_privacy] section must not need an image that knows --extract.
    assert "grep -q fl_privacy /app/governance.toml" in script, extract["command"]
    assert extract["imagePullPolicy"] == container["imagePullPolicy"], "the init could run a stale cached image"
    assert extract["image"] == container["image"], "the extract must be written by the loader that reads it"
    (source,) = [m for m in extract["volumeMounts"] if m["name"] == VOLUME_NAME]
    assert source["mountPath"] == MOUNT_PATH
    assert source.get("readOnly") is True
    volumes = {v["name"]: v for v in pod["spec"]["volumes"]}
    assert "emptyDir" in volumes["governance-extract"], volumes["governance-extract"]


def test_editing_the_document_changes_the_rollout_checksum_on_both_pods(tmp_path: Path) -> None:
    """A ConfigMap edit restarts nothing; this annotation is what rolls the pod.

    Without it a trust could edit its access rules, watch the ConfigMap update, and keep
    serving the old policy until something unrelated restarted the pod. With no digest from
    sync-kit the fl-client falls back to the whole document's checksum, as data-access-api uses.
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


def test_the_fl_client_rolls_on_its_own_sections_digest(tmp_path: Path) -> None:
    """sync-kit's digest of the client's section drives the client's rollout, so an [access]-only
    edit — which changes the ConfigMap — leaves a running FL job alone."""
    digest = "ab" * 32
    rendered = _render(
        "--set-file",
        f"governance.document={_document_file(tmp_path)}",
        "--set",
        f"governance.flPrivacyChecksum={digest}",
    )
    pods = _pods(rendered)

    assert pods["fl-client"]["metadata"]["annotations"][CHECKSUM_ANNOTATION] == digest
    assert pods["data-access-api"]["metadata"]["annotations"][CHECKSUM_ANNOTATION] != digest


def test_the_flower_client_gets_none_of_the_document(tmp_path: Path) -> None:
    """Nothing on Flower reads a site privacy section. Mounting the document there reported a
    policy nothing enforced, and rolling the pod on every edit killed its jobs for nothing. The
    [disclosure]/[access] halves still reach data-access-api, as on NVFLARE."""
    rendered = _render("--set", "flBackend=flower", "--set-file", f"governance.document={_document_file(tmp_path)}")
    pods = _pods(rendered)
    pod = pods["fl-client"]
    container = _container(pod, "fl-client")

    # Positive control: the backend switch really happened.
    assert container["image"].endswith("flower-supernode:stag"), container["image"]
    assert _env(container, "ACCESS_POLICY_FILE") is None
    assert not [m for m in container["volumeMounts"] if m["name"] in (VOLUME_NAME, "governance-extract")]
    assert not [c for c in pod["spec"].get("initContainers", []) if c["name"] == "governance-extract"]
    assert CHECKSUM_ANNOTATION not in pod["metadata"].get("annotations", {})
    assert _env(_container(pods["data-access-api"], "data-access-api"), "ACCESS_POLICY_FILE") == MOUNT_PATH


def test_the_document_does_not_disturb_the_fl_site_privacy_env_wiring(tmp_path: Path) -> None:
    """The chart renders both sources as given; setting both is refused at runtime by the
    client and before deploy by sync-kit, not silently resolved here."""
    rendered = _render(
        "--set",
        "flClient.nvflare.sitePrivacy.policy=percentile",
        "--set-file",
        f"governance.document={_document_file(tmp_path)}",
    )
    container = _container(_pods(rendered)["fl-client"], "fl-client")

    assert _env(container, "FL_SITE_PRIVACY_POLICY") == "percentile"
    assert _env(container, "ACCESS_POLICY_FILE") == EXTRACT_PATH


def test_data_access_api_runs_at_the_kits_disclosure_floor() -> None:
    """The chart passed no COHORT_QUERY_THRESHOLD, so every chart-deployed trust ran at 10 whatever
    its kit said, while check-governance validated the document against the kit's value."""
    default = _container(_pods(_render())["data-access-api"], "data-access-api")
    raised = _container(
        _pods(_render("--set", "dataAccessApi.cohortQueryThreshold=25"))["data-access-api"], "data-access-api"
    )

    assert _env(default, "COHORT_QUERY_THRESHOLD") == "10"
    assert _env(raised, "COHORT_QUERY_THRESHOLD") == "25"
