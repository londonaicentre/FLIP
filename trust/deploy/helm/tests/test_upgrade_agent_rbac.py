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

# The chart refuses the agent on a trust that runs XNAT until FLIP#2071, so every render that switches the agent on
# switches XNAT off.
AGENT_ON = ("--set", "autoUpgrade.enabled=true", "--set", "xnat.enabled=false")

# The trust's per-image pins and the kit-derived values differ only in what they enable, so
# rendering each variant is what makes the coverage check mean something.
VARIANTS = [
    ("defaults", [*AGENT_ON]),
    (
        "optional-workloads",
        [
            *AGENT_ON,
            "--set",
            "autoscaling.enabled=true",
            "--set",
            "podDisruptionBudget.enabled=true",
        ],
    ),
    # The agent cannot be rendered beside XNAT until FLIP#2071, so this variant checks the XNAT kinds against the
    # Role rendered without XNAT (the Role does not depend on it): the Role is ready when the guard goes.
    ("with-xnat", ["--set", "xnat.enabled=true"]),
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
    docs = _render(*AGENT_ON)
    for kind in ("ServiceAccount", "Role", "RoleBinding", "CronJob"):
        assert _agent(docs, kind)["metadata"]["namespace"] == "flip-trust"


def test_the_role_never_grants_secrets():
    """A `secrets` verb here would let the agent read every credential in the trust's namespace."""
    rendered = str(_agent(_render(*AGENT_ON), "Role"))
    assert "secrets" not in rendered.lower(), (
        "the agent reads its API key from a projected volume, which needs no RBAC; any secrets rule "
        "reopens the exposure #1644 exists to avoid"
    )


def test_the_api_key_arrives_as_a_one_key_projection():
    cronjob = _agent(_render(*AGENT_ON), "CronJob")
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    (volume,) = [v for v in pod["volumes"] if v["name"] == "kit"]
    assert volume["name"] == "kit"
    assert volume["secret"]["items"] == [{"key": "trust-api-key", "path": "trust-api-key"}], (
        "only the trust's API key may be projected; anything else is a credential the pod has no use for"
    )
    assert volume["secret"]["secretName"] == "flip-trust-secrets"  # pragma: allowlist secret


def _kit_secret(*extra: str) -> str:
    cronjob = _agent(_render(*AGENT_ON, *extra), "CronJob")
    (volume,) = [v for v in cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"] if v["name"] == "kit"]
    return volume["secret"]["secretName"]


def _service_secret(docs: list[dict]) -> set[str]:
    """Every Secret name the trust-api Deployment reads an env var from."""
    (deploy,) = [d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-trust-api")]
    names = set()
    for container in deploy["spec"]["template"]["spec"]["containers"]:
        for env in container.get("env", []):
            ref = env.get("valueFrom", {}).get("secretKeyRef")
            if ref:
                names.add(ref["name"])
    return names


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param((), id="default existing Secret"),
        pytest.param(("--set", "secrets.existingName=site-kit"), id="site-named existing Secret"),
    ],
)
def test_the_agent_reads_the_same_secret_as_the_trust_services(extra):
    """FLIP#1644: under any release name, the agent mounts the Secret trust-api reads, not a guessed name.

    Rendered with fullname ``site-release`` so ``<fullname>-secrets`` cannot coincide with the default
    ``secrets.existingName`` (it did for the release name ``flip-trust``, which hid a kind-cluster
    failure: the tick pod waited forever on a Secret that did not exist).
    """
    cmd = ("--set", "fullnameOverride=site-release", *extra)
    docs = _render(*AGENT_ON, *cmd)
    assert {_kit_secret(*cmd)} == _service_secret(docs)


def test_a_chart_created_secret_is_refused_with_the_agent():
    """With secrets.create the passwords are release values, stored in the record the agent reads.

    The chart's own Secret would also need `secrets` verbs to upgrade. So the render stops, naming
    the setting, instead of producing an agent that leaks or cannot work.
    """
    cmd = [
        "helm",
        "template",
        "flip-trust",
        str(CHART_DIR),
        "--set",
        "flClient.kitHostPath=/opt/flip/fl-kit",
        "--set",
        "autoUpgrade.enabled=true",
        "--set",
        "secrets.create=true",
        "--set",
        "secrets.data.trust-api-key=x",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=CHART_DIR)
    assert out.returncode != 0
    assert "secrets.create=false" in out.stderr


def test_a_chart_created_secret_still_renders_without_the_agent():
    docs = _render("--set", "secrets.create=true", "--set", "secrets.data.trust-api-key=x")
    assert any(d.get("kind") == "Secret" for d in docs)


def test_the_agent_finishes_inside_the_job_deadline():
    """The agent's budget is under activeDeadlineSeconds, so it reports FAILED before it is killed."""
    spec = _agent(_render(*AGENT_ON), "CronJob")["spec"]["jobTemplate"]["spec"]
    deadline = spec["activeDeadlineSeconds"]
    pod = spec["template"]["spec"]
    args = pod["containers"][0]["args"]
    budget = int(args[args.index("--budget-seconds") + 1])
    assert budget == deadline - 300
    # SIGTERM on the deadline is the backstop: the grace period has to cover the report's retries.
    assert pod["terminationGracePeriodSeconds"] >= 120


def test_a_deadline_too_short_for_the_report_is_refused():
    cmd = [
        "helm",
        "template",
        "flip-trust",
        str(CHART_DIR),
        "--set",
        "flClient.kitHostPath=/opt/flip/fl-kit",
        *AGENT_ON,
        "--set",
        "autoUpgrade.activeDeadlineSeconds=600",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=CHART_DIR)
    assert out.returncode != 0
    assert "activeDeadlineSeconds" in out.stderr


def test_kit_secret_name_overrides_the_default():
    assert _kit_secret("--set", "autoUpgrade.kitSecretName=agent-only") == "agent-only"  # pragma: allowlist secret


@pytest.mark.parametrize(("variant", "extra"), VARIANTS)
def test_the_role_covers_every_kind_the_chart_renders(variant, extra):
    """The rot guard: a template that renders a kind the Role lacks fails here, not on a live trust."""
    docs = _render(*extra)
    rules = _role_rules(docs if variant != "with-xnat" else _render(*AGENT_ON))
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
    rules = _role_rules(_render(*AGENT_ON))
    for rule in rules:
        verbs = set(rule["verbs"])
        if "pods" in rule["resources"]:
            assert verbs == {"get", "list"}, "pod access is read-only: the agent never creates one"
        else:
            assert WRITE_VERBS <= verbs, f"{rule['resources']} is missing {sorted(WRITE_VERBS - verbs)}"


def test_a_tick_cannot_overlap_the_next_one():
    spec = _agent(_render(*AGENT_ON), "CronJob")["spec"]
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["jobTemplate"]["spec"]["backoffLimit"] == 0, (
        "a retry inside the same hour re-runs an hour-old decision; the hub's lease is then the only "
        "thing standing between two upgrades"
    )


def test_the_tick_deadline_is_under_the_hubs_lease():
    """The hub's lease is 2h. A tick that outlives it would die holding it, and the release would
    only be re-attempted after expiry."""
    spec = _agent(_render(*AGENT_ON), "CronJob")["spec"]["jobTemplate"]["spec"]
    assert 0 < spec["activeDeadlineSeconds"] < 2 * 60 * 60


def test_the_agent_runs_against_the_published_chart():
    cronjob = _agent(_render(*AGENT_ON), "CronJob")
    container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    args = container["args"]
    # The agent is cluster-only, so it takes no mode switch; an old --mode would make argparse exit 2.
    assert "--mode" not in args
    for flag in ("--namespace", "--release", "--kit-name", "--hub", "--budget-seconds"):
        assert flag in args, flag
    assert "--chart-repository" in args
    assert args[args.index("--chart-repository") + 1].startswith("oci://")
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["HELM_DRIVER"] == "configmap", (
        "the agent and the deploy side must agree on the driver, or the release is invisible to it"
    )
    assert env["CENTRAL_HUB_API_URL"] == ""


def test_the_rendered_args_are_what_the_agent_accepts():
    """Run the agent's own argument parser on the CronJob's args: a renamed or removed flag exits 2 at 03:00."""
    import importlib.util
    import sys
    from unittest import mock

    spec = importlib.util.spec_from_file_location(
        "trust_upgrade_agent", CHART_DIR.parents[2] / "scripts" / "trust_upgrade_agent.py"
    )
    agent = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so the module has to be registered first.
    with mock.patch.dict(sys.modules, {"trust_upgrade_agent": agent}):
        spec.loader.exec_module(agent)
    cronjob = _agent(_render(*AGENT_ON), "CronJob")
    args = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["args"]
    with mock.patch.object(agent, "tick", return_value=0) as tick, mock.patch.object(agent.signal, "signal"):
        assert agent.main([str(a) for a in args]) == 0
    cluster, dry_run = tick.call_args.args
    assert cluster.chart_repository.startswith("oci://")
    assert dry_run is False
    agent.set_budget(None)


def test_the_agent_sheds_every_capability_it_does_not_need():
    pod = _agent(_render(*AGENT_ON), "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert pod["securityContext"]["runAsNonRoot"] is True


def test_the_agent_user_can_read_its_projected_key():
    """FLIP#1644: found on kind. The kubelet writes the key root-owned; at 0400 with no fsGroup the
    agent (uid 65534) got EACCES on every tick. fsGroup must hand the file to the pod's group, the
    mode must grant that group read, and nobody else."""
    pod = _agent(_render(*AGENT_ON), "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    ctx = pod["securityContext"]
    (volume,) = [v for v in pod["volumes"] if v["name"] == "kit"]
    mode = volume["secret"]["defaultMode"]
    assert ctx["runAsUser"] != 0
    assert ctx.get("fsGroup") == ctx.get("runAsGroup") == 65534
    assert mode & 0o040, f"group cannot read the key (mode {mode:o})"
    assert not mode & 0o007, f"others can read the key (mode {mode:o})"


def test_the_agent_reads_health_from_the_trust_api_service():
    """FLIP#1644: the pod's loopback has no trust-api; the URL must name the Service the chart renders."""
    docs = _render(*AGENT_ON, "--set", "trustApi.service.port=8123")
    pod = _agent(docs, "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    services = [d for d in docs if d.get("kind") == "Service" and d["metadata"]["name"] == "trust-api"]
    assert len(services) == 1, "the URL below names a Service called trust-api"
    port = services[0]["spec"]["ports"][0]["port"]
    assert env["TRUST_API_URL"] == f"http://trust-api.flip-trust.svc:{port}"
    assert port == 8123


def test_a_read_only_root_still_gets_a_writable_tmp_and_helm_home():
    """FLIP#1644, found on kind: with readOnlyRootFilesystem and no /tmp volume the upgrade died with
    "No usable temporary directory" after claiming the lease. Every path helm and tempfile write to
    must sit on a pod-scoped emptyDir."""
    pod = _agent(_render(*AGENT_ON), "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert "emptyDir" in volumes[mounts["/tmp"]], "/tmp must be a pod-scoped emptyDir"
    env = {e["name"]: e.get("value") for e in container["env"]}
    for var in ("HOME", "HELM_CACHE_HOME", "HELM_CONFIG_HOME", "HELM_DATA_HOME"):
        assert env.get(var, "").startswith("/tmp"), f"{var} must be under the writable /tmp"


def _render_result(*extra: str) -> subprocess.CompletedProcess[str]:
    cmd = ["helm", "template", "flip-trust", str(CHART_DIR), "--namespace", "flip-trust"]
    cmd += ["--set", "flClient.kitHostPath=/opt/flip/fl-kit", *extra]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=CHART_DIR)


def test_the_agent_is_refused_on_a_trust_that_runs_xnat():
    """Until FLIP#2071 the agent cannot run xnat-init or the xnat-db dump, so the chart refuses it beside XNAT."""
    out = _render_result("--set", "autoUpgrade.enabled=true", "--set", "xnat.enabled=true")
    assert out.returncode != 0
    assert "autoUpgrade.enabled cannot be used with XNAT until FLIP#2071" in out.stderr


def test_xnat_alone_and_the_agent_without_xnat_still_render():
    assert _render_result("--set", "xnat.enabled=true").returncode == 0
    assert _render_result(*AGENT_ON).returncode == 0
