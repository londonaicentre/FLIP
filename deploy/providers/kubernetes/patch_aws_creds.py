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

"""Patch current AWS SSO credentials into the fl-client Kubernetes Secret.

Run after every `helm upgrade` (which resets s3-access-key-id to the placeholder
from values-secrets.yaml) or after an SSO token refresh. Usage:

  python3 patch_aws_creds.py [--namespace flip-trust] [--secret-name <name>]
"""

import argparse
import base64
import json
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="flip-trust")
    parser.add_argument("--secret-name", default="trust-release-flip-trust-secrets")
    args = parser.parse_args()

    profile = os.environ.get("AWS_PROFILE", "stag")
    result = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "env"],
        capture_output=True, text=True,
    )
    creds: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if line.startswith("export "):
            key, _, val = line[7:].partition("=")
            creds[key] = val

    key_id = creds.get("AWS_ACCESS_KEY_ID", "")
    secret = creds.get("AWS_SECRET_ACCESS_KEY", "")
    token = creds.get("AWS_SESSION_TOKEN", "")
    if not key_id:
        print(f"❌  No AWS credentials found — run: aws sso login --profile {profile}")
        sys.exit(1)

    patch = {"data": {
        "s3-access-key-id":     base64.b64encode(key_id.encode()).decode(),
        "s3-secret-access-key": base64.b64encode(secret.encode()).decode(),
        "aws-session-token":    base64.b64encode(token.encode()).decode(),
    }}
    r = subprocess.run(
        ["kubectl", "patch", "secret", args.secret_name,
         "-n", args.namespace, "--type", "merge", "-p", json.dumps(patch)],
        capture_output=True, text=True,
    )
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit(1)
    print("✓ Patched — restart fl-client to pick up new credentials:")
    print(f"  kubectl rollout restart deployment -n {args.namespace} -l app.kubernetes.io/component=fl-client")


if __name__ == "__main__":
    main()
