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

"""Unit tests for sync_k8s_kit.py's generated Helm override — the values whose
absence or misplacement installs a release that looks healthy and is not: the
FL-server port-only egress rule (FLIP#593 pt.3), the OMOP vocab-load bucket
(FLIP#842/843), and flClient.kitHostPath (FLIP#965/#1009).

The egress rule is PORT-ONLY (FL_SERVER_PORT added to ``allowedEgressPorts``),
not a resolved-/32 CIDR pin: the FL server's internet-facing NLB has AWS-managed
IPs that rotate on recreation, so a pinned /32 would go stale. A port-only rule
is immune to that drift and renders deterministically across runs."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "sync_k8s_kit.py"
_spec = importlib.util.spec_from_file_location("sync_k8s_kit", _SCRIPT)
sync_k8s_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_k8s_kit)


_FL_KIT = {
    "TRUST_NAME": "Kubernetes Trust",
    "FL_KIT_SLOT": "Trust_K8s",
    "FL_KIT_SLOT_NUMBER": "0",
    "CENTRAL_HUB_API_URL": "https://stag.flip.aicentre.co.uk/api",
    "FL_BACKEND": "nvflare",
    "AICENTRE_BUCKET_NAME": "flipstag-aicentre",
    "FLARE_KIT_DATE": "20260312",
    "NLB_SUBDOMAIN": "fl.stag.flip.aicentre.co.uk",
    "FL_SERVER_PORT": "8002",
}


def test_render_override_emits_port_only_egress_rule():
    """The egress block is a port-only rule on FL_SERVER_PORT (no pinned CIDRs)."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert "networkPolicies:" in out
    assert "allowedEgressPorts:" in out
    # FL-server gRPC port present as a {port, protocol} entry...
    assert "- port: 8002" in out
    # ...and no resolved-/32 CIDR pinning remains.
    assert "allowedEgressCIDRsWithPorts" not in out
    assert "/32" not in out


