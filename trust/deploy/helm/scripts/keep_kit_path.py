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

"""Keep the FL kit path a deployed release already mounts (FLIP#1984).

`deploy-release` sets `flClient.kitHostPath` from KIT_DEST, which defaults to /opt/flip/fl-kit. A
trust whose kit was staged elsewhere was deployed with KIT_DEST=<path>, and any later deploy or
upgrade that did not repeat it moved the release back to the default: the new fl-client pod could
not mount its kit while the old one was already gone.

Reads `helm get values <release> -o json` on stdin and prints the `--set` that keeps the release's
path, or nothing for a first install (no release, so nothing on stdin) or a release without one.
The Makefile runs this only when the operator passed neither KIT_DEST nor FL_CLIENT_KIT_HOST_PATH,
so passing either is how a kit is moved. stage-kit runs it with --path, so a re-stage goes where the
release mounts rather than where KIT_DEST defaults (a FLIP_DATA_ROOT added to a live trust's kit, FLIP#2113).
"""

from __future__ import annotations

import argparse
import json
import sys


def kept_path(values_json: str) -> str:
    """The release's flClient.kitHostPath, or "" when there is none to keep."""
    try:
        values = json.loads(values_json) if values_json.strip() else None
    except ValueError:
        return ""
    fl_client = (values or {}).get("flClient") or {}
    path = fl_client.get("kitHostPath") if isinstance(fl_client, dict) else None
    return path.strip() if isinstance(path, str) else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--default", default="", help="the path the Makefile would set otherwise")
    parser.add_argument("--path", action="store_true", help="print the path alone, not the --set")
    args = parser.parse_args()

    path = kept_path(sys.stdin.read())
    if not path:
        return 0
    if path != args.default:
        print(
            f"ℹ️  Keeping flClient.kitHostPath={path}, the path the release already mounts. "
            "Pass KIT_DEST=<path> to move it.",
            file=sys.stderr,
        )
    print(path if args.path else f"--set flClient.kitHostPath={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
