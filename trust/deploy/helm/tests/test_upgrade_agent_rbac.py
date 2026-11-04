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

"""The auto-upgrade agent's identity (#1644): what it may do, and that it still covers the chart.

The Role behind this CronJob is a permanent privilege concentration inside a trust namespace, so
three things are pinned rather than left to review:

* **Coverage cannot rot.** The Role enumerates the kinds the chart renders instead of `*`. A new
  template that renders a kind the Role does not list would only fail at 03:00 on a live trust, so
  the render below is compared against the Role's rules on every run.
* **It cannot read Secrets.** The agent gets the trust's API key through a projected volume with an
  `items:` list, which the kubelet mounts without consulting the ServiceAccount. Any `secrets` verb
  in this Role would hand the pod every credential in the namespace, so the absence is asserted, not
  assumed.
* **Two ticks cannot run at once.** Two agents racing for the lease waste an upgrade and the loser
  can misdate which release moved, so `concurrencyPolicy` and `backoffLimit` are pinned too.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]

# Rendered kinds -> the (apiGroup, resource) the Role has to grant. Deliberately explicit: a kind
# missing from this map fails the test, so adding a template means deciding what the agent needs.
KIND_TO_RESOURCE = {
    "ConfigMap": ("", "configmaps"),
    "Service": ("", "services"),
    "ServiceAccount": ("", "serviceaccounts"),
    "PersistentVolumeClaim": ("", "persistentvolumeclaims"),
    "ResourceQuota": ("", "resourcequotas"),
    "Pod": ("", "pods"),
    "Deployment": ("apps", "deployments"),
    "StatefulSet": ("apps", "statefulsets"),
    "DaemonSet": ("apps", "daemonsets"),
    "Job": ("batch", "jobs"),
    "CronJob": ("batch", "cronjobs"),
    "NetworkPolicy": ("networking.k8s.io", "networkpolicies"),
    "Role": ("rbac.authorization.k8s.io", "roles"),
    "RoleBinding": ("rbac.authorization.k8s.io", "rolebindings"),
    "HorizontalPodAutoscaler": ("autoscaling", "horizontalpodautoscalers"),
    "PodDisruptionBudget": ("policy", "poddisruptionbudgets"),
}

# Everything an upgrade of a rendered object can need. `get`/`list` included because Helm compares
# the render against what is live before it applies.
WRITE_VERBS = {"get", "list", "create", "update", "patch", "delete"}

# The trust's per-image pins and the kit-derived values differ only in what they enable, so
# rendering each variant is what makes the coverage check mean something.
VARIANTS = [
    ("defaults", ["--set", "autoUpgrade.enabled=true"]),
    (
        "optional-workloads",
        [
            "--set",
            "autoUpgrade.enabled=true",
            "--set",
            "autoscaling.enabled=true",
            "--set",
            "podDisruptionBudget.enabled=true",
        ],
    ),
    (
        "with-xnat",
        ["--set", "autoUpgrade.enabled=true", "--set", "xnat.enabled=true"],
    ),
]


def _render(*extra: str) -> list[dict]:
    """Render the chart and return its objects. ``--api-versions`` is not needed: no CRDs here."""
    cmd = [
        "helm",
        "template",
        "flip-trust",
        str(CHART_DIR),
        "--namespace",
        "flip-trust",
        # The chart refuses to render without an FL kit path; the agent tests do not exercise it.
        "--set",
        "flClient.kitHostPath=/opt/flip/fl-kit",
        *extra,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=CHART_DIR)
    assert out.returncode == 0, f"helm template failed:\n{out.stderr}"
    return [doc for doc in yaml.safe_load_all(out.stdout) if doc]


def _agent(docs: list[dict], kind: str) -> dict:
    found = [
        d
        for d in docs
        if d.get("kind") == kind and str(d.get("metadata", {}).get("name", "")).endswith("-upgrade-agent")
    ]
    assert len(found) == 1, f"expected exactly one {kind} named *-upgrade-agent, got {len(found)}"
    return found[0]


def _role_rules(docs: list[dict]) -> list[dict]:
    return _agent(docs, "Role")["rules"]


def _covers(rules: list[dict], group: str, resource: str) -> bool:
    return any(group in (rule.get("apiGroups") or []) and resource in (rule.get("resources") or []) for rule in rules)


def test_the_agent_is_absent_until_it_is_switched_on():
    names = {str(d.get("metadata", {}).get("name", "")) for d in _render()}
    assert not [n for n in names if n.endswith("-upgrade-agent")], (
        "the agent must render nothing unless autoUpgrade.enabled is set: enabling it hands a "
        "ServiceAccount the ability to rewrite every workload in the namespace"
    )


def test_the_agent_renders_a_service_account_a_role_a_binding_and_a_cronjob():
    docs = _render("--set", "autoUpgrade.enabled=true")
    for kind in ("ServiceAccount", "Role", "RoleBinding", "CronJob"):
        assert _agent(docs, kind)["metadata"]["namespace"] == "flip-trust"


def test_the_role_never_grants_secrets():
    """A `secrets` verb here would let the agent read every credential in the trust's namespace."""
    rendered = str(_agent(_render("--set", "autoUpgrade.enabled=true"), "Role"))
    assert "secrets" not in rendered.lower(), (
        "the agent reads its API key from a projected volume, which needs no RBAC; any secrets rule "
        "reopens the exposure #1644 exists to avoid"
    )


