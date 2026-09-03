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
"""
generate_values.py - Env-to-Helm-values mapping script.

Reads a .env file and generates:
  - values-override.yaml  (non-sensitive Helm values)
  - values-secrets.yaml   (sensitive values, never printed to stdout)

Usage (from the repo root):
  python3 deploy/providers/kubernetes/scripts/generate_values.py \\
      --env-file trust/.env.<CODE>.<env> \\
      --output-dir deploy/providers/kubernetes

No third-party dependencies (stdlib only).
"""

import argparse
import os
import sys

# Map of env-var name -> (YAML path as dotted key, is_sensitive)
ENV_VAR_MAP = {
    "CENTRAL_HUB_API_URL": ("trustApi.env.CENTRAL_HUB_API_URL", False),
    "TRUST_NAME": ("trustName", False),
    "TRUST_NUMBER": ("trustNumber", False),
    "AWS_REGION": ("awsRegion", False),
    "LOG_LEVEL": ("logLevel", False),
    "ENV": ("environment", False),
    "FL_BACKEND": ("flBackend", False),
    "UPLOADED_FEDERATED_DATA_BUCKET": ("uploadedFederatedDataBucket", False),
    "S3_BUCKET": ("flClient.nvflare.kitFromS3.bucket", False),
    "S3_BUCKET_FLOWER": ("flClient.flower.kitFromS3.bucket", False),
    "S3_KIT_DATE": ("flClient.nvflare.kitFromS3.kitDate", False),
    "S3_KIT_DATE_FLOWER": ("flClient.flower.kitFromS3.kitDate", False),
    "AICENTRE_BUCKET_NAME": ("omopDb.initJob.s3Bucket", False),
    "OMOP_DATA_VERSION": ("omopDb.initJob.dataVersion", False),
}

# Env var name -> Secret key name. Both sides are identifiers; no value appears
# here, which is why the entries detect-secrets' keyword rule matches carry an
# inline pragma. Nothing in this file is ever a credential.
SECRET_VAR_MAP = {
    "AES_KEY_BASE64": "aes-key-base64",
    "TRUST_API_KEY": "trust-api-key",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY_HEADER": "trust-internal-service-key-header",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY": "trust-internal-service-key",  # pragma: allowlist secret
    "OMOP_POSTGRES_PASSWORD": "omop-postgres-password",  # pragma: allowlist secret
    "DATA_ACCESS_POSTGRES_PASSWORD": "data-access-postgres-password",  # pragma: allowlist secret
    "ORTHANC_REGISTERED_USERS": "orthanc-registered-users",
    "XNAT_ADMIN_PASSWORD": "xnat-admin-password",  # pragma: allowlist secret
    "XNAT_SERVICE_USER": "xnat-service-user",
    "XNAT_SERVICE_PASSWORD": "xnat-service-password",  # pragma: allowlist secret
    "XNAT_DATASOURCE_PASSWORD": "xnat-datasource-password",  # pragma: allowlist secret
    "XNAT_DATASOURCE_ADMIN_PASSWORD": "xnat-datasource-admin-password",  # pragma: allowlist secret
    "GRAFANA_ADMIN_PASSWORD": "grafana-admin-password",  # pragma: allowlist secret
    "AWS_ACCESS_KEY_ID": "s3-access-key-id",
    "AWS_SECRET_ACCESS_KEY": "s3-secret-access-key",  # pragma: allowlist secret
    "AWS_SESSION_TOKEN": "aws-session-token",
}


def deep_set(d, key_path, value):
    """Set a nested dict value from a dotted key path like 'a.b.c'."""
    parts = key_path.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def parse_env_file(path):
    """Parse a simple KEY=VALUE env file, skipping comments and blanks."""
    env = {}
    if not os.path.isfile(path):
        print("WARNING: Env file not found: {}".format(path), file=sys.stderr)
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip("\"'")
    return env


def build_values(env):
    """Build (values_overrides, secrets_dict) from parsed env vars."""
    overrides = {}
    for env_var, (yaml_path, _) in ENV_VAR_MAP.items():
        if env_var not in env or not env[env_var]:
            continue
        val = env[env_var]
        # Normalise flBackend: accept any casing
        if env_var == "FL_BACKEND":
            val = val.lower().replace(" ", "")
        # Also set Flower kit bucket when S3_BUCKET is set without explicit FLOWER variant
        if env_var == "S3_BUCKET" and "S3_BUCKET_FLOWER" not in env:
            deep_set(overrides, "flClient.flower.kitFromS3.bucket", val)
        deep_set(overrides, yaml_path, val)

    secrets = {}
    for env_var, secret_key in SECRET_VAR_MAP.items():
        if env_var not in env or not env[env_var]:
            continue
        secrets[secret_key] = env[env_var]
    return overrides, secrets


