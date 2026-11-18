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

"""``scripts/stage-to-node.sh`` stages the kit and the vocabulary onto any cluster's node (FLIP#1958).

``stage-kit`` and ``stage-vocab`` used ``docker exec`` / ``docker cp`` into the node container, which exists only on
kind; on k3s the operator had to place the kit by hand with root on the host. A kind node keeps the docker path; any
other node gets a short-lived root pod pinned to it with the destination as a hostPath, and ``kubectl cp``. The fake
docker here really runs ``exec`` and ``cp`` on the local filesystem, so the kind path is judged by what lands in the
destination; the fake kubectl records the pod's manifest and the order of its calls.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
SCRIPT = CHART_DIR / "scripts" / "stage-to-node.sh"
KIT_SUBDIRS = "local startup transfer"

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$STAGE_LOG"
case "$1" in
  inspect) [ -n "$KIND_LABEL" ] || exit 1; printf '%s' "$KIND_LABEL" ;;
  exec) shift 2; "$@" ;;
  cp) cp -R "$2" "${3#*:}" ;;
esac
"""

FAKE_KUBECTL = """#!/bin/sh
echo "kubectl $*" >> "$STAGE_LOG"
case "$*" in
  *"get nodes"*) printf 'node-1' ;;
  *"create namespace"*) printf 'apiVersion: v1\\nkind: Namespace\\n' ;;
  *"apply -f -"*) manifest=$(cat); printf '%s\n' "$manifest" >> "$STAGE_MANIFESTS"
    case "$manifest" in *"kind: Pod"*) [ -z "$FAIL_APPLY" ] || {
      echo 'pods "flip-stage" is forbidden: violates PodSecurity "baseline:latest"' >&2; exit 1; } ;; esac ;;
  *"wait pod"*) [ -z "$FAIL_WAIT" ] || exit 1 ;;
  *" cp "*) [ -z "$FAIL_CP" ] || exit 1 ;;
esac
exit 0
"""


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    src = tmp_path / "Trust_6"
    (src / "startup").mkdir(parents=True)
    (src / "startup" / "fed_client.json").write_text("{}")
    (src / "local").mkdir()
    (src / "local" / "resources.json").write_text("{}")
    return src


def _stage(tmp_path: Path, *args: str, kind: bool, docker: bool = True, **env: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", FAKE_DOCKER), ("kubectl", FAKE_KUBECTL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log, manifests = tmp_path / "calls.log", tmp_path / "manifests.yaml"
    run_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "STAGE_LOG": str(log),
        "STAGE_MANIFESTS": str(manifests),
        "KIND_LABEL": "flip-dev" if kind else "",
        **({} if docker else {"DOCKER": str(tmp_path / "no-docker")}),
        **env,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--context", "ctx", *args], capture_output=True, text=True, env=run_env, timeout=30
    )
    calls = log.read_text().splitlines() if log.exists() else []
    docs = [d for d in yaml.safe_load_all(manifests.read_text()) if d] if manifests.exists() else []
    return result, calls, docs


def _index(calls: list[str], needle: str) -> int:
    return next(i for i, call in enumerate(calls) if needle in call)


