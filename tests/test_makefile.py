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
"""The root Makefile's site upgrade verb is gated and data-safe (FLIP#1204).

`make upgrade-onprem-trust` must run the readiness checklist and then reach the trust-level
upgrade, never the first-install steps (`ensure-seeded` re-seeds OMOP / Orthanc, `xnat-reset`
wipes the XNAT archive and database).

Usage:
    python3 tests/test_makefile.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_dry_run import assert_data_safe, dry_run, main  # noqa: E402


class UpgradeOnpremTrust(unittest.TestCase):
    def test_upgrade_onprem_trust_is_gated_and_data_safe(self):
        out = dry_run("upgrade-onprem-trust", "TAG=v0.6.0", "YES=1", subdir=".")
        assert "onboard_onprem_trust.py" in out, out  # the readiness checklist gates it
        assert "site_upgrade.py plan" in out, out
        assert "--tag v0.6.0" in out, out
        assert "--yes" in out, out
        assert "onboard_onprem_trust.py SCR --gate" in out, out  # the gate must not suggest up-onprem-trust
        assert "_upgrade-trust-apply" in out, out
        assert_data_safe(out, "upgrade-onprem-trust")


if __name__ == "__main__":
    main()