def yaml_quote(value):
    """Return a YAML-safe quoted string for a scalar value."""
    # Python bool is a subclass of int; check type first.
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    needs_quoting = (
        not s
        or " " in s
        or "#" in s
        or ":" in s
        or s[0] in "'\""
        or s in ("true", "false", "yes", "no", "on", "off", "null", "~")
    )
    if needs_quoting:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return '"{}"'.format(escaped)
    return s


def yaml_dump(data, indent=0):
    """Simple YAML serializer for dict/list/scalar structures (stdlib only)."""
    lines = []
    prefix = "  " * indent
    if isinstance(data, dict):
        for key, val in data.items():
            k = str(key)
            if " " in k or ":" in k or "#" in k:
                k = '"{}"'.format(k)
            if isinstance(val, dict):
                lines.append("{}{}:".format(prefix, k))
                lines.append(yaml_dump(val, indent + 1))
            elif isinstance(val, list):
                lines.append("{}{}:".format(prefix, k))
                for item in val:
                    if isinstance(item, dict):
                        lines.append("{}  - ".format(prefix))
                        lines.append(yaml_dump(item, indent + 2))
                    else:
                        lines.append("{}  - {}".format(prefix, yaml_quote(item)))
            else:
                lines.append("{}{}: {}".format(prefix, k, yaml_quote(val)))
    elif isinstance(data, list):
        for item in data:
            lines.append("{}- {}".format(prefix, yaml_quote(item)))
    return "\n".join(lines)


def write_yaml(data, path, permissions=0o644):
    """Write a YAML file using the stdlib serializer with given file permissions."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, permissions)
    with os.fdopen(fd, "w") as f:
        f.write(yaml_dump(data))
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate Helm values override file(s) from a .env file."
    )
    parser.add_argument(
        "--env-file",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            ".env.development",
        ),
        help="Path to the .env file (default: ../../.env.development relative to script dir)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for generated YAML files (default: current dir)",
    )
    parser.add_argument(
        "--secrets-file",
        default=None,
        help="Output path for secrets values file (default: <output-dir>/values-secrets.yaml)",
    )
    args = parser.parse_args()

    env = parse_env_file(args.env_file)
    if not env:
        print("ERROR: No env vars found. Exiting.", file=sys.stderr)
        sys.exit(1)

    overrides, secrets = build_values(env)

    if not overrides and not secrets:
        print("WARNING: No recognised env vars found in the file.", file=sys.stderr)
        sys.exit(0)

    # Write non-sensitive overrides (safe to echo path)
    overrides_path = os.path.join(args.output_dir, "values-override.yaml")
    write_yaml(overrides, overrides_path)
    print("Wrote non-sensitive overrides to: {}".format(overrides_path))

    # Write secrets file (never echo contents)
    secrets_path = args.secrets_file or os.path.join(
        args.output_dir, "values-secrets.yaml"
    )
    if secrets:
        write_yaml({"secrets": {"create": True, "data": secrets}}, secrets_path, permissions=0o600)
        print("Wrote secrets values to: {} (permissions: 0o600)".format(secrets_path))
        print("WARNING: values-secrets.yaml contains sensitive data.", file=sys.stderr)
        print(
            "   Do not commit it. Inside this repository .gitignore already matches "
            "**/values-secrets.yaml; elsewhere, add the same rule.",
            file=sys.stderr,
        )

        # Say which slots the env file could not fill. templates/secrets.yaml omits
        # an empty key, so the pod that mounts it dies with CreateContainerConfigError
        # — better to hear it here than from a crash-looping container.
        unfilled = sorted(key for key in SECRET_VAR_MAP.values() if key not in secrets)
        if unfilled:
            print(
                "WARNING: no value in the env file for: {}".format(", ".join(unfilled)),
                file=sys.stderr,
            )
            print(
                "   Fill by hand whichever your deployment needs — an omitted key fails the pod "
                "at container creation, not at render.",
                file=sys.stderr,
            )
    else:
        print("No secrets env vars found; no secrets file written.")

    # Print helm invocation hint
    print()
    print("---")
    print("To use with helm:")
    print("  helm upgrade --install trust-release . \\")
    print("    -f {} \\".format(overrides_path))
    if secrets:
        print("    -f {}".format(secrets_path))
    else:
        print("    # (no secrets file)")
    print()


if __name__ == "__main__":
    main()
