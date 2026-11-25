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

"""Refuse a deploy that would move the FL kit path a release mounts (FLIP#2042, FLIP#1984).

The kit file's FL_KIT_DIR is where the FL kit lives on the node. A release deployed by an earlier
checkout may mount another path, passed as KIT_DEST on the command line and never written to the
kit file; deploying it from the kit file would point the FL client at a folder that does not hold
its kit, with the old pod already gone (the Deployment is Recreate).

Reads `helm get values <release> -o json` on stdin. Exits 1, before anything changes, when the
release mounts a path other than --kit-path. A first install (nothing on stdin), a release whose
kit is a Secret or whose FL client is off, and --moved (the operator staged the kit at the new
path) all pass.
"""

from __future__ import annotations

import argparse
import json
import sys


def mounted_path(values_json: str) -> str:
    """The kit hostPath the release mounts, or "" when it mounts none."""
    try:
        values = json.loads(values_json) if values_json.strip() else None
    except ValueError:
        return ""
    fl_client = (values or {}).get("flClient")
    if not isinstance(fl_client, dict) or fl_client.get("enabled") is False:
        return ""
    if (fl_client.get("kit") or {}).get("source") == "secret":
        return ""
    path = fl_client.get("kitHostPath")
    return path.strip() if isinstance(path, str) else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit-path", required=True, help="the path this deploy renders (the kit's FL_KIT_DIR)")
    parser.add_argument("--kit-file", default="", help="the kit file, for the message")
    parser.add_argument("--moved", action="store_true", help="the kit is staged at --kit-path: move the release")
    args = parser.parse_args()

    mounted = mounted_path(sys.stdin.read())
    if not mounted or mounted == args.kit_path or args.moved:
        return 0
    kit_file = args.kit_file or "the kit file"
    print(
        f"❌ The release mounts the FL kit from {mounted}, but this deploy would mount {args.kit_path}.\n"
        f"   The path comes from FL_KIT_DIR in {kit_file}. Nothing has been changed.\n"
        f"   If the kit is at {mounted}: set FL_KIT_DIR={mounted} in {kit_file} and run this again.\n"
        f"   If you are moving it to {args.kit_path}: stage it there first (make stage-kit KIT=<CODE> HUB=<env> ...),\n"
        "   then run this again with KIT_MOVED=1.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
