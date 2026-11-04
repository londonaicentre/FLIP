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

"""Write a slot's FL kit directory as the Secret the chart reads with flClient.kit.source=secret.

A managed node pool (AKS, FLIP#1390) replaces nodes at will, so the kit cannot live on one. Each file
the fl-client mounts (one directory level down: NVFLARE's local/ and startup/, Flower's certificates/
and keys/) becomes one key, <subdir>__<file>, because a Secret key cannot hold a slash; the chart's
stage-fl-kit init container lays them back out. Prints the Secret as YAML for `kubectl apply -f -`.

Usage:
    python3 scripts/kit_secret.py --kit-src <slot kit dir> --name <secret> --namespace <ns>
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

# Kubernetes refuses a Secret whose data exceeds 1 MiB.
SECRET_LIMIT = 1024 * 1024
# The file each backend's client cannot start without, as <subdir>/<file> (a glob for Flower,
# whose credential is numbered by slot).
REQUIRED = {
    "nvflare": ["startup/fed_client.json"],
    "flower": ["certificates/ca.crt", "keys/supernode_credentials_*"],
}
# The only Flower files a SuperNode reads. A provisioning tree also holds ca.key, the SuperLink's
# server.key and every other slot's credential, none of which may reach this trust (FLIP#965).
FLOWER_CA = "certificates__ca.crt"
FLOWER_CREDENTIAL = re.compile(r"keys__supernode_credentials_\d+")


def collect(kit_src: Path) -> dict[str, bytes]:
    """Every mounted kit file as {<subdir>__<file>: content}; root-level files are not mounted.

    Raises:
        ValueError: A directory nested below a subdirectory (the chart cannot lay it out).
    """
    data: dict[str, bytes] = {}
    for sub in sorted(p for p in kit_src.iterdir() if p.is_dir()):
        for entry in sorted(sub.iterdir()):
            if entry.is_dir():
                nested = ", ".join(str(p.relative_to(kit_src)) for p in entry.rglob("*") if p.is_file())
                raise ValueError(f"nested directory {entry.relative_to(kit_src)} is not supported ({nested})")
            data[f"{sub.name}__{entry.name}"] = entry.read_bytes()
    return data


def backend_of(kit_src: Path) -> str | None:
    """The backend whose required files the kit carries, or None."""
    for backend, required in REQUIRED.items():
        if all(any(kit_src.glob(pattern)) for pattern in required):
            return backend
    return None


def flower_problems(data: dict[str, bytes]) -> list[str]:
    """Why a Flower kit is not one slot's: files beyond ca.crt and its own credential, or not one credential."""
    credentials = [key for key in data if FLOWER_CREDENTIAL.fullmatch(key)]
    problems = [
        f"{key.replace('__', '/', 1)} does not belong in a trust's kit"
        for key in sorted(data)
        if key != FLOWER_CA and key not in credentials
    ]
    if len(credentials) != 1:
        problems.append(f"needs exactly one keys/supernode_credentials_<slot>, found {len(credentials)}")
    return problems


def render(name: str, namespace: str, encoded: dict[str, str]) -> str:
    """The Secret as YAML, from base64-encoded values (keys and base64 need no quoting)."""
    lines = [
        "apiVersion: v1",
        "kind: Secret",
        "type: Opaque",
        "metadata:",
        f"  name: {name}",
        f"  namespace: {namespace}",
        "  labels:",
        "    app.kubernetes.io/component: fl-kit",
        "data:",
    ]
    lines += [f"  {key}: {value}" for key, value in sorted(encoded.items())]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kit-src", type=Path, required=True, help="the slot's kit directory")
    parser.add_argument("--name", required=True, help="Secret name (the chart's flClient.kit.secretName)")
    parser.add_argument("--namespace", required=True)
    args = parser.parse_args()

    if not args.kit_src.is_dir():
        print(f"❌ {args.kit_src} is not a directory", file=sys.stderr)
        return 1
    backend = backend_of(args.kit_src)
    if backend is None:
        wanted = "; ".join(f"{b}: {', '.join(r)}" for b, r in REQUIRED.items())
        print(f"❌ {args.kit_src} is not a slot's FL kit (needs {wanted})", file=sys.stderr)
        return 1
    try:
        data = collect(args.kit_src)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if backend == "flower" and (problems := flower_problems(data)):
        print(f"❌ {args.kit_src} is not one slot's Flower kit:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    encoded = {key: base64.b64encode(value).decode() for key, value in data.items()}
    size = sum(len(v) for v in encoded.values())
    if size > SECRET_LIMIT:
        print(f"❌ the kit is {size} bytes encoded; a Secret holds at most 1 MiB", file=sys.stderr)
        return 1
    sys.stdout.write(render(args.name, args.namespace, encoded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
