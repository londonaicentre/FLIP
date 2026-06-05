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
     FL backend, AWS region, the fl-client kit S3 bucket, and the FL kit slot
     (so the NVFLARE kit path resolves to the slot the hub assigned, not the
     cosmetic trust name).

The plaintext keys are never written to disk — they go straight from the kit
file into the Kubernetes Secret over kubectl's TLS channel. The generated
override file (``k8s-trust-*.yaml``) is gitignored and contains no secrets.

Usage:
  python3 sync_k8s_kit.py --kit Trust_K8s --env stag
  python3 sync_k8s_kit.py --kit Trust_2          # env auto-detected from $PROD
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

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


def _kubectl_ns(namespace: str) -> list[str]:
    """kubectl namespace args (empty for the default namespace)."""
    return ["-n", namespace] if namespace and namespace != "default" else []


def patch_k8s_secret(secret_name: str, namespace: str, entries: dict[str, str]) -> None:
    """Create or merge-patch a Kubernetes Secret with the given entries.

    Values are passed to kubectl directly — never written to disk.

    Args:
        secret_name: Secret resource name (chart default: ``<release>-flip-trust-secrets``).
        namespace: Kubernetes namespace.
        entries: Mapping of Secret key -> plaintext value.
    """
    ns = _kubectl_ns(namespace)
    exists = subprocess.run(
        ["kubectl", "get", "secret", secret_name, *ns],
        capture_output=True, text=True,
    ).returncode == 0

    if exists:
        data = {k: base64.b64encode(v.encode()).decode() for k, v in entries.items()}
        patch = json.dumps({"data": data})
        subprocess.run(
            ["kubectl", "patch", "secret", secret_name, *ns, "--type", "merge", "-p", patch],
            check=True,
        )
        verb = "Patched"
    else:
        args = ["kubectl", "create", "secret", "generic", secret_name, *ns]
        for k, v in entries.items():
            args += ["--from-literal", f"{k}={v}"]
        subprocess.run(args, check=True)
        verb = "Created"

    print(f"  ✓ {verb} Kubernetes Secret entries.")
    print("    Plaintext values went straight to Kubernetes — not persisted to disk.")


def build_secret_entries(kit: dict[str, str]) -> dict[str, str]:
    """Pick the per-trust secret keys the kit carries with real values."""
    entries: dict[str, str] = {}
    for kit_key, secret_key in KIT_TO_SECRET_KEY.items():
        value = kit.get(kit_key, "")
        if not _is_placeholder(value):
            entries[secret_key] = value
    return entries


def render_override(kit: dict[str, str], code: str, aws_region: str) -> str:
    """Render the Helm values override (no secrets) from kit settings."""
    trust_name = kit.get("TRUST_NAME", code)
    slot = kit.get("FL_KIT_SLOT", "").strip()
    slot_number = kit.get("FL_KIT_SLOT_NUMBER", "").strip()
    hub_url = kit.get("CENTRAL_HUB_API_URL", "")
    fl_backend = kit.get("FL_BACKEND", "nvflare").strip() or "nvflare"
    kit_bucket = kit.get("AICENTRE_BUCKET_NAME", "").strip()
    flare_kit_date = kit.get("FLARE_KIT_DATE", "").strip()
    flower_kit_date = kit.get("FLOWER_KIT_DATE", "").strip()

    lines = [
        "# ── Generated by sync_k8s_kit.py — DO NOT edit by hand ────────────────",
        f"# Per-deployment override for trust '{code}' (kit: trust/.env.{code}.<env>).",
        "# Contains no secrets — the per-trust keys live in the Kubernetes Secret,",
        "# patched into the cluster by the same sync-kit run. Regenerate by re-running",
        "#   make -C deploy/providers/kubernetes sync-kit KIT=" + code + " PROD=<env>",
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
        "trustApi:",
        "  env:",
        f"    CENTRAL_HUB_API_URL: {hub_url}",
        "",
    ]

    # fl-client kit S3 bucket + slot-aware path. The NVFLARE kit is published
    # under .../net-1/services/<slot>, where <slot> is the FL kit slot the hub
    # assigned (e.g. Trust_2) — NOT the cosmetic trustName. Pin the path to the
    # slot so FL training resolves the right kit. (Flower's path carries no slot.)
    fl_section = ["flClient:"]
    if fl_backend == "flower":
        fl_section += [
            "  flower:",
            "    kitFromS3:",
            f"      bucket: {kit_bucket}" if kit_bucket else '      bucket: ""  # set AICENTRE_BUCKET_NAME in the kit',
        ]
        if flower_kit_date:
            fl_section.append(f"      kitDate: \"{flower_kit_date}\"")
    else:
        fl_section += [
            "  nvflare:",
            "    kitFromS3:",
            f"      bucket: {kit_bucket}" if kit_bucket else '      bucket: ""  # set AICENTRE_BUCKET_NAME in the kit',
        ]
        if flare_kit_date:
            fl_section.append(f"      kitDate: \"{flare_kit_date}\"")
        if slot:
            fl_section.append(
                "      pathTemplate: "
                '"fl-flare-participant-kits/'
                "{{ .Values.flClient.nvflare.kitFromS3.kitDate }}/net-1/services/"
                f'{slot}"'
            )
    lines += fl_section
    lines.append("")
    return "\n".join(lines)


def main(code: str, env: str, namespace: str, secret_name: str,
         output_dir: Path, aws_region: str, apply_secret: bool,
         write_override: bool = True) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    kit_path = repo_root / "trust" / f".env.{code}.{env}"

    print(f"🔧 Syncing K8s trust kit: {code}  (env={env})")
    print(f"   Kit file:  {kit_path}")
    print(f"   Namespace: {namespace}")
    print()

    if not kit_path.exists():
        print(f"❌ Kit file not found: {kit_path}")
        print("   Register the trust first:")
        print(f"     make new-trust TRUST_CODE={code} TRUST_NAME=\"...\"")
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
        ns_present = subprocess.run(
            ["kubectl", "get", "ns", namespace],
            capture_output=True, text=True,
        ).returncode == 0
        if ns_present:
            print("🔐 Patching per-trust secrets into the Kubernetes Secret…")
            patch_k8s_secret(secret_name, namespace, entries)
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
        override_path.write_text(render_override(kit, code, aws_region))
        print(f"  ✓ Wrote values override: {override_path}")
        print()
    else:
        print(f"ⓘ  --no-write-override: skipping override file write (existing {rel_override} preserved).")
        print()

    print("─" * 64)
    print("📦 Deploy the chart with the generated override:")
    print(f"     make -C deploy/providers/kubernetes up OVERRIDES_FILE={rel_override}")
    print()
    print("   For FL training, also open the FL-server NLB to this node's public IP:")
    print(f"     make -C deploy/providers/AWS add-k8s-trust K8S_TRUST_IP=<node-public-ip> PROD={env}")
    print("─" * 64)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync a registered FLIP trust kit into the Kubernetes Helm deployment"
    )
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
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory for the generated values override",
    )
    parser.add_argument("--aws-region", default="eu-west-2", help="AWS region for S3 access")
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

    # Resolve env suffix the same way the rest of the tooling does.
    if args.env is None:
        prod = os.environ.get("PROD", "")
        env_suffix = "production" if prod == "true" else "stag" if prod == "stag" else "development"
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
    )
