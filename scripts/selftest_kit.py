#!/usr/bin/env python3
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
"""Write a hub-less trust kit for an Azure node's self-test (FLIP#1390).

`make register-trust` fills a kit's managed blocks from the hub; a self-test has no hub, so
this fills them with values that work locally and can never reach anything: a hub URL under
.invalid, fresh random keys, slot Trust_1. The stack refuses any `<run-make-…>` left behind,
so this refuses first, naming the key.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"^([A-Z0-9_]+)=<run-make-[a-z-]+>$")


def managed_values(backend: str, docker_tag: str) -> dict[str, str]:
    """Values for the blocks `make register-trust` would fill from the hub."""
    return {
        "AES_KEY_BASE64": base64.b64encode(secrets.token_bytes(32)).decode(),
        "CENTRAL_HUB_API_URL": "https://hub.invalid/api",
        "TRUST_API_KEY_HEADER": "X-Trust-API-Key",
        "FL_BACKEND": backend,
        "FLOWER_KIT_DATE": "selftest",
        "FLARE_KIT_DATE": "selftest",
        "DOCKER_TAG": docker_tag,
        "DOCKER_REGISTRY": "ghcr.io/londonaicentre/",
        "DOCKER_FL_TAG": docker_tag,
        "DOCKER_FL_REGISTRY": "ghcr.io/londonaicentre/",
        "NLB_SUBDOMAIN": "fl-server.invalid",
        "FL_SERVER_PORT": "8002",
        "TRUST_API_KEY": secrets.token_urlsafe(32),
        "TRUST_INTERNAL_SERVICE_KEY": secrets.token_urlsafe(32),
        "FL_KIT_SLOT": "Trust_1",
        "FL_KIT_SLOT_NUMBER": "1",
        "EXPECTED_TRUST_ID": str(uuid.uuid4()),
    }


def host_values(fl_kit_dir: str, data_root: str) -> dict[str, str]:
    """Host-local settings for a CPU-only node with its data under data_root."""
    return {
        "NUM_AVAILABLE_GPUS": "0",
        "MEMORY_PER_GPU_IN_GIB": "0",
        "FL_KIT_DIR": fl_kit_dir,
        "OMOP_DATA_DIR": f"{data_root}/omop/db_data",
        "ORTHANC_STORAGE_DIR": f"{data_root}/orthanc-storage",
        "BASE_IMAGES_DOWNLOAD_DIR": data_root,
        "XNAT_DATA_DIR": "/opt/flip/xnat",
    }


def render(template: str, backend: str, docker_tag: str, fl_kit_dir: str, data_root: str) -> str:
    """Fill every placeholder and host setting in the template; refuse an unknown placeholder."""
    managed = managed_values(backend, docker_tag)
    host = host_values(fl_kit_dir, data_root)
    seen: set[str] = set()
    lines = ["TRUST_NAME=Self-test node", "TRUST_CODE=SELFTEST"]
    unfilled = []
    for line in template.splitlines():
        match = PLACEHOLDER.match(line)
        key = match.group(1) if match else line.split("=", 1)[0].lstrip("#").strip()
        if match:
            if key in managed:
                line = f"{key}={managed[key]}"
            elif line.endswith("<run-make-generate-xnat-credentials>"):
                line = f"{key}={secrets.token_urlsafe(24)}"
            else:
                unfilled.append(key)
        elif key in host and (line.startswith(f"{key}=") or line.startswith(f"#{key}=")):
            line = f"{key}={host[key]}"
            seen.add(key)
        lines.append(line)
    for key in sorted(set(host) - seen):
        lines.append(f"{key}={host[key]}")
    if unfilled:
        raise ValueError("no self-test value for: " + ", ".join(unfilled))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a hub-less trust kit for a node self-test.")
    parser.add_argument("--backend", required=True, choices=["nvflare", "flower"])
    parser.add_argument("--fl-kit-dir", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--docker-tag", default="stag")
    parser.add_argument("--template", default=str(REPO_ROOT / "trust" / ".env.example"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"ERROR: {out} already exists. Pass --force to replace it.", file=sys.stderr)
        return 1
    try:
        body = render(Path(args.template).read_text(), args.backend, args.docker_tag, args.fl_kit_dir, args.data_root)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(body)
    os.chmod(out, 0o600)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