def test_a_kind_node_gets_the_kit_through_docker_wiped_completed_and_private(tmp_path, kit):
    dest = tmp_path / "node" / "fl-kit"
    (dest / "startup").mkdir(parents=True)
    (dest / "startup" / "old-client.key").write_text("revoked")
    uid = os.getuid()

    result, calls, docs = _stage(
        tmp_path, "--src", str(kit), "--dest", str(dest), "--subdirs", KIT_SUBDIRS, "--owner", str(uid), kind=True
    )

    assert result.returncode == 0, result.stderr
    assert not docs, "no pod on kind"
    assert not (dest / "startup" / "old-client.key").exists(), "the previous kit is wiped before the copy"
    assert (dest / "startup" / "fed_client.json").read_text() == "{}"
    assert (dest / "transfer").is_dir(), "subPaths the chart mounts exist before the pod starts"
    for path in (dest, dest / "startup", dest / "transfer", dest / "startup" / "fed_client.json"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, f"{path} is readable by others: {oct(mode)}"
        assert path.stat().st_uid == uid
    assert _index(calls, "-mindepth 1 -delete") < _index(calls, "docker cp")


def test_any_other_node_gets_a_root_pod_pinned_to_it_with_the_destination_as_hostpath(tmp_path, kit):
    result, calls, docs = _stage(
        tmp_path, "--src", str(kit), "--dest", "/data/flip-k8s/fl-kit", "--subdirs", KIT_SUBDIRS, "--owner", "1000",
        kind=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    pod = next(d for d in docs if d["kind"] == "Pod")
    spec = pod["spec"]
    assert spec["nodeName"] == "node-1"
    assert spec["volumes"] == [
        {"name": "dest", "hostPath": {"path": "/data/flip-k8s/fl-kit", "type": "DirectoryOrCreate"}}
    ]
    assert spec["containers"][0]["securityContext"] == {"runAsUser": 0}
    assert spec["containers"][0]["volumeMounts"] == [{"name": "dest", "mountPath": "/stage"}]
    assert not [c for c in calls if c.startswith("docker") and "inspect" not in c], "nothing goes through docker"

    name = pod["metadata"]["name"]
    order = [
        _index(calls, "apply -f -"),
        _index(calls, f"wait pod {name}"),
        _index(calls, "-mindepth 1 -delete"),
        _index(calls, f" cp {kit}/. {name}:/stage"),
        _index(calls, f"exec {name} -- sh -c"),
        _index(calls, f"delete pod {name}"),
    ]
    assert order == sorted(order), calls
    finish = calls[order[4]]
    assert finish.index("mkdir -p") < finish.index("chmod -R go-rwx") < finish.index("chown -R 1000:1000")


def test_a_host_without_docker_uses_the_pod(tmp_path, kit):
    result, _calls, docs = _stage(
        tmp_path, "--src", str(kit), "--dest", "/opt/flip/fl-kit", "--owner", "1000", kind=True, docker=False
    )

    assert result.returncode == 0, result.stderr
    assert [d for d in docs if d["kind"] == "Pod"]


def test_the_vocabulary_is_left_world_readable_and_owned_by_nobody_new(tmp_path):
    bundle = tmp_path / "vocab"
    bundle.mkdir()
    (bundle / "CONCEPT.csv").write_text("concept_id\n")
    (bundle / "CONCEPT.csv").chmod(0o600)
    dest = tmp_path / "node" / "omop-vocab"

    result, calls, _docs = _stage(tmp_path, "--src", str(bundle), "--dest", str(dest), "--world-readable", kind=True)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE((dest / "CONCEPT.csv").stat().st_mode) & 0o004, "the loader runs as the image user"
    assert not [c for c in calls if "chown" in c]


def test_the_pod_is_removed_when_a_step_fails(tmp_path, kit):
    result, calls, docs = _stage(
        tmp_path, "--src", str(kit), "--dest", "/opt/flip/fl-kit", "--owner", "1000", kind=False, FAIL_CP="1"
    )

    assert result.returncode != 0
    name = next(d for d in docs if d["kind"] == "Pod")["metadata"]["name"]
    assert any(f"delete pod {name}" in c for c in calls)
    assert not any(f"exec {name} -- sh -c" in c for c in calls), "nothing is chowned after a failed copy"


@pytest.mark.parametrize(
    ("failure", "says"),
    [("FAIL_APPLY", "would not run the staging pod"), ("FAIL_WAIT", "did not start within 180s")],
    ids=["refused", "never-ready"],
)
def test_a_cluster_that_will_not_run_the_pod_gets_the_command_to_stage_by_hand(tmp_path, kit, failure, says):
    """FLIP#1477: under Pod Security baseline (or with the image out of reach) the operator is told what to run."""
    result, calls, _docs = _stage(
        tmp_path, "--src", str(kit), "--dest", "/opt/flip/fl-kit", "--subdirs", KIT_SUBDIRS, "--owner", "1000",
        kind=False, **{failure: "1"},
    )  # fmt: skip

    assert result.returncode == 1
    assert says in result.stderr
    assert (
        "sudo sh -c 'mkdir -p /opt/flip/fl-kit && find /opt/flip/fl-kit -mindepth 1 -delete"
        f" && cp -a <your copy of {kit}>/. /opt/flip/fl-kit/"
        " && for d in local startup transfer; do mkdir -p /opt/flip/fl-kit/$d; done"
        " && chmod -R go-rwx /opt/flip/fl-kit && chown -R 1000:1000 /opt/flip/fl-kit'"
    ) in result.stderr
    assert not any(" cp " in c for c in calls), "nothing is copied"


def test_the_by_hand_command_for_the_vocabulary_keeps_it_world_readable(tmp_path):
    bundle = tmp_path / "vocab"
    bundle.mkdir()

    result, _calls, _docs = _stage(
        tmp_path, "--src", str(bundle), "--dest", "/opt/flip/omop-vocab", "--world-readable", kind=False,
        FAIL_APPLY="1",
    )  # fmt: skip

    assert result.returncode == 1
    assert f"cp -a <your copy of {bundle}>/. /opt/flip/omop-vocab/ && chmod -R a+rX /opt/flip/omop-vocab'" in (
        result.stderr
    )


@pytest.mark.parametrize(
    "args",
    [
        ("--dest", "/", "--owner", "1000"),
        ("--dest", "", "--owner", "1000"),
        ("--dest", "relative/fl-kit", "--owner", "1000"),
        ("--dest", "/opt/flip/fl-kit"),
        ("--dest", "/opt/flip/fl-kit", "--owner", "flip"),
    ],
    ids=["root", "empty", "relative", "no-owner", "named-owner"],
)
def test_a_destination_or_owner_that_could_do_harm_is_refused_before_any_call(tmp_path, kit, args):
    result, calls, _docs = _stage(tmp_path, "--src", str(kit), *args, kind=False)

    assert result.returncode == 2
    assert calls == []


def test_the_staging_image_is_the_charts_pinned_busybox():
    """The pod's image is the chart's own digest-pinned mirror (trust/deploy/mirror-images.manifest)."""
    script = SCRIPT.read_text()
    image = re.search(r'^STAGE_IMAGE="([^"]+)"', script, re.M).group(1)
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
    init = values["xnat"]["web"]["initImage"]

    assert image == f"{init['repository']}:{init['tag']}@{init['digest']}"
