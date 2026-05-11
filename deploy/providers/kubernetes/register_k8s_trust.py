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

"""Register a Kubernetes-deployed trust with the FLIP Central Hub.

This script generates trust credentials, updates the local env file with the
trust name and API-key hash, writes keys to a Kubernetes Secret when possible,
and creates a Helm values override file for Kubernetes deployment.

Usage:
  python3 register_k8s_trust.py --trust-name Trust_2 \\
      --hub-url https://stag.flip.aicentre.co.uk/api \\
      --env-file ../../../.env.development

What it does:
  1. Reads TRUST_NAMES and TRUST_API_KEY_HASHES from the env file
  2. Generates a TRUST_API_KEY and TRUST_INTERNAL_SERVICE_KEY for the new trust
  3. Updates TRUST_NAMES and TRUST_API_KEY_HASHES in the env file
     (plaintext keys are not written to the env file)
  4. Creates/updates Kubernetes Secret keys when kubectl access is available
  5. Creates a Helm values override file (k8s-trust-<name>.yaml) with trust settings
  6. Prints the manual AWS/Terraform follow-up steps
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path


def generate_key() -> tuple[str, str]:
    """Generate a cryptographically secure API key and its SHA-256 hash.

    Returns:
        (plaintext_key, hex_sha256_hash)
    """
    key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return key, key_hash


def _kubectl_namespace(namespace: str) -> list[str]:
    """Build kubectl args with namespace when not in default ns."""
    return ["-n", namespace] if namespace and namespace != "default" else []


def create_k8s_secret(
    secret_name: str,
    namespace: str,
    entries: dict[str, str],
    label: str = "",
) -> None:
    """Create or update a Kubernetes Secret with the given key-value entries.

    Keys are stored in the Secret only — never written to disk outside kubectl's TLS channel.

    Args:
        secret_name: K8s Secret resource name
        namespace: K8s namespace
        entries: Mapping of secret key → value
        label: Optional app instance label (e.g. 'flip-trust')
    """
    ns_args = _kubectl_namespace(namespace)

    # Check if secret exists
    result = subprocess.run(
        ["kubectl", "get", "secret", secret_name] + ns_args,
        capture_output=True, text=True,
    )
    action = "patch" if result.returncode == 0 else "create"

    if action == "create":
        args = ["kubectl", "create", "secret", "generic", secret_name] + ns_args
        for k, v in entries.items():
            args += ["--from-literal", f"{k}={v}"]
        if label:
            args += ["--dry-run=client", "-o", "yaml"]
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            subprocess.run(
                ["kubectl", "apply", "-f", "-"] + ns_args,
                input=result.stdout, text=True, check=True,
            )
        else:
            subprocess.run(args, check=True)
    else:
        # build a JSON patch from entries
        import base64
        data = {}
        for k, v in entries.items():
            data[k] = base64.b64encode(v.encode()).decode()
        patch = json.dumps({"data": data})
        subprocess.run(
            ["kubectl", "patch", "secret", secret_name] + ns_args +
            ["--type", "merge", "-p", patch],
            check=True,
        )

    entry_keys = ", ".join(
        f"{k}={v[:6]}..." if len(v) > 6 else f"{k}=***"
        for k, v in entries.items()
    )
    print(f"  ✓ {'Created' if action == 'create' else 'Updated'} Secret/{secret_name} ({entry_keys})")
    print(f"    Keys are stored in Kubernetes only — not persisted to disk.")
    print()


def read_env_vars(env_path: Path) -> dict[str, str]:
    """Read key=value pairs from an env file.

    Args:
        env_path: Path to the .env file

    Returns:
        Dict of env var name → value
    """
    if not env_path.exists():
        return {}

    pairs = {}
    with open(env_path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                # Strip surrounding quotes
                value = value.strip().strip('"').strip("'")
                pairs[key.strip()] = value
    return pairs


def read_json_env(env_path: Path, key: str) -> dict | list:
    """Read a JSON-encoded environment variable from an env file.

    Args:
        env_path: Path to the .env file
        key: Environment variable name (e.g. 'TRUST_NAMES')

    Returns:
        Parsed JSON value, or empty dict/list
    """
    env_vars = read_env_vars(env_path)
    raw = env_vars.get(key, "[]" if "NAMES" in key else "{}")
    try:
        return json.loads(raw.replace("'", '"'))
    except (json.JSONDecodeError, TypeError):
        return [] if "NAMES" in key else {}


def write_env_line_replacement(env_path: Path, key: str, value: str) -> None:
    """Replace or append a single env var line in the env file.

    Args:
        env_path: Path to the .env file
        key: Environment variable name
        value: New value
    """
    if not env_path.exists():
        print(f"❌ Env file {env_path} does not exist — cannot update {key}")
        sys.exit(1)

    new_line = f"{key}={value}\n"
    lines = env_path.read_text().splitlines(keepends=True)

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} "):
            lines[i] = new_line
            found = True
            break

    if found:
        env_path.write_text("".join(lines))
        print(f"  ✓ Updated {key} in {env_path}")
    else:
        # Append at end
        with open(env_path, "a") as f:
            if not lines[-1].endswith("\n"):
                f.write("\n")
            f.write(f"\n{new_line}")
        print(f"  ✓ Appended {key} to {env_path}")


def create_helm_values(
    trust_name: str,
    hub_url: str,
    output_dir: Path,
    trust_number: int | None,
) -> Path:
    """Create a Helm values override file for the K8s trust.

    Does NOT embed keys in the file — keys are stored in the K8s Secret only.

    Args:
        trust_name: Trust identifier (e.g. 'Trust_2')
        hub_url: Central Hub API URL (CloudFront)
        output_dir: Directory to write the override file
        trust_number: Optional numeric trust identifier for TRUST_NUMBER

    Returns:
        Path to the created override file
    """
    override_path = output_dir / f"k8s-trust-{trust_name}.yaml"
    trust_number_line = (
        f'trustNumber: "{trust_number:02d}"\n' if trust_number is not None else "# trustNumber: <set-me>\n"
    )

    content = f"""# ── Generated by register_k8s_trust.py ────────────────────────────────
