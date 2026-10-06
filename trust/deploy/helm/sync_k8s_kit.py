#!/usr/bin/env python3
#
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
#

"""Sync a registered FLIP trust *kit* into the Kubernetes Helm deployment.

This is the glue between the hub-side registration flow and the trust-side
Helm chart. It does NOT register anything with the hub — registration is done
once, centrally, by the DB-backed `register_trust` CLI:

    make new-trust TRUST_CODE=<CODE> TRUST_NAME="..."
    make -C deploy/providers/AWS register-trusts KIT=<CODE> PROD=<env>
    make sync-trust-kit KIT=<CODE> PROD=<env>      # fills the Hub-shared block

…which mints the per-trust credentials and writes them, together with the
Hub-shared block (AES key, hub URL, FL settings), into the kit file
``trust/.env.<CODE>.<env>``.

This script reads that kit file and:
  1. Patches the trust's per-trust secrets (trust-api-key,
     trust-internal-service-key[-header], aes-key-base64) into the chart's
     Kubernetes Secret — only the keys the kit actually carries, leaving the
     infrastructure secrets (XNAT / OMOP / S3) created by the chart untouched.
  2. Writes a Helm values override (``k8s-trust-<CODE>.yaml``) carrying the
     non-secret, deployment-specific settings the chart needs: the hub URL,
     FL backend, AWS region, trust number, where the FL kit sits on the node,
     the release image pins, the OMOP vocabulary bucket, the FL-server
     egress port and — when the kit names one — the trust's governance
     document (FLIP#1259), read from its ``ACCESS_POLICY_FILE`` and embedded
     whole (a path on the deploy host means nothing inside a pod).

The plaintext keys are never written to disk — they go straight from the kit
file into the Kubernetes Secret over kubectl's TLS channel. The generated
override file (``k8s-trust-*.yaml``) is gitignored and contains no secrets.

Usage:
  python3 sync_k8s_kit.py --kit Trust_K8s --env stag
  python3 sync_k8s_kit.py --kit Trust_2          # env auto-detected from $PROD
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]

# Maps kit env-var name -> Kubernetes Secret key. Only the per-trust secrets
# the kit owns; infra secrets (xnat-*, omop-*, s3-*) are left as the chart
# created them.
KIT_TO_SECRET_KEY = {  # maps env-var name -> Secret key (names only, no values)
    "TRUST_API_KEY": "trust-api-key",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY": "trust-internal-service-key",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY_HEADER": "trust-internal-service-key-header",  # pragma: allowlist secret
    "AES_KEY_BASE64": "aes-key-base64",  # pragma: allowlist secret
}

# Kit keys that must hold a real value (not a scaffolding placeholder) before a
# deployment can poll the hub. AES_KEY_BASE64 and CENTRAL_HUB_API_URL live in
# the Hub-shared block filled by `make sync-trust-kit`.
REQUIRED_KIT_KEYS = ("TRUST_API_KEY", "AES_KEY_BASE64", "CENTRAL_HUB_API_URL")

# The chart names its Secret "<release>-flip-trust-secrets" (fullname + suffix).
# Stripping the suffix recovers the Helm release name, used to stamp ownership
# metadata so `helm upgrade --install` can adopt a Secret this script created.
_SECRET_NAME_SUFFIX = "-flip-trust-secrets"


def derive_release_name(secret_name: str) -> str:
    """Best-effort Helm release name from the chart Secret name (FLIP#595)."""
    if secret_name.endswith(_SECRET_NAME_SUFFIX):
        return secret_name[: -len(_SECRET_NAME_SUFFIX)]
    return secret_name


def _is_placeholder(value: str) -> bool:
    """True if a kit value is empty or still a scaffolding placeholder (<...>)."""
    v = value.strip()
    return not v or (v.startswith("<") and v.endswith(">"))


def read_env_vars(env_path: Path) -> dict[str, str]:
    """Read key=value pairs from an env file, stripping surrounding quotes."""
    pairs: dict[str, str] = {}
    if not env_path.exists():
        return pairs
    with open(env_path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            pairs[key.strip()] = value.strip().strip('"').strip("'")
    return pairs


#: The kubectl invocation every call below starts from. `--kube-context` appends
#: `--context <ctx>` so the whole sync acts on one named cluster rather than whichever
#: cluster kubectl currently points at (a workstation with several kind clusters).
KUBECTL: list[str] = ["kubectl"]


#: An immutable, pullable image tag — a release or a CI sha tag (scripts/site_upgrade.py).
IMMUTABLE_IMAGE_TAG = re.compile(r"^(?:v\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?|sha-[0-9a-f]{7})$")


class GovernanceDocumentError(RuntimeError):
    """The kit's governance configuration cannot be deployed: its document is unreadable or
    invalid, or its site privacy filter is invalid or one this trust's backend would not
    enforce."""


def _site_policy() -> ModuleType:
    """``flip.nvflare.site_policy``, loaded by path — stdlib-only, and the loader the fl-client runs."""
    path = REPO_ROOT / "flip-utils" / "flip" / "nvflare" / "site_policy.py"
    spec = importlib.util.spec_from_file_location("_flip_site_policy", path)
    if spec is None or spec.loader is None:
        raise GovernanceDocumentError(f"cannot load the site privacy validator from {path}")
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves its own module through sys.modules, so register before executing.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as e:
        del sys.modules[spec.name]
        raise _needs_newer_python(e) from None
    return module


def _needs_newer_python(e: ModuleNotFoundError) -> GovernanceDocumentError:
    return GovernanceDocumentError(
        f"validating the kit's governance configuration needs Python 3.11 or newer (tomllib), and this "
        f"python3 is {sys.version.split()[0]} ({e})"
    )


def _validate_governance(kit: dict[str, str], document: Path | None) -> str | None:
    """Validate what the pods will enforce, with the services' own loaders, before deploying it.

    A chart-deployed trust otherwise learns of an invalid document from a crash-looping pod, and
    of a filter its backend ignores from nothing at all. Both loaders are stdlib-only.

    Args:
        kit: The trust's kit file as a mapping.
        document: The governance document, or ``None`` when the kit names none.

    Returns:
        str | None: sha256 of the fl-client's extracted section on NVFLARE (what rolls the
        fl-client), or ``None`` when there is no document or the backend is not NVFLARE.

    Raises:
        GovernanceDocumentError: On an invalid document, an invalid FL_SITE_PRIVACY_* value, a
            filter set in both, or a filter on a backend that does not enforce one.
    """
    env = {key: value for key, value in kit.items() if key.startswith("FL_SITE_PRIVACY_")}
    backend = (kit.get("FL_BACKEND", "").strip() or "nvflare").lower()
    floor = kit.get("COHORT_QUERY_THRESHOLD", "").strip() or "10"
    if not floor.isdigit() or int(floor) < 1:
        raise GovernanceDocumentError(f"COHORT_QUERY_THRESHOLD={floor!r} is not a positive integer")
    if document is None and not any(value.strip() for value in env.values()):
        # Nothing to validate — and the validators need tomllib (3.11+), which a trust using
        # neither control must not: sync-kit runs on the deploy host's own python3.
        return None
    site_policy = _site_policy()

    if document is not None:
        service_root = str(REPO_ROOT / "trust" / "data-access-api")
        if service_root not in sys.path:
            sys.path.insert(0, service_root)
        try:
            from data_access_api.policy import AccessPolicyError, load_policy
        except ModuleNotFoundError as e:
            raise _needs_newer_python(e) from None

        try:
            load_policy(path=str(document), floor=int(floor))
        except AccessPolicyError as e:
            raise GovernanceDocumentError(str(e)) from None
        env["ACCESS_POLICY_FILE"] = str(document)

    try:
        site_policy.check_backend(env, backend)
        site_policy.resolve_policy(env)
        if document is None or backend != "nvflare":
            return None
        with tempfile.TemporaryDirectory() as tmp:
            extract = Path(tmp) / "governance.fl_privacy.toml"
            site_policy.extract(str(document), extract)
            return hashlib.sha256(extract.read_bytes()).hexdigest()
    except site_policy.SitePolicyError as e:
        raise GovernanceDocumentError(str(e)) from None


def _kubectl_ns(namespace: str) -> list[str]:
    """kubectl namespace args (empty for the default namespace)."""
    return ["-n", namespace] if namespace and namespace != "default" else []


def stamp_helm_ownership(secret_name: str, namespace: str, release_name: str) -> None:
    """Stamp Helm ownership metadata on a Secret so `helm upgrade --install`
    adopts it instead of aborting (FLIP#595).

    Without these, a fresh ``helm install`` errors:
        Secret "<name>" ... cannot be imported into the current release:
        missing key "app.kubernetes.io/managed-by": must be set to "Helm"

    Idempotent via ``--overwrite`` — also heals a Secret created by an earlier
    run of this script (or a prior chart version) that lacks the metadata.
    """
    ns = _kubectl_ns(namespace)
    rel_ns = namespace or "default"
    subprocess.run(
        [*KUBECTL, "label", "secret", secret_name, *ns, "app.kubernetes.io/managed-by=Helm", "--overwrite"],
        check=True,
    )
    subprocess.run(
        [
            *KUBECTL,
            "annotate",
            "secret",
            secret_name,
            *ns,
            f"meta.helm.sh/release-name={release_name}",
            f"meta.helm.sh/release-namespace={rel_ns}",
            "--overwrite",
        ],
        check=True,
    )


def patch_k8s_secret(secret_name: str, namespace: str, entries: dict[str, str], release_name: str) -> None:
    """Create or merge-patch a Kubernetes Secret with the given entries.

    Values are passed to kubectl directly — never written to disk. The Secret is
    stamped with Helm ownership metadata (see :func:`stamp_helm_ownership`) so a
    later ``helm upgrade --install`` adopts it rather than failing (FLIP#595).

    Args:
        secret_name: Secret resource name (chart default: ``<release>-flip-trust-secrets``).
        namespace: Kubernetes namespace.
        entries: Mapping of Secret key -> plaintext value.
        release_name: Helm release name to record as the Secret's owner.
    """
    ns = _kubectl_ns(namespace)
    exists = (
        subprocess.run(
            [*KUBECTL, "get", "secret", secret_name, *ns],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )

    if exists:
        data = {k: base64.b64encode(v.encode()).decode() for k, v in entries.items()}
        patch = json.dumps({"data": data})
        subprocess.run(
            [*KUBECTL, "patch", "secret", secret_name, *ns, "--type", "merge", "-p", patch],
            check=True,
        )
        verb = "Patched"
    else:
        args = [*KUBECTL, "create", "secret", "generic", secret_name, *ns]
        for k, v in entries.items():
            args += ["--from-literal", f"{k}={v}"]
        subprocess.run(args, check=True)
        verb = "Created"

    stamp_helm_ownership(secret_name, namespace, release_name)

    print(f"  ✓ {verb} Kubernetes Secret entries (Helm-owned by release '{release_name}').")
    print("    Plaintext values went straight to Kubernetes — not persisted to disk.")


#: The generated Helm secrets values file, alongside the chart. It is the OTHER writer of
#: the same Secret keys (scripts/generate_values.py renders it from the kit), which is what
#: makes `align_values_secrets` necessary — see its docstring.
VALUES_SECRETS_NAME = "values-secrets.yaml"


def align_values_secrets(path: Path, entries: dict[str, str]) -> list[str]:
    """Write the kit's per-trust secrets into ``values-secrets.yaml`` as well (FLIP#1360).

    Helm 4 applies the release server-side, and SSA raises a conflict when an apply would
    *change* a field another manager owns. `patch-kit-secrets` writes
    ``.data.{aes-key-base64,trust-api-key,trust-internal-service-key}`` with field manager
    ``kubectl-patch``; the chart templates those same keys from ``values-secrets.yaml``. So
    the next ``helm upgrade`` dies with::

        Apply failed with 3 conflicts: conflict with "kubectl-patch" using v1: .data.trust-api-key

    — but only when the two disagree. That conflict is therefore not noise: it is the API
    server reporting that the cluster's live keys and the chart's rendered keys have drifted,
    and the release stays on its last working revision rather than overwriting live
    credentials. The fix is to remove the *disagreement*, not to silence the report:

    * ``--force-conflicts`` on the upgrade would hand ownership back by overwriting the live
      keys with whatever ``values-secrets.yaml`` holds — a stale value, or none at all, since
      ``templates/secrets.yaml`` omits an empty slot. A redeploy would silently revert the
      trust's API key and the trust would poll the hub with a dead credential.
    * Re-patching with ``--field-manager=helm`` does NOT help: the patch is an *Update*
      operation, a different managedFields entry from helm's *Apply*, so the conflict is
      merely re-reported as ``conflict with "helm"`` (verified against Helm 4.3 on Kubernetes
      1.37).
    * ``kubectl apply --server-side`` of the three keys as ``helm`` is worse still: a partial
      apply prunes every key that manager owns and did not list, emptying the XNAT/OMOP/
      Orthanc slots out of the same Secret (also verified).

    Keeping the generated values file in step with what we just patched means helm's apply
    is a no-op on those fields, so SSA raises nothing, the kit stays the single source of
    truth, and no redeploy can revert a key. Only the slots the kit owns are touched; every
    other slot, comment and the file's 0600 mode are preserved.

    Args:
        path: Path to ``values-secrets.yaml``. A missing file is not an error — the chart
            then renders ``secrets.create: false`` and manages no Secret of its own.
        entries: Secret key -> plaintext value, as patched into the cluster.

    Returns:
        list[str]: The Secret key NAMES realigned (never values), for the operator log.
    """
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    aligned: list[str] = []
    for secret_key, value in entries.items():
        pattern = re.compile(rf"^(\s*){re.escape(secret_key)}:\s.*$")
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if match:
                replacement = f'{match.group(1)}{secret_key}: "{value}"'
                if line != replacement:
                    lines[i] = replacement
                    aligned.append(secret_key)
                break
        else:
            # The slot is absent (an older generated file, or a kit that did not carry the
            # key when it was generated). Add it under secrets.data rather than leaving the
            # chart to render a Secret without it.
            try:
                data_at = next(i for i, line in enumerate(lines) if re.match(r"^\s{2}data:\s*$", line))
            except StopIteration:
                continue
            lines.insert(data_at + 1, f'    {secret_key}: "{value}"')
            aligned.append(secret_key)

    if aligned:
        # Rewrite in place (the file is already 0600 and gitignored); never widen the mode.
        path.write_text("\n".join(lines) + "\n")
    return aligned


def build_secret_entries(kit: dict[str, str]) -> dict[str, str]:
    """Pick the per-trust secret keys the kit carries with real values."""
    entries: dict[str, str] = {}
    for kit_key, secret_key in KIT_TO_SECRET_KEY.items():
        value = kit.get(kit_key, "")
        if not _is_placeholder(value):
            entries[secret_key] = value
    return entries


def render_override(kit: dict[str, str], code: str, aws_region: str, trust_dir: Path | None = None) -> str:
    """Render the Helm values override (no secrets) from kit settings.

    Args:
        kit: The trust's kit file as a mapping.
        code: Trust CODE, used in the generated comments.
        aws_region: AWS region for the S3-backed Jobs.
        trust_dir: Directory a relative ``ACCESS_POLICY_FILE`` resolves against — the trust
            tree, which is what Compose's ``--project-directory trust`` does for its own
            mount of the same file. Only consulted when the kit names a document.

    Returns:
        str: The override file's contents.

    Raises:
        GovernanceDocumentError: If the kit's governance configuration cannot be deployed — an
            ``ACCESS_POLICY_FILE`` that cannot be read or is invalid, or a site privacy filter
            that is invalid or would be ignored by this trust's backend.
    """
    trust_name = kit.get("TRUST_NAME", code)
    slot_number = kit.get("FL_KIT_SLOT_NUMBER", "").strip()
    hub_url = kit.get("CENTRAL_HUB_API_URL", "")
    fl_backend = kit.get("FL_BACKEND", "nvflare").strip() or "nvflare"
    kit_bucket = kit.get("AICENTRE_BUCKET_NAME", "").strip()
    # Where the operator placed this trust's kit ON THE NODE. FL_KIT_DIR is the same
    # value the Compose path uses, so one kit field serves both deployment shapes.
    # The fallback is the canonical FLIP kit path: every shipped kit sets FL_KIT_DIR
    # to it, the Ansible EC2/on-prem plays stage to it, and the chart Makefile's
    # KIT_DEST defaults to it — a kit missing the field still renders a path the
    # default `make stage-kit` actually wrote to.
    kit_host_path = kit.get("FL_KIT_DIR", "").strip() or "/opt/flip/fl-kit"

    lines = [
        "# ── Generated by sync_k8s_kit.py — DO NOT edit by hand ────────────────",
        f"# Per-deployment override for trust '{code}' (kit: trust/.env.{code}.<env>).",
        "# Contains no secrets — the per-trust keys live in the Kubernetes Secret,",
        "# patched into the cluster by the same sync-kit run. Regenerate by re-running",
        "#   make -C trust/deploy/helm sync-kit KIT=" + code + " PROD=<env>",
        "#",
        "# TRUST_API_KEY_HEADER is intentionally NOT set here: the chart default",
        "# (values.yaml) is 'Authorization', which is the platform default the hub",
        "# validates against. Override it only for a hub configured with a custom header.",
        "",
        f"trustName: {trust_name}",
    ]
    if slot_number:
        lines.append(f"trustNumber: {slot_number}")
    lines += [
        f"awsRegion: {aws_region}",
        f"flBackend: {fl_backend}",
        "",
    ]
    # The release this site runs (FLIP#1204): the kit's Hub-shared DOCKER_TAG pins every
    # FLIP-built image via global.image.tag. A dev kit (Hub-shared block commented out)
    # carries none, and the chart's per-service tags apply. `make upgrade-trust-k8s TAG=`
    # overrides this with --set at deploy time and site_upgrade.py rewrites the kit, so
    # the two stay in step.
    docker_tag = kit.get("DOCKER_TAG", "").strip()
    if docker_tag:
        lines += [
            "global:",
            "  image:",
            f"    tag: {docker_tag}",
            "",
        ]
    # The compose opt-outs (OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG) hold one image back from
    # the release pin; the chart's `image.pin` values are their Kubernetes twin.
    for kit_key, values_key in (("OMOP_DB_TAG", "omopDb"), ("ORTHANC_TAG", "orthanc"), ("XNAT_TAG", "xnat")):
        pin = kit.get(kit_key, "").strip()
        if pin:
            lines += [f"{values_key}:", "  image:", f"    pin: {pin}", ""]
    # The FL client follows the kit's DOCKER_FL_TAG (as the compose stack does) only when it
    # names an immutable image the registry serves — vX.Y.Z or sha-…; a dev kit's locally
    # built `dev` or the floating `stag` leaves it on the release pin (generate_values.py
    # fl_client_pin applies the same rule).
    fl_tag = kit.get("DOCKER_FL_TAG", "").strip()
    fl_client_pin = fl_tag if IMMUTABLE_IMAGE_TAG.match(fl_tag) else ""
    lines += [
        "trustApi:",
        "  env:",
        f"    CENTRAL_HUB_API_URL: {hub_url}",
        "",
    ]

    # OMOP core vocabulary (FLIP#842/843): the licensed bundle cannot be mirrored
    # publicly, so the chart runs its vocab-load hook only when a readable bucket
    # is named — the chart default is empty, and a release installed without this
    # has NO vocabulary (cohort queries joining omop.concept return nothing).
    # Each environment reads its OWN bucket (no cross-account read), which is
    # exactly what AICENTRE_BUCKET_NAME carries.
    if kit_bucket:
        lines += [
            "omopDb:",
            "  vocabLoad:",
            f"    s3Bucket: {kit_bucket}",
            "",
        ]

    # The chart does not fetch the participant kit — a trust holds no FLIP AWS
    # credentials, and the kit reaches the operator out-of-band (trust/README.md).
    # It is provisioned onto the node before the workload starts, so all the kit
    # override carries is where to find it. On kind, `make stage-kit` does:
    #   docker cp <kit>/. <cluster>-control-plane:/opt/flip/fl-kit/
    fl_section = [
        "flClient:",
        f"  kitHostPath: {kit_host_path}",
    ]
    if fl_client_pin:
        fl_section += ["  image:", f"    pin: {fl_client_pin}"]
    # The kit's FL_SITE_PRIVACY_* (FLIP#851) reach the chart's flClient.nvflare.sitePrivacy, as
    # they reach the Compose client, so check-governance's view of the filter is what runs.
    site_privacy = [
        (value_key, kit.get(kit_key, "").strip())
        for value_key, kit_key in (
            ("policy", "FL_SITE_PRIVACY_POLICY"),
            ("percentile", "FL_SITE_PRIVACY_PERCENTILE"),
            ("gamma", "FL_SITE_PRIVACY_GAMMA"),
        )
    ]
    if any(value for _, value in site_privacy):
        fl_section += ["  nvflare:", "    sitePrivacy:"]
        fl_section += [f'      {value_key}: "{value}"' for value_key, value in site_privacy if value]
    lines += fl_section

    # The trust's own disclosure floor. The chart passed none before, so every chart-deployed
    # trust ran at the default 10 while check-governance validated against the kit's value.
    threshold = kit.get("COHORT_QUERY_THRESHOLD", "").strip()
    if threshold:
        lines += ["", "dataAccessApi:", f"  cohortQueryThreshold: {threshold}"]

    # FL-server egress (FLIP#593 pt.3): the default-deny egress NetworkPolicy
    # drops the fl-client's outbound gRPC to the FL server unless FL_SERVER_PORT
    # is allow-listed. The FL server sits behind an internet-facing NLB with
    # AWS-managed IPs that rotate on recreation (and DNS returns a reordered
    # subset), so pinning resolved /32s would relocate the very drift it tries to
    # fix. Instead emit a PORT-ONLY egress rule: add FL_SERVER_PORT to
    # allowedEgressPorts (the template supports a no-CIDR port entry). This is
    # immune to NLB IP churn — K8s NetworkPolicy can't match DNS names anyway —
    # and re-running sync-kit regenerates it deterministically rather than
    # silently dropping a hand-added block and severing FL training.
    #
    # Helm replaces (not merges) list values across `-f` files, so the override's
    # allowedEgressPorts must restate the chart defaults (DNS/HTTP/HTTPS, kept in
    # sync with values.yaml) alongside the FL-server port — otherwise the
    # override would wipe the default egress allowlist.
    fl_port = kit.get("FL_SERVER_PORT", "").strip()
    if fl_port:
        lines += [
            "",
            "networkPolicies:",
            "  # Restates the chart-default egress ports (values.yaml) plus the",
            "  # fl-client → fl-server gRPC port; Helm replaces this list wholesale.",
            "  allowedEgressPorts:",
            "    - port: 53",
            "      protocol: UDP",
            "    - port: 53",
            "      protocol: TCP",
            "    - port: 80",
            "      protocol: TCP",
            "    - port: 443",
            "      protocol: TCP",
            f"    - port: {fl_port}  # fl-client → fl-server gRPC (FL_SERVER_PORT)",
            "      protocol: TCP",
        ]

    # The trust's governance document (FLIP#1259). The kit names a *path* — the same
    # ACCESS_POLICY_FILE the Compose stack mounts — but a path on the deploy host means
    # nothing inside a pod, so what travels is the document itself, which the chart renders
    # into a read-only ConfigMap that data-access-api mounts and the NVFLARE fl-client's
    # governance-extract init container reads (the client itself sees its section only). A
    # relative path resolves against the trust tree, as Compose's --project-directory trust
    # resolves its own mount. Unreadable is a hard error rather than an omission: the release
    # would otherwise install clean and quietly keep the platform defaults the operator
    # believes their rules replaced.
    policy_ref = kit.get("ACCESS_POLICY_FILE", "").strip()
    policy_path: Path | None = None
    document = ""
    if policy_ref:
        policy_path = Path(policy_ref)
        if not policy_path.is_absolute():
            policy_path = ((trust_dir or Path("trust")) / policy_path).resolve()
        try:
            document = policy_path.read_text()
        except OSError as e:
            raise GovernanceDocumentError(
                f"ACCESS_POLICY_FILE={policy_ref!r} (resolved to {policy_path}) could not be read: {e}"
            ) from None
    fl_privacy_checksum = _validate_governance(kit, policy_path)
    if policy_path is not None:
        lines += ["", f"# The governance document itself, read from {policy_ref}:", "governance:"]
        if fl_privacy_checksum:
            # Rolls the fl-client only when its own section changes (templates/fl-client.yaml).
            lines.append(f"  flPrivacyChecksum: {fl_privacy_checksum}")
        lines.append("  document: |")
        lines += [f"    {line}" if line.strip() else "" for line in document.splitlines()]

    lines.append("")
    return "\n".join(lines)


def main(
    code: str,
    env: str,
    namespace: str,
    secret_name: str,
    output_dir: Path,
    aws_region: str,
    apply_secret: bool,
    write_override: bool = True,
    release_name: str | None = None,
) -> None:
    release_name = release_name or derive_release_name(secret_name)
    repo_root = REPO_ROOT
    kit_path = repo_root / "trust" / f".env.{code}.{env}"

    print(f"🔧 Syncing K8s trust kit: {code}  (env={env})")
    print(f"   Kit file:  {kit_path}")
    print(f"   Namespace: {namespace}")
    print()

    if not kit_path.exists():
        print(f"❌ Kit file not found: {kit_path}")
        print("   Register the trust first:")
        print(f'     make new-trust TRUST_CODE={code} TRUST_NAME="..."')
        print(f"     make -C deploy/providers/AWS register-trusts KIT={code} PROD={env}")
        print(f"     make sync-trust-kit KIT={code} PROD={env}")
        sys.exit(1)

    kit = read_env_vars(kit_path)

    missing = [k for k in REQUIRED_KIT_KEYS if _is_placeholder(kit.get(k, ""))]
    if missing:
        print(f"❌ Kit {kit_path.name} is missing real values for: {', '.join(missing)}")
        print("   These come from registration + the Hub-shared block. Run:")
        print(f"     make -C deploy/providers/AWS register-trusts KIT={code} PROD={env}")
        print(f"     make sync-trust-kit KIT={code} PROD={env}")
        sys.exit(1)

    # ── 1. Patch the per-trust secrets into the cluster ──────────────────
    entries = build_secret_entries(kit)
    if apply_secret:
        ns_present = (
            subprocess.run(
                [*KUBECTL, "get", "ns", namespace],
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        if ns_present:
            print("🔐 Patching per-trust secrets into the Kubernetes Secret…")
            patch_k8s_secret(secret_name, namespace, entries, release_name)
            # Keep the chart's own view of these keys identical, or the next Helm 4
            # `upgrade` conflicts on them (server-side apply) — see align_values_secrets.
            realigned = align_values_secrets(output_dir / VALUES_SECRETS_NAME, entries)
            if realigned:
                print(f"  ✓ Realigned {VALUES_SECRETS_NAME} slots: {', '.join(sorted(realigned))}")
                print("    (so the next `helm upgrade` applies the same values and raises no SSA conflict)")
            print()
        else:
            print(f"ⓘ  Namespace '{namespace}' not found — skipping Secret patch.")
            print("   Deploy the chart first (it creates the namespace + Secret), then re-run.")
            print()
    else:
        print("ⓘ  --no-apply-secret: skipping the Kubernetes Secret patch.")
        print("   Per-trust secret fields would be patched.")
        print()

    # ── 2. Write the (secret-free) Helm values override ──────────────────
    rel_override = f"k8s-trust-{code}.yaml"
    if write_override:
        output_dir.mkdir(parents=True, exist_ok=True)
        override_path = output_dir / rel_override
        try:
            override = render_override(kit, code, aws_region, trust_dir=repo_root / "trust")
        except GovernanceDocumentError as e:
            print(f"❌ {e}")
            print("   Nothing was deployed. A relative ACCESS_POLICY_FILE resolves against trust/, as the")
            print("   Compose stack's --project-directory trust does. `make -C trust check-governance")
            print(f"   KIT={code} PROD={env}` validates the document and the kit's FL_SITE_PRIVACY_* in full.")
            sys.exit(1)
        override_path.write_text(override)
        print(f"  ✓ Wrote values override: {override_path}")
        print()
    else:
        print(f"ⓘ  --no-write-override: skipping override file write (existing {rel_override} preserved).")
        print()

    print("─" * 64)
    print("📦 Deploy the chart with the generated override:")
    print(f"     make -C trust/deploy/helm up OVERRIDES_FILE={rel_override}")
    print()
    print("   For FL training, also open the FL-server NLB to this node's public IP:")
    print(f"     make -C deploy/providers/AWS add-k8s-trust K8S_TRUST_IP=<node-public-ip> PROD={env}")
    print("─" * 64)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync a registered FLIP trust kit into the Kubernetes Helm deployment")
    parser.add_argument("--kit", required=True, help="Trust CODE (e.g. Trust_K8s, Trust_2)")
    parser.add_argument(
        "--env",
        default=None,
        help="Deployment env suffix (stag|production|development). Default: from $PROD.",
    )
    parser.add_argument("--namespace", default="flip-trust", help="Kubernetes namespace")
    parser.add_argument(
        "--secret-name",
        default="trust-release-flip-trust-secrets",
        help="Chart-created Secret name (default for release 'trust-release')",
    )
    parser.add_argument(
        "--release-name",
        default=None,
        help="Helm release name for Secret ownership stamping (default: derived from --secret-name)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory for the generated values override",
    )
    parser.add_argument("--aws-region", default="eu-west-2", help="AWS region for S3 access")
    parser.add_argument(
        "--kube-context",
        default="",
        help="kubectl context to act on (default: kubectl's current context)",
    )
    parser.add_argument(
        "--no-apply-secret",
        dest="apply_secret",
        action="store_false",
        help="Only write the override; do not patch the Kubernetes Secret",
    )
    parser.add_argument(
        "--no-write-override",
        dest="write_override",
        action="store_false",
        help="Only patch the Kubernetes Secret; do not overwrite the values override file",
    )
    args = parser.parse_args()
    if args.kube_context:
        KUBECTL += ["--context", args.kube_context]

    # Resolve env suffix the same way the rest of the tooling does (deploy/env_mode.mk).
    # The LZA estates get their own kit tokens, so a PROD the map does not know must fail
    # rather than fall through to a `development` kit that does not exist.
    if args.env is None:
        prod = os.environ.get("PROD", "")
        env_by_prod = {
            "": "development",
            "stag": "stag",
            "true": "production",
            "lza": "lza-prod",
            "lza-stag": "lza-stag",
        }
        if prod not in env_by_prod:
            sys.exit(f"❌ PROD={prod!r} is not a deployment mode — unset, stag, true, lza or lza-stag")
        env_suffix = env_by_prod[prod]
    else:
        env_suffix = args.env

    main(
        code=args.kit,
        env=env_suffix,
        namespace=args.namespace,
        secret_name=args.secret_name,
        output_dir=args.output_dir,
        aws_region=args.aws_region,
        apply_secret=args.apply_secret,
        write_override=args.write_override,
        release_name=args.release_name,
    )
