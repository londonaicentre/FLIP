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

"""Register FLIP's site components on the NVFLARE server (FLIP#1390).

Run by the fl-server at container start (``python -m flip.nvflare.server_site_config /app/local``).
NVFLARE merges every ``local/*__p_resources.json`` into the server's *parent* process config and
appends its ``components`` to the provisioned ``resources.json`` ones, so FLIP's components live in a
file of their own: the provisioned kit is never edited, and no job process loads them.

The file carries ``components`` only. Any other key the provisioned ``resources.json`` also sets would
be a merge conflict.

Stdlib-only, like ``site_policy``: it runs before NVFLARE starts.
"""

import argparse
import json
import sys
from pathlib import Path

PARENT_RESOURCES_FILE = "flip__p_resources.json"

SERVER_COMPONENTS = [
    {
        # Tells the hub why NVFLARE's scheduler has not started a job, and fails the model on its last try.
        "id": "flip_job_scheduling_reporter",
        "path": "flip.nvflare.components.job_scheduling_reporter.JobSchedulingReporter",
        "args": {},
    },
]


def write_server_site_config(local_dir: Path) -> Path:
    """Write FLIP's server site components into the kit's ``local`` directory.

    Args:
        local_dir (Path): the server kit's ``local`` directory.

    Returns:
        Path: the file written.

    Raises:
        FileNotFoundError: if ``local_dir`` does not exist.
    """
    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        raise FileNotFoundError(f"NVFLARE server local directory not found: {local_dir}")
    target = local_dir / PARENT_RESOURCES_FILE
    target.write_text(json.dumps({"components": SERVER_COMPONENTS}, indent=2) + "\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("local_dir", type=Path, help="the NVFLARE server kit's local directory")
    args = parser.parse_args(argv)
    try:
        target = write_server_site_config(args.local_dir)
    except OSError as e:
        print(f"❌ cannot register FLIP's server components: {e}", file=sys.stderr)
        return 1
    print(f"✅ FLIP server components registered in {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