def test_render_override_restates_default_egress_ports():
    """Helm replaces list values wholesale, so the override must restate the
    chart-default egress ports (DNS/HTTP/HTTPS) alongside the FL-server port —
    otherwise the override would wipe the default egress allowlist."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    for port in ("- port: 53", "- port: 80", "- port: 443", "- port: 8002"):
        assert port in out, f"missing default/FL egress port: {port}"


def test_render_override_is_stable_across_runs():
    """The whole point of #593 pt.3: regenerating the override is idempotent —
    a second render with the same inputs reproduces the egress rule byte-for-byte.
    With a port-only rule this holds unconditionally (no DNS in the path)."""
    first = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    second = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert first == second
    assert "allowedEgressPorts:" in second
    assert "- port: 8002" in second


def test_render_override_uses_kit_fl_server_port():
    """The port is sourced from the kit (FL_SERVER_PORT), not hardcoded — e.g.
    Flower's 9092 rather than NVFLARE's 8002."""
    kit = {**_FL_KIT, "FL_BACKEND": "flower", "FL_SERVER_PORT": "9092"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "- port: 9092" in out
    assert "- port: 8002" not in out


def test_render_override_omits_block_without_fl_port():
    """No FL_SERVER_PORT in the kit ⇒ no egress override emitted (the chart
    default egress allowlist from values.yaml applies unchanged)."""
    kit = {k: v for k, v in _FL_KIT.items() if k != "FL_SERVER_PORT"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "networkPolicies:" not in out
    assert "allowedEgressPorts" not in out


def test_render_override_sets_vocab_load_bucket_from_kit():
    """The chart's vocab-load hook is gated on omopDb.vocabLoad.s3Bucket, whose
    default is empty (the licensed bundle has no public mirror — FLIP#842/843).
    The override must supply the env's OWN bucket, or the deployment installs
    cleanly with no vocabulary and cohort queries silently match nothing."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    # Anchored on newlines, not loose substrings: the nesting IS the meaning here.
    # Emitted one level deeper (e.g. folded into the preceding trustApi block) each
    # fragment would still match, and `trustApi` is additionalProperties:true in
    # values.schema.json — so Helm would silently accept the misplaced key.
    assert "\nomopDb:\n  vocabLoad:\n    s3Bucket: flipstag-aicentre\n" in out


def test_render_override_omits_vocab_load_without_bucket():
    """No AICENTRE_BUCKET_NAME in the kit ⇒ no vocabLoad block, leaving the chart's
    empty default in place (rather than emitting an empty bucket that reads as a
    configured one)."""
    kit = {k: v for k, v in _FL_KIT.items() if k != "AICENTRE_BUCKET_NAME"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    # Not `"omopDb:" not in out` — that would break spuriously the day an unrelated
    # omopDb key joins the override. s3Bucket appears nowhere else: the fl-client
    # section carries only `kitHostPath:` now that the chart never fetches the kit.
    assert "vocabLoad" not in out
    assert "s3Bucket" not in out


def test_render_override_can_leave_the_vocab_load_off():
    """A cluster with no AWS route (AKS, FLIP#1390) must not get the S3 vocab-load hook: it would
    fail, and as a post-install hook it fails the whole release."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2", vocab_load=False)
    assert "vocabLoad" not in out


def test_vocab_load_follows_the_operator_and_never_a_scaffolding_default():
    stag = {**_FL_KIT, "AICENTRE_BUCKET_NAME": "flipstag-aicentre"}
    scaffold = {**_FL_KIT, "AICENTRE_BUCKET_NAME": "flipdev-aicentre"}
    assert sync_k8s_kit.should_load_vocab(stag, "stag", requested=True)
    assert not sync_k8s_kit.should_load_vocab(stag, "stag", requested=False), "the operator opted out"
    # #881: .env.example ships the dev bucket, which no other environment can read.
    assert not sync_k8s_kit.should_load_vocab(scaffold, "lza-stag", requested=True)
    assert sync_k8s_kit.should_load_vocab(scaffold, "development", requested=True)


def test_render_override_carries_the_kits_seed_partition():
    """SOURCE_TRUST names the mock-data partition a trust is seeded with; without it the chart falls
    back to the slot number, and a trust on Trust_3 finds no third partition (FLIP#1390)."""
    out = sync_k8s_kit.render_override({**_FL_KIT, "SOURCE_TRUST": "1"}, "Trust_K8s", "eu-west-2")
    assert '\ntrustData:\n  seed:\n    sourceTrust: "1"\n' in out
    assert "sourceTrust" not in sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")


def test_render_override_holds_pinned_images_back_from_the_release():
    """The kit's OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG opt-outs reach the chart as `image.pin`,
    beside the global.image.tag the kit's DOCKER_TAG sets; a kit without them emits none."""
    kit = {**_FL_KIT, "DOCKER_TAG": "sha-badcff1", "OMOP_DB_TAG": "latest", "XNAT_TAG": "v0.6.0"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "\nglobal:\n  image:\n    tag: sha-badcff1\n" in out
    assert "\nomopDb:\n  image:\n    pin: latest\n" in out
    assert "\nxnat:\n  image:\n    pin: v0.6.0\n" in out
    assert "orthanc:" not in out
    plain = sync_k8s_kit.render_override({**_FL_KIT, "DOCKER_TAG": "v0.6.0"}, "Trust_K8s", "eu-west-2")
    assert "pin:" not in plain


def test_render_override_pins_the_fl_client_to_an_immutable_docker_fl_tag():
    """One flClient block (a second top-level key would be a YAML duplicate): kitHostPath and,
    when the kit's DOCKER_FL_TAG is a release or sha- tag, image.pin. `dev` / `stag` emit none."""
    out = sync_k8s_kit.render_override({**_FL_KIT, "DOCKER_FL_TAG": "sha-03fdb61"}, "Trust_K8s", "eu-west-2")
    assert out.count("\nflClient:\n") == 1
    assert "\nflClient:\n  kitHostPath: /opt/flip/fl-kit\n  image:\n    pin: sha-03fdb61\n" in out
    for tag in ("dev", "stag"):
        out = sync_k8s_kit.render_override({**_FL_KIT, "DOCKER_FL_TAG": tag}, "Trust_K8s", "eu-west-2")
        assert "pin:" not in out, tag


def test_render_override_sets_kit_host_path_from_kit():
    """flClient.kitHostPath comes from the kit's FL_KIT_DIR. It is `required` in the
    chart, so an override that omits it (or nests it wrong) fails the render — and one
    that emits the wrong path leaves the pod Pending on `hostPath type check failed`,
    with nothing naming the staged path it disagrees with. Anchored on newlines because
    the nesting IS the meaning: emitted a level deeper the fragment would still match."""
    kit = {**_FL_KIT, "FL_KIT_DIR": "/srv/kits/Trust_K8s"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "\nflClient:\n  kitHostPath: /srv/kits/Trust_K8s\n" in out


def test_render_override_falls_back_to_canonical_kit_host_path():
    """A kit with no FL_KIT_DIR still renders the canonical path — the same value every
    shipped kit file sets, the Ansible EC2/on-prem plays stage to, and the chart
    Makefile's KIT_DEST defaults to. Any other fallback would render a path nothing
    staged to (FLIP#1009 review)."""
    kit = {k: v for k, v in _FL_KIT.items() if k != "FL_KIT_DIR"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "\nflClient:\n  kitHostPath: /opt/flip/fl-kit\n" in out


def test_render_override_kit_host_path_ignores_blank_fl_kit_dir():
    """A kit file carrying an empty FL_KIT_DIR (a commented-out or cleared value) must
    fall back rather than emit `kitHostPath:` with no value — Helm would read that as
    null and the chart's `required` guard would fire at deploy time instead of here."""
    kit = {**_FL_KIT, "FL_KIT_DIR": "   "}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "\nflClient:\n  kitHostPath: /opt/flip/fl-kit\n" in out


_DOCUMENT = (
    "[disclosure]\nmin_cohort_size = 25\n\n"
    '[[access.rule]]\nid = "withdrawn"\naction = "cohort.accession_ids"\neffect = "deny"\n'
    'projects = ["3f1c9a70-5e42-4d8b-9c31-7a2e6b4f8d15"]\n\n'
    '[fl_privacy.nvflare]\npolicy = "percentile"\n'
)


def _override(tmp_path, document: str = _DOCUMENT, **kit: str) -> str:
    (tmp_path / "governance.toml").write_text(document)
    return sync_k8s_kit.render_override(
        {**_FL_KIT, "ACCESS_POLICY_FILE": "governance.toml", **kit}, "Trust_K8s", "eu-west-2", trust_dir=tmp_path
    )


def _checksum(override: str) -> str:
    return next(line.split(": ", 1)[1] for line in override.splitlines() if "flPrivacyChecksum:" in line)


def test_render_override_embeds_the_governance_document(tmp_path):
    """The kit's ACCESS_POLICY_FILE names a path on the deploy host; the chart needs the
    document ITSELF (it is rendered into a ConfigMap and mounted read-only — a host path
    means nothing inside a pod). Relative paths resolve against the trust tree, which is
    what the Compose stack's --project-directory trust does for its own mount of the same
    file. Anchored on the block scalar's indentation, because the nesting is the meaning:
    a document emitted a level deeper would still be readable YAML and would silently
    become an empty string."""
    (tmp_path / "policies").mkdir()
    (tmp_path / "policies" / "governance.Trust_K8s.toml").write_text(
        '[disclosure]\nmin_cohort_size = 25\n\n[fl_privacy.nvflare]\npolicy = "percentile"\n'
    )
    kit = {**_FL_KIT, "ACCESS_POLICY_FILE": "policies/governance.Trust_K8s.toml"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2", trust_dir=tmp_path)

    assert (
        "  document: |\n"
        '    [disclosure]\n    min_cohort_size = 25\n\n    [fl_privacy.nvflare]\n    policy = "percentile"\n'
    ) in out
    assert "\ngovernance:\n  flPrivacyChecksum: " in out


def test_render_override_accepts_an_absolute_governance_path(tmp_path):
    """An operator who pointed ACCESS_POLICY_FILE at an absolute path gets that file —
    resolving it against the trust tree would append a relative path to it and read
    something else entirely."""
    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 30\n")
    kit = {**_FL_KIT, "ACCESS_POLICY_FILE": str(document)}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2", trust_dir=tmp_path / "elsewhere")

    assert "\n    min_cohort_size = 30\n" in out


def test_render_override_fails_loudly_on_an_unreadable_governance_document(tmp_path):
    """A document named but not readable must stop the sync, not be skipped: the release
    would otherwise install clean, mount nothing, and enforce the platform defaults the
    operator believes their rules replaced — the silent-ignore defect this whole feature
    exists to remove."""
    kit = {**_FL_KIT, "ACCESS_POLICY_FILE": "governance.Missing.toml"}

    with pytest.raises(sync_k8s_kit.GovernanceDocumentError) as excinfo:
        sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2", trust_dir=tmp_path)

    assert "ACCESS_POLICY_FILE" in str(excinfo.value)
    assert "governance.Missing.toml" in str(excinfo.value)


def test_render_override_refuses_a_document_data_access_api_would_refuse(tmp_path):
    """Validated with data-access-api's own loader against the kit's floor, so the pods never
    crash-loop on a document the deploy accepted."""
    with pytest.raises(sync_k8s_kit.GovernanceDocumentError, match="below the configured COHORT_QUERY_THRESHOLD"):
        _override(tmp_path, "[disclosure]\nmin_cohort_size = 20\n", COHORT_QUERY_THRESHOLD="25")


def test_render_override_refuses_a_site_privacy_section_on_flower(tmp_path):
    """Nothing on Flower enforces [fl_privacy.nvflare]; deploying it would read as active."""
    with pytest.raises(sync_k8s_kit.GovernanceDocumentError, match="nothing on flower enforces it"):
        _override(tmp_path, FL_BACKEND="flower")


def test_render_override_refuses_a_filter_in_both_the_document_and_the_kit(tmp_path):
    with pytest.raises(sync_k8s_kit.GovernanceDocumentError, match="configured twice"):
        _override(tmp_path, FL_SITE_PRIVACY_POLICY="percentile")


def test_render_override_refuses_a_misspelt_site_privacy_variable():
    with pytest.raises(sync_k8s_kit.GovernanceDocumentError, match="FL_SITE_PRIVACY_PERCENTIL"):
        sync_k8s_kit.render_override(
            {**_FL_KIT, "FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTIL": "5"},
            "Trust_K8s",
            "eu-west-2",
        )


def test_render_override_passes_the_kits_site_privacy_filter_to_the_chart():
    """The chart's flClient.nvflare.sitePrivacy was never filled from the kit, so a Helm trust
    ran unfiltered while check-governance validated the kit's filter."""
    out = sync_k8s_kit.render_override(
        {**_FL_KIT, "FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": "25"},
        "Trust_K8s",
        "eu-west-2",
    )

    assert '\n  nvflare:\n    sitePrivacy:\n      policy: "percentile"\n      percentile: "25"\n' in out


def test_render_override_passes_the_kits_disclosure_floor_to_the_chart():
    """The chart never passed COHORT_QUERY_THRESHOLD, so every chart-deployed trust ran at 10."""
    out = sync_k8s_kit.render_override({**_FL_KIT, "COHORT_QUERY_THRESHOLD": "15"}, "Trust_K8s", "eu-west-2")

    assert "\ndataAccessApi:\n  cohortQueryThreshold: 15\n" in out


def test_the_fl_client_checksum_tracks_its_section_only(tmp_path):
    """An [access]-only edit must not roll the fl-client (it interrupts a running job); an edit
    to its own section must."""
    base = _checksum(_override(tmp_path))
    access_edit = _checksum(_override(tmp_path, _DOCUMENT.replace("min_cohort_size = 25", "min_cohort_size = 40")))
    fl_edit = _checksum(_override(tmp_path, _DOCUMENT + "percentile = 30\n"))

    assert base == access_edit
    assert base != fl_edit


def test_a_flower_trust_gets_no_fl_client_checksum(tmp_path):
    """Nothing on the Flower client reads the document, so nothing there should roll with it."""
    out = _override(tmp_path, "[disclosure]\nmin_cohort_size = 25\n", FL_BACKEND="flower")

    assert "flPrivacyChecksum" not in out
    assert "\ngovernance:\n  document: |\n" in out


def test_render_override_omits_governance_without_the_kit_variable():
    """No ACCESS_POLICY_FILE in the kit ⇒ no governance block, leaving the chart's empty
    default in place (an unconditional `document:` would deploy the empty string as a
    document and mount a file the services then refuse to parse)."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")

    assert "governance" not in out
    assert "document:" not in out


# ── Helm Secret ownership ────────────────────────────────────────────────
# The Helm Secret-ownership stamping in sync_k8s_kit.py
# (FLIP#595) — so a Secret this script creates can be adopted by a subsequent
# `helm upgrade --install` instead of aborting the release.


def test_derive_release_name_strips_chart_suffix():
    assert sync_k8s_kit.derive_release_name("trust-release-flip-trust-secrets") == "trust-release"
    assert sync_k8s_kit.derive_release_name("my-trust-flip-trust-secrets") == "my-trust"


def test_derive_release_name_without_suffix_is_identity():
    assert sync_k8s_kit.derive_release_name("some-other-secret") == "some-other-secret"


def test_stamp_helm_ownership_issues_label_and_annotate(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", lambda args, **kw: calls.append(args) or None)
    sync_k8s_kit.stamp_helm_ownership("trust-release-flip-trust-secrets", "flip-trust", "trust-release")

    label = next(c for c in calls if c[1] == "label")
    annotate = next(c for c in calls if c[1] == "annotate")
    assert "app.kubernetes.io/managed-by=Helm" in label
    assert "--overwrite" in label
    assert "--overwrite" in annotate
    assert "meta.helm.sh/release-name=trust-release" in annotate
    assert "meta.helm.sh/release-namespace=flip-trust" in annotate
    assert ["-n", "flip-trust"] == label[4:6]  # namespaced


def test_kube_context_reaches_every_kubectl_call(monkeypatch):
    """`make … KUBE_CONTEXT=<ctx>` must act on that cluster, not whichever one kubectl points at."""
    calls = []
    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", lambda args, **kw: calls.append(args) or None)
    monkeypatch.setattr(sync_k8s_kit, "KUBECTL", ["kubectl", "--context", "kind-flip-kch"])
    sync_k8s_kit.stamp_helm_ownership("trust-release-flip-trust-secrets", "flip-trust", "trust-release")
    assert calls
    assert all(c[:3] == ["kubectl", "--context", "kind-flip-kch"] for c in calls)


def test_patch_k8s_secret_stamps_ownership_on_create(monkeypatch):
    """A freshly-created Secret must be stamped Helm-owned (the #595 fix)."""
    calls = []

    class _Res:
        returncode = 1  # secret does not yet exist → create path

    def fake_run(args, **kw):
        calls.append(args)
        return _Res()

    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", fake_run)
    sync_k8s_kit.patch_k8s_secret(
        "trust-release-flip-trust-secrets",
        "flip-trust",
        {"trust-api-key": "x"},
        "trust-release",
    )
    verbs = [c[1] for c in calls]
    assert "create" in verbs  # secret created
    assert "label" in verbs  # ...then stamped
    assert "annotate" in verbs


def test_patch_k8s_secret_heals_ownership_on_existing(monkeypatch):
    """An already-present Secret must be merge-patched and (re-)stamped Helm-owned
    with --overwrite, so a pre-existing unowned Secret becomes adoptable (the #595
    heal path)."""
    calls = []

    class _Res:
        returncode = 0  # secret already exists → patch path

    def fake_run(args, **kw):
        calls.append(args)
        return _Res()

    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", fake_run)
    sync_k8s_kit.patch_k8s_secret(
        "trust-release-flip-trust-secrets",
        "flip-trust",
        {"trust-api-key": "x"},
        "trust-release",
    )
    verbs = [c[1] for c in calls]
    assert "patch" in verbs  # existing secret merge-patched
    assert "label" in verbs  # ...then (re-)stamped
    assert "annotate" in verbs
    label = next(c for c in calls if c[1] == "label")
    annotate = next(c for c in calls if c[1] == "annotate")
    assert "--overwrite" in label
    assert "--overwrite" in annotate


def test_stamp_helm_ownership_default_namespace(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", lambda args, **kw: calls.append(args) or None)
    sync_k8s_kit.stamp_helm_ownership("s", "", "rel")
    annotate = next(c for c in calls if c[1] == "annotate")
    assert "meta.helm.sh/release-namespace=default" in annotate


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_a_kit_with_nothing_to_validate_never_loads_the_validators(monkeypatch):
    """sync-kit runs on the deploy host's python3. The validators import tomllib (3.11+), so a
    trust with no document and no FL_SITE_PRIVACY_* must not need them — on Ubuntu 22.04's 3.10
    every sync-kit, deploy-trust-k8s and upgrade-trust-k8s would otherwise crash."""

    def unavailable():
        raise AssertionError("the validators were loaded for a kit with nothing to validate")

    monkeypatch.setattr(sync_k8s_kit, "_site_policy", unavailable)

    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")

    assert "governance" not in out


def test_an_interpreter_without_tomllib_is_a_clear_refusal(tmp_path, monkeypatch):
    """With something to validate on a 3.10 host, the sync stops with the reason, not a traceback."""
    import builtins

    real_import = builtins.__import__

    def no_tomllib(name, *args, **kwargs):
        if name == "tomllib":
            raise ModuleNotFoundError("No module named 'tomllib'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tomllib)
    monkeypatch.delitem(sys.modules, "tomllib", raising=False)

    with pytest.raises(sync_k8s_kit.GovernanceDocumentError, match="Python 3.11"):
        sync_k8s_kit.render_override(
            {**_FL_KIT, "FL_SITE_PRIVACY_POLICY": "percentile"}, "Trust_K8s", "eu-west-2", trust_dir=tmp_path
        )


def test_make_passes_no_vocab_load_through_to_every_sync():
    """NO_VOCAB_LOAD=1 is how an AKS operator keeps the S3 hook out (FLIP#1390); dry-run the target."""
    import shutil
    import subprocess

    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    chart = Path(__file__).resolve().parents[1]
    run = lambda *extra: subprocess.run(  # noqa: E731
        ["make", "-n", "-C", str(chart), "sync-kit", "KIT=Trust_K8s", "PROD=stag", *extra],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "--no-vocab-load" in run("NO_VOCAB_LOAD=1")
    assert "--no-vocab-load" not in run()