def test_the_api_key_arrives_as_a_one_key_projection():
    cronjob = _agent(_render("--set", "autoUpgrade.enabled=true"), "CronJob")
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    (volume,) = pod["volumes"]
    assert volume["name"] == "kit"
    assert volume["secret"]["items"] == [{"key": "trust-api-key", "path": "trust-api-key"}], (
        "only the trust's API key may be projected; anything else is a credential the pod has no use for"
    )
    assert volume["secret"]["secretName"] == "flip-trust-secrets"  # pragma: allowlist secret


@pytest.mark.parametrize("variant,extra", VARIANTS)
def test_the_role_covers_every_kind_the_chart_renders(variant, extra):
    """The rot guard: a template that renders a kind the Role lacks fails here, not on a live trust."""
    docs = _render(*extra)
    rules = _role_rules(docs)
    missing = []
    for doc in docs:
        kind = doc.get("kind")
        if kind == "Secret":
            pytest.fail(
                "the chart renders a Secret, and this Role has no secrets verbs by design — the "
                "agent cannot upgrade a release that creates its own Secret (set secrets.create=false)"
            )
        if kind not in KIND_TO_RESOURCE:
            pytest.fail(f"{variant}: {kind} is not in KIND_TO_RESOURCE — decide what the agent needs for it")
        group, resource = KIND_TO_RESOURCE[kind]
        if not _covers(rules, group, resource):
            missing.append(f"{kind} ({group or 'core'}/{resource})")
    assert not missing, f"{variant}: the Role does not cover {', '.join(sorted(set(missing)))}"


def test_the_role_grants_the_verbs_an_upgrade_needs():
    rules = _role_rules(_render("--set", "autoUpgrade.enabled=true"))
    for rule in rules:
        verbs = set(rule["verbs"])
        if "pods" in rule["resources"]:
            assert verbs == {"get", "list"}, "pod access is read-only: the agent never creates one"
        else:
            assert WRITE_VERBS <= verbs, f"{rule['resources']} is missing {sorted(WRITE_VERBS - verbs)}"


def test_a_tick_cannot_overlap_the_next_one():
    spec = _agent(_render("--set", "autoUpgrade.enabled=true"), "CronJob")["spec"]
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["jobTemplate"]["spec"]["backoffLimit"] == 0, (
        "a retry inside the same hour re-runs an hour-old decision; the hub's lease is then the only "
        "thing standing between two upgrades"
    )


def test_the_tick_deadline_is_under_the_hubs_lease():
    """The hub's lease is 2h. A tick that outlives it would die holding it, and the release would
    only be re-attempted after expiry."""
    spec = _agent(_render("--set", "autoUpgrade.enabled=true"), "CronJob")["spec"]["jobTemplate"]["spec"]
    assert 0 < spec["activeDeadlineSeconds"] < 2 * 60 * 60


def test_the_agent_runs_the_cluster_mode_against_the_published_chart():
    cronjob = _agent(_render("--set", "autoUpgrade.enabled=true"), "CronJob")
    container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    args = container["args"]
    assert "cluster" in args and "--mode" in args
    assert "--chart-repository" in args
    assert args[args.index("--chart-repository") + 1].startswith("oci://")
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["HELM_DRIVER"] == "configmap", (
        "the agent and the deploy side must agree on the driver, or the release is invisible to it"
    )
    assert env["CENTRAL_HUB_API_URL"] == ""


def test_the_agent_sheds_every_capability_it_does_not_need():
    pod = _agent(_render("--set", "autoUpgrade.enabled=true"), "CronJob")["spec"]["jobTemplate"]["spec"]["template"][
        "spec"
    ]
    container = pod["containers"][0]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert pod["securityContext"]["runAsNonRoot"] is True