# This file contains the per-trust configuration for {trust_name}
# deployed via the flip-trust Helm chart. Keys are stored in the
# Kubernetes Secret 'flip-trust-secrets', created by this script.
#
# Deploy with:
#   helm upgrade --install flip-trust . \\
#       -f k8s-trust-{trust_name}.yaml \\
#       -f ci/test-values.yaml \\
#       --namespace flip-trust \\
#       --set namespace.create=false

trustName: {trust_name}
{trust_number_line}
trustApi:
  env:
    CENTRAL_HUB_API_URL: {hub_url}
    POLL_INTERVAL_SECONDS: "5"

flClient:
  enabled: true
  gpu:
    enabled: true
    count: 1
  nvflare:
    kitFromS3:
      enabled: true
      bucket: fl-kits

secrets:
  create: false
  existingName: flip-trust-secrets
"""

    override_path.write_text(content)
    print(f"\n  ✓ Helm values override written to {override_path}")
    return override_path


def main(
    trust_name: str,
    hub_url: str,
    env_file: Path,
    output_dir: Path,
    trust_number: int | None,
    force: bool,
) -> None:
    """Register a new K8s trust.

    Args:
        trust_name: Unique trust name (e.g. 'Trust_2')
        hub_url: Central Hub API URL for the running system
        env_file: Path to the .env file
        output_dir: Directory for the Helm override file
        force: Overwrite existing keys for this trust
    """
    # ── Load existing configuration ──────────────────────────────────
    print(f"🔑 Registering K8s trust: {trust_name}")
    print(f"   Hub URL: {hub_url}")
    print(f"   Env file: {env_file}")
    print(f"   Output dir: {output_dir}")
    print(f"   Trust number: {trust_number if trust_number is not None else 'not set (uses chart default)'}")
    print()

    trust_names = read_json_env(env_file, "TRUST_NAMES")
    if not isinstance(trust_names, list):
        trust_names = []

    trust_api_key_hashes = read_json_env(env_file, "TRUST_API_KEY_HASHES")
    if not isinstance(trust_api_key_hashes, dict):
        trust_api_key_hashes = {}

    # Check if trust already exists
    if trust_name in trust_api_key_hashes and not force:
        print(f"⚠️  {trust_name} already has a key hash registered. Use --force to regenerate.")
        print(f"   To use existing keys, update your K8s Secret directly.")
        sys.exit(0)

    # ── Add trust name to TRUST_NAMES ────────────────────────────────
    if trust_name not in trust_names:
        trust_names.append(trust_name)
        write_env_line_replacement(env_file, "TRUST_NAMES", json.dumps(trust_names))
    else:
        print(f"  ⓘ {trust_name} already in TRUST_NAMES")

    # ── Generate API key (trust → hub auth) ─────────────────────────
    trust_api_key, trust_api_key_hash = generate_key()
    trust_api_key_hashes[trust_name] = trust_api_key_hash
    write_env_line_replacement(env_file, "TRUST_API_KEY_HASHES", json.dumps(trust_api_key_hashes))

    # ── Generate trust-internal service key ──────────────────────────
    internal_key, _ = generate_key()

    # ── Read shared secrets ──────────────────────────────────────────
    env_vars = read_env_vars(env_file)
    aes_key = env_vars.get("AES_KEY_BASE64", "")
    internal_service_key_header = env_vars.get(
        "TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key"
    )

    # ── Store keys in Kubernetes Secret (never on disk) ──────────────
    ns_arg = _kubectl_namespace("flip-trust")
    kubectl_ok = subprocess.run(
        ["kubectl", "get", "ns", "flip-trust"] + ns_arg,
        capture_output=True, text=True,
    ).returncode == 0

    if kubectl_ok:
        print("🔐 Creating Kubernetes Secret with generated keys...")
        create_k8s_secret(
            secret_name="flip-trust-secrets",
            namespace="flip-trust",
            entries={
                "trust-api-key": trust_api_key,
                "aes-key-base64": aes_key,
                "trust-internal-service-key-header": internal_service_key_header,
                "trust-internal-service-key": internal_key,
            },
            label="flip-trust",
        )
    else:
        print("ⓘ kubectl not available or 'flip-trust' namespace not found.")
        print("  Keys were NOT written to disk. To create the Secret manually:")
        print(f"  Refer to {env_file} TRUST_API_KEY_HASHES for the hash.")
        print(f"  Then create the Secret with your deployment tool.")
        print()

    # ── Create Helm values override ──────────────────────────────────
    create_helm_values(
        trust_name=trust_name,
        hub_url=hub_url,
        output_dir=output_dir,
        trust_number=trust_number,
    )

    # ── Print AWS-side Terraform steps ────────────────────────────────
    print()
    print("─" * 64)
    print("📋 TERRAFORM DEPLOYMENT STEPS (codified in deploy/providers/AWS/)")
    print("─" * 64)
    print()
    print(f"The trust name and API key hash are Terraform variables in the AWS module.")
    print(f"Apply the following to register {trust_name} with the running system:")
    print()
    print(f"  cd deploy/providers/AWS")
    print()
    print(f"  # This secret_string value replaces the one in main.tf (line ~158):")
    trust_names_json = json.dumps(trust_names)
    hashes_json = json.dumps(trust_api_key_hashes)
    print(f"  # trust_names:          {trust_names_json}")
    print(f"  # trust_api_key_hashes: {hashes_json}")
    print()
    print(f"  # Apply the updated secret:")
    print(f"  terraform apply -target=module.flip_api_secret")
    print()
    print(f"  # The flip-api seed reads TRUST_NAMES on startup; force a redeploy:")
    print(f"  # (ECS: aws ecs update-service ... --force-new-deployment)")
    print(f"  make deploy-centralhub")
    print()
    print(f"To verify:")
    print(f"  make status  # checks all endpoints after the redeploy")
    print()
    print("─" * 64)
    print("📦 K8S DEPLOYMENT")
    print("─" * 64)
    print()
    if kubectl_ok:
        print(f"  Kubernetes Secret 'flip-trust-secrets' has been updated with {trust_name} keys.")
        print()
    else:
        print(f"  Secrets are stored in Kubernetes only (not written to disk).")
        print(f"  Create the Secret manually with the keys generated above.")
        print()
    print(f"Deploy the chart:")
    helm_cmd = (
        f"helm upgrade --install flip-trust . \\\n"
        f"  -f k8s-trust-{trust_name}.yaml \\\n"
        f"  -f ci/test-values.yaml \\\n"
        f"  --namespace flip-trust \\\n"
        f"  --set namespace.create=false"
    )
    print(f"  {helm_cmd}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Register a Kubernetes-deployed trust with the FLIP Central Hub"
    )
    parser.add_argument(
        "--trust-name",
        default="Trust_K8s",
        help="Trust name (e.g. Trust_2, Trust_K8s). Default: Trust_K8s",
    )
    parser.add_argument(
        "--hub-url",
        required=True,
        help="Central Hub API URL (e.g. https://stag.flip.aicentre.co.uk/api)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to the .env file (default: auto-detected from PROD env var)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Output directory for Helm override file",
    )
    parser.add_argument(
        "--trust-number",
        type=int,
        default=None,
        help="Optional numeric trust identifier for trustNumber in generated values",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of keys even if trust already exists",
    )
    args = parser.parse_args()

    # Auto-detect env file from PROD env var if not explicitly set
    if args.env_file is None:
        prod = os.environ.get("PROD", "")
        repo_root = Path(__file__).resolve().parents[3]
        if prod == "true":
            env_file = repo_root / ".env.production"
        elif prod == "stag":
            env_file = repo_root / ".env.stag"
        else:
            env_file = repo_root / ".env.development"
    else:
        env_file = args.env_file

    main(
        trust_name=args.trust_name,
        hub_url=args.hub_url,
        env_file=env_file,
        output_dir=args.output_dir,
        trust_number=args.trust_number,
        force=args.force,
    )
