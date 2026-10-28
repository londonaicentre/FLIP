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
# ruff: noqa: PT008, PT009, PT027 - unittest assertions: CI runs scripts/tests/ with bare `python <file>`, no pytest.
"""``setup_auto_upgrade.py``: the units it writes, the answer paths, and that nothing changes on --dry-run.

Everything under /etc and /opt is redirected into a temp dir; no systemctl, git or hub call is ever made.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import setup_auto_upgrade as setup  # noqa: E402

KIT_TEXT = "CENTRAL_HUB_API_URL=https://hub.example/api\nTRUST_API_KEY_HEADER=X-Trust-API-Key\nTRUST_API_KEY=k\n"


class Sandbox(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.kit = root / "repo" / ".env.GSTT.prod"
        self.kit.parent.mkdir()
        self.kit.write_text(KIT_TEXT)
        self.etc, self.units, self.checkout = root / "etc", root / "units", root / "opt" / "FLIP"
        self.units.mkdir()
        self.calls, self.toggles = [], []
        patches = [
            mock.patch.object(setup, "ETC_KIT_DIR", self.etc),
            mock.patch.object(setup, "UNIT_DIR", self.units),
            mock.patch.object(setup, "CHECKOUT", self.checkout),
            mock.patch.object(setup, "run", lambda cmd, dry, **kw: self.calls.append(cmd)),
            mock.patch.object(setup, "set_hub_toggle", lambda kit, on: self.toggles.append(on)),
            mock.patch.object(setup.os, "geteuid", lambda: 0),
            mock.patch.object(setup.shutil, "which", lambda name: "/bin/systemctl"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def main(self, *args):
        with mock.patch("builtins.print"):
            return setup.main(["--kit-file", str(self.kit), *args])


class Install(Sandbox):
    def test_yes_installs_everything_and_turns_the_hub_toggle_on(self):
        self.assertEqual(self.main("--answer", "yes"), 0)
        shared = self.etc / ".env.GSTT.prod"
        self.assertEqual(shared.read_text(), KIT_TEXT)
        self.assertEqual(oct(shared.stat().st_mode & 0o777), "0o600")
        self.assertTrue(self.kit.is_symlink())
        self.assertEqual(self.kit.resolve(), shared.resolve())
        self.assertEqual(self.toggles, [True])
        self.assertIn(["systemctl", "enable", "--now", "flip-trust-upgrade.timer"], self.calls)
        self.assertTrue(any(c[:3] == ["git", "clone", "--no-checkout"] for c in self.calls))

    def test_the_service_points_at_the_agents_checkout_and_the_shared_kit(self):
        self.main("--answer", "yes")
        service = (self.units / "flip-trust-upgrade.service").read_text()
        self.assertIn(f"{self.checkout}/scripts/trust_upgrade_agent.py tick", service)
        self.assertIn(f"--kit-file {self.etc / '.env.GSTT.prod'}", service)
        self.assertIn(f"--checkout {self.checkout}", service)
        self.assertIn("Type=oneshot", service)

    def test_the_timer_is_hourly(self):
        self.main("--answer", "yes")
        self.assertIn("OnUnitActiveSec=1h", (self.units / "flip-trust-upgrade.timer").read_text())

    def test_running_it_twice_is_harmless(self):
        self.main("--answer", "yes")
        self.assertEqual(self.main("--answer", "yes"), 0)
        self.assertEqual(self.toggles, [True, True])
        self.assertEqual(self.kit.resolve(), (self.etc / ".env.GSTT.prod").resolve())

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.main("--answer", "yes", "--dry-run"), 0)
        self.assertFalse(self.kit.is_symlink())
        self.assertFalse(self.etc.exists())
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertEqual(self.toggles, [])


class Decline(Sandbox):
    def test_no_tells_the_hub_off_because_a_new_install_defaults_on(self):
        self.assertEqual(self.main("--answer", "no"), 0)
        self.assertEqual(self.toggles, [False])
        self.assertFalse(self.kit.is_symlink())
        self.assertEqual(self.calls, [])

    def test_no_after_yes_stops_the_timer(self):
        self.main("--answer", "yes")
        self.calls.clear()
        self.main("--answer", "no")
        self.assertIn(["systemctl", "disable", "--now", "flip-trust-upgrade.timer"], self.calls)
        self.assertEqual(self.toggles[-1], False)

    def test_disclosure_names_the_off_switch(self):
        self.assertIn("switch this off at any time from the Trust portal", " ".join(setup.DISCLOSURE.split()))


class Refusals(Sandbox):
    def test_needs_root(self):
        with mock.patch.object(setup.os, "geteuid", lambda: 1000):
            self.assertEqual(self.main("--answer", "yes"), 2)

    def test_needs_systemd(self):
        with mock.patch.object(setup.shutil, "which", lambda name: None):
            self.assertEqual(self.main("--answer", "yes"), 2)

    def test_refuses_a_remotely_driven_host(self):
        self.kit.write_text(KIT_TEXT + "DOCKER_HOST=ssh://ubuntu@ec2\n")
        self.assertEqual(self.main("--answer", "yes"), 2)
        self.assertEqual(self.toggles, [])

    def test_missing_kit(self):
        self.kit.unlink()
        self.assertEqual(self.main("--answer", "yes"), 2)

    def test_will_not_clobber_a_different_kit_already_in_etc(self):
        self.etc.mkdir()
        (self.etc / ".env.GSTT.prod").write_text("other")
        with self.assertRaises(SystemExit):
            self.main("--answer", "yes")
        self.assertEqual(self.toggles, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
