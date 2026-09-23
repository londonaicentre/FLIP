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
"""trust/xnat/Makefile's in-place upgrade checks the XNAT directories where they are (FLIP#1204)."""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from make_dry_run import dry_run  # noqa: E402


class UpgradeXnat(unittest.TestCase):
    def test_upgrade_xnat_checks_ownership_where_the_directories_are(self):
        """With a remote (ssh://) docker context the XNAT dirs live on the trust host (FLIP#1204).

        upgrade-trust-ec2 drives upgrade-xnat from the admin workstation, so a bare `stat -c` reads
        this machine's filesystem (and fails outright on BSD stat) instead of the trust host's —
        the same split xnat-reset already makes.
        """
        out = dry_run("upgrade-xnat", subdir="trust/xnat")
        assert "ssh://*)" in out, f"upgrade-xnat has no remote-context branch:\n{out}"
        assert 'ssh "$xnat_ctx" "stat -c' in out, f"the remote branch must stat over ssh:\n{out}"
        assert "Fix: ssh $xnat_ctx sudo chown" in out, f"the remote remedy must be runnable on the host:\n{out}"


if __name__ == "__main__":
    if not shutil.which("make"):
        print("make not installed — skipping", file=sys.stderr)
        sys.exit(0)
    unittest.main()
