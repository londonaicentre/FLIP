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

This script generates API keys for a new trust, updates the local env file,
creates a Helm values override file for Kubernetes deployment, and outputs
the manual steps required on the AWS side (Secrets Manager, Terraform, flip-api restart).

Usage:
  python3 register_k8s_trust.py --trust-name Trust_2 \\
      --hub-url https://stag.flip.aicentre.co.uk/api \\
      --env-file ../../../.env.development

What it does:
  1. Reads existing trust names and keys from the env file
  2. Generates a TRUST_API_KEY and TRUST_INTERNAL_SERVICE_KEY for the new trust
  3. Appends the new trust to TRUST_NAMES, TRUST_API_KEYS, TRUST_API_KEY_HASHES,
     and TRUST_INTERNAL_SERVICE_KEYS in the env file
  4. Creates a Helm values override file (k8s-trust-<name>.yaml) with:
       - trustName: <name>
       - hub URL, API key, internal service key, AES key
  5. Prints the manual AWS steps required (Secrets Manager + Terraform)
"""

import argparse
import hashlib
import json
import os
import secrets
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
    trust_api_key: str,
    hub_url: str,
    aes_key: str,
    internal_service_key: str,
    internal_service_key_header: str,
    output_dir: Path,
) -> Path:
    """Create a Helm values override file for the K8s trust.

    Args:
        trust_name: Trust identifier (e.g. 'Trust_2')
        trust_api_key: Per-trust API key for hub authentication
        hub_url: Central Hub API URL (CloudFront)
        aes_key: AES-256 base64 key shared with the hub
        internal_service_key: Trust-internal service key
        internal_service_key_header: Header name for trust-internal auth
        output_dir: Directory to write the override file

    Returns:
        Path to the created override file
    """
    override_path = output_dir / f"k8s-trust-{trust_name}.yaml"

    content = f"""# ── Generated by register_k8s_trust.py ────────────────────────────────
# This file contains the per-trust configuration for {trust_name}
# deployed via the flip-trust Helm chart.
#
# Deploy with:
#   helm upgrade --install flip-trust . \\
#       -f k8s-trust-{trust_name}.yaml \\
#       -f ci/test-values.yaml \\
#       --namespace flip-trust \\
#       --set namespace.create=false

trustName: {trust_name}
trustNumber: "02"

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

# Deploy the generated keys to the Kubernetes Secret:
#
#   kubectl -n flip-trust create secret generic flip-trust-secrets \\
#     --from-literal=trust-api-key={trust_api_key} \\
#     --from-literal=aes-key-base64={aes_key} \\
#     --from-literal=trust-internal-service-key-header={internal_service_key_header} \\
#     --from-literal=trust-internal-service-key={internal_service_key} \\
#     [other existing keys...]
"""

    override_path.write_text(content)
    print(f"\n  ✓ Helm values override written to {override_path}")
    return override_path


def main(
    trust_name: str,
    hub_url: str,
    env_file: Path,
    output_dir: Path,
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
    print()

    trust_names = read_json_env(env_file, "TRUST_NAMES")
    if not isinstance(trust_names, list):
        trust_names = []

    trust_api_keys = read_json_env(env_file, "TRUST_API_KEYS")
    if not isinstance(trust_api_keys, dict):
        trust_api_keys = {}

    trust_api_key_hashes = read_json_env(env_file, "TRUST_API_KEY_HASHES")
    if not isinstance(trust_api_key_hashes, dict):
        trust_api_key_hashes = {}

    trust_internal_keys = read_json_env(env_file, "TRUST_INTERNAL_SERVICE_KEYS")
    if not isinstance(trust_internal_keys, dict):
        trust_internal_keys = {}

    # Check if trust already exists
    if trust_name in trust_api_keys and not force:
        print(f"⚠️  {trust_name} already has a TRUST_API_KEY. Use --force to regenerate.")
        print(f"   To use existing keys, set them in your K8s Secret directly.")
        sys.exit(0)

    # ── Add trust name to TRUST_NAMES ────────────────────────────────
    if trust_name not in trust_names:
        trust_names.append(trust_name)
        write_env_line_replacement(env_file, "TRUST_NAMES", json.dumps(trust_names))
    else:
        print(f"  ⓘ {trust_name} already in TRUST_NAMES")

    # ── Generate API key (trust → hub auth) ─────────────────────────
    trust_api_key, trust_api_key_hash = generate_key()
    trust_api_keys[trust_name] = trust_api_key
    trust_api_key_hashes[trust_name] = trust_api_key_hash

    write_env_line_replacement(env_file, "TRUST_API_KEYS", json.dumps(trust_api_keys))
    write_env_line_replacement(env_file, "TRUST_API_KEY_HASHES", json.dumps(trust_api_key_hashes))

    # ── Generate trust-internal service key ──────────────────────────
    internal_key, _ = generate_key()
    trust_internal_keys[trust_name] = internal_key

    write_env_line_replacement(env_file, "TRUST_INTERNAL_SERVICE_KEYS", json.dumps(trust_internal_keys))

    # ── Read shared secrets ──────────────────────────────────────────
    env_vars = read_env_vars(env_file)
    aes_key = env_vars.get("AES_KEY_BASE64", "")
    internal_service_key_header = env_vars.get(
        "TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key"
    )

    # ── Create Helm values override ──────────────────────────────────
    create_helm_values(
        trust_name=trust_name,
        trust_api_key=trust_api_key,
        hub_url=hub_url,
        aes_key=aes_key,
        internal_service_key=internal_key,
        internal_service_key_header=internal_service_key_header,
        output_dir=output_dir,
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
    print(f"Create the Kubernetes Secret with the generated keys:")
    print()
    print(f"  Keys have been stored in {env_file}. Use them to create the Secret:")
    masked_api = trust_api_key[:8] + "..." if len(trust_api_key) > 8 else trust_api_key
    masked_internal = internal_key[:8] + "..." if len(internal_key) > 8 else internal_key
    secret_kubectl = (
        f"kubectl -n flip-trust create secret generic flip-trust-secrets \\\n"
        f"  --from-literal=trust-api-key=$(grep TRUST_API_KEYS {env_file} | python3 -c \"import json,sys,os; print(json.loads(os.environ.get('V','').replace(\\\"'\\\",'\\\"'))['{trust_name}'])\") \\\n"
        f"  --from-literal=aes-key-base64=$(grep AES_KEY_BASE64 {env_file} | cut -d= -f2) \\\n"
        f"  --from-literal=trust-internal-service-key-header={internal_service_key_header} \\\n"
        f"  --from-literal=trust-internal-service-key=(TRUST_INTERNAL_SERVICE_KEYS {trust_name} value from {env_file}) \\\n"
        f"  [plus your existing data-access-postgres-password, orthanc-*, s3-*, etc.]"
    )
    print(f"  {secret_kubectl}")
    print()
    print(f"Then deploy the chart:")
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
        default=Path(__file__).resolve().parents[3] / ".env.development",
        help="Path to the .env file (default: <repo_root>/.env.development)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Output directory for Helm override file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of keys even if trust already exists",
    )
    args = parser.parse_args()

    main(
        trust_name=args.trust_name,
        hub_url=args.hub_url,
        env_file=args.env_file,
        output_dir=args.output_dir,
        force=args.force,
    )
