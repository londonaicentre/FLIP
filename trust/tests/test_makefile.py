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
"""trust/Makefile's upgrade verb never reaches a first-install step (FLIP#1204).

`up-trust` re-seeds OMOP / Orthanc whenever the seed markers differ from the kit and runs
`xnat-reset`; `upgrade-trust` exists so a live site can move to a release without either. These
dry-run the targets against a scratch kit, so a refactor that reuses up-trust's prerequisites
fails here rather than on a live site.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from make_dry_run import DESTRUCTIVE, KIT, dry_run  # noqa: E402


class UpgradeTrust(unittest.TestCase):
    def test_upgrade_trust_pulls_recreates_and_upgrades_xnat_without_reset(self):
        out = dry_run("upgrade-trust", "TAG=v0.6.0", "YES=1")
        assert "site_upgrade.py plan" in out, out
        assert "_upgrade-trust-apply" in out, out
        for step in DESTRUCTIVE:
            assert step not in out, f"{step!r} reached from upgrade-trust:\n{out}"

    def test_apply_phase_names_the_tag_only_via_the_kit(self):
        """The apply sub-make re-includes the kit, so the tag it uses is whatever the resolver wrote."""
        out = dry_run("_upgrade-trust-apply")
        assert "compose" in out, out
        assert " pull" in out, out
        assert " up -d" in out, out
        assert "upgrade-xnat" in out, out
        assert "sha-badcff1" in out, out  # printed from the kit, not from TAG=
        for step in DESTRUCTIVE:
            assert step not in out, f"{step!r} reached from _upgrade-trust-apply:\n{out}"

    def test_apply_phase_honours_a_cpu_only_override(self):
        """upgrade-trust-ec2 passes NUM_AVAILABLE_GPUS=0: it must beat a template kit's 1 (FLIP#1204).

        The EC2 trust is GPU-less; with the kit's value the apply phase adds the GPU overlay and the
        recreated fl-client fails with "could not select device driver nvidia".
        """
        gpu_kit = KIT.replace("NUM_AVAILABLE_GPUS=0", "NUM_AVAILABLE_GPUS=1")
        assert gpu_kit != KIT, "scratch kit no longer carries NUM_AVAILABLE_GPUS"
        assert ".gpu.yml" in dry_run("_upgrade-trust-apply", kit=gpu_kit), "a GPU kit should get the overlay"
        out = dry_run("_upgrade-trust-apply", "NUM_AVAILABLE_GPUS=0", kit=gpu_kit)
        assert ".gpu.yml" not in out, f"the CPU-only override did not drop the GPU overlay:\n{out}"

    def test_up_trust_is_still_the_first_install_verb(self):
        """The guard would be meaningless if up-trust had quietly stopped seeding and resetting."""
        out = dry_run("up-trust")
        assert "ensure-seeded" in out, out
        assert "up-xnat" in out, out


if __name__ == "__main__":
    if not shutil.which("make"):
        print("make not installed — skipping", file=sys.stderr)
        sys.exit(0)
    unittest.main()
