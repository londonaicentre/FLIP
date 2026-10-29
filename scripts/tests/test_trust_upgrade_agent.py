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
# ruff: noqa: PT009, PT027 - unittest assertions: CI runs scripts/tests/ with bare `python <file>`, no pytest.
"""The upgrade agent's rules (FLIP trust auto-upgrade): ``decide`` and ``in_window``, plus the tick's I/O order.

Stdlib only, run as ``python scripts/tests/test_trust_upgrade_agent.py`` like its siblings.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import trust_upgrade_agent as agent  # noqa: E402

NOW = datetime(2026, 10, 23, 2, 0)  # 03:00 BST
HEALTHY = agent.Health(reachable=True, status="ok", version="v1.2.0", hub_key_match=True, dead_tasks=())
CLEAN = agent.Local(current_release="v1.2.0", checkout_clean=True, unsupported=False, health=HEALTHY)


def policy(**kw):
    base = {
        "enabled": True,
        "target_release": "v1.3.0",
        "maintenance_window": None,
        "timezone": None,
        "is_busy": False,
        "lease": None,
        "failed_release": None,
    }
    return {**base, **kw}


class InWindow(unittest.TestCase):
    def test_no_window_is_always_open(self):
        self.assertTrue(agent.in_window(NOW, None, None))

    def test_follows_the_trust_zone(self):
        w = {"start": "01:00", "end": "05:00"}
        self.assertTrue(agent.in_window(NOW, w, "Europe/London"))  # 03:00 BST
        self.assertFalse(agent.in_window(NOW, {"start": "04:00", "end": "05:00"}, "Europe/London"))

    def test_overnight_and_exclusive_end(self):
        w = {"start": "22:00", "end": "04:00"}
        self.assertTrue(agent.in_window(datetime(2026, 10, 23, 23, 0), w, "UTC"))
        self.assertTrue(agent.in_window(datetime(2026, 10, 23, 3, 59), w, "UTC"))
        self.assertFalse(agent.in_window(datetime(2026, 10, 23, 4, 0), w, "UTC"))


class Decide(unittest.TestCase):
    def test_upgrades_when_every_condition_holds(self):
        d = agent.decide(policy(), CLEAN, NOW)
        self.assertEqual((d.action, d.release), ("UPGRADE", "v1.3.0"))

    def test_off_means_nothing(self):
        self.assertEqual(agent.decide(policy(enabled=False), CLEAN, NOW).action, "SKIP")

    def test_never_upgrades_to_a_non_release_identity(self):
        for target in (None, "sha-abc1234", "latest"):
            self.assertEqual(agent.decide(policy(target_release=target), CLEAN, NOW).action, "SKIP", target)

    def test_already_current(self):
        self.assertEqual(agent.decide(policy(target_release="v1.2.0"), CLEAN, NOW).action, "SKIP")

    def test_a_failed_release_is_not_retried(self):
        d = agent.decide(policy(failed_release="v1.3.0"), CLEAN, NOW)
        self.assertEqual(d.action, "SKIP")
        # ...but the next release is a fresh attempt.
        self.assertEqual(agent.decide(policy(failed_release="v1.2.5"), CLEAN, NOW).action, "UPGRADE")

    def test_waits_outside_the_window_and_while_busy_and_during_a_lease(self):
        self.assertEqual(
            agent.decide(
                policy(maintenance_window={"start": "04:00", "end": "05:00"}, timezone="Europe/London"), CLEAN, NOW
            ).action,
            "SKIP",
        )
        self.assertEqual(agent.decide(policy(is_busy=True), CLEAN, NOW).action, "SKIP")
        self.assertEqual(
            agent.decide(policy(lease={"release": "v1.3.0", "expires_at": "x"}), CLEAN, NOW).action, "SKIP"
        )

    def test_host_blockers_are_reported_not_forced(self):
        dirty = agent.Local("v1.2.0", checkout_clean=False, unsupported=False, health=HEALTHY)
        self.assertEqual(agent.decide(policy(), dirty, NOW).reason, agent.LOCAL_MODIFICATIONS)
        unknown = agent.Local("v1.2.0", checkout_clean=None, unsupported=False)
        self.assertEqual(agent.decide(policy(), unknown, NOW).reason, agent.UNKNOWN_CHECKOUT)
        remote = agent.Local("v1.2.0", checkout_clean=True, unsupported=True)
        self.assertEqual(agent.decide(policy(), remote, NOW).reason, agent.UNSUPPORTED_MODE)

    def test_a_blocker_is_reported_even_outside_the_window(self):
        dirty = agent.Local("v1.2.0", checkout_clean=False, unsupported=False, health=HEALTHY)
        p = policy(maintenance_window={"start": "04:00", "end": "05:00"}, timezone="UTC")
        self.assertEqual(agent.decide(p, dirty, NOW).action, "BLOCKED")

    def test_an_unhealthy_site_is_never_touched(self):
        """Recreating every container on an already-broken site makes the two causes indistinguishable."""
        for health, needle in [
            (agent.Health(False, None, None, None, ()), "unreachable"),
            (agent.Health(True, "degraded", "v1.2.0", True, ("task",)), "degraded"),
            (agent.Health(True, "ok", "v1.2.0", True, ("poller",)), "poller"),
            (agent.Health(True, "ok", "v1.2.0", False, ()), "AES key"),
        ]:
            with self.subTest(health=health):
                d = agent.decide(policy(), agent.Local("v1.2.0", True, False, health), NOW)
                self.assertEqual((d.action, d.reason), ("BLOCKED", agent.SITE_UNHEALTHY))
                self.assertIn(needle, d.detail or "")

    def test_being_unhealthy_does_not_nag_when_there_is_nothing_to_do(self):
        """A disabled or already-current trust must not report a blocker it was never going to act on."""
        sick = agent.Local("v1.3.0", True, False, agent.Health(False, None, None, None, ()))
        self.assertEqual(agent.decide(policy(), sick, NOW).action, "SKIP")
        self.assertEqual(agent.decide(policy(enabled=False), sick, NOW).action, "SKIP")


class Tick(unittest.TestCase):
    """The order of side effects: claim before touching anything, report whatever happened."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.kit = Path(d) / ".env.GSTT.prod"
        self.kit.write_text(
            "CENTRAL_HUB_API_URL=https://hub.example/api\nTRUST_API_KEY_HEADER=X-Trust-API-Key\nTRUST_API_KEY=k\nFL_KIT_SLOT=Trust_1\n"
        )

    def _run(self, claim_status=200, upgrade_result=(True, ""), pol=None, dry_run=False, verified=(True, "on v1.3.0")):
        calls = []

        def hub(kit, method, route, body=None):
            calls.append((method, route, body))
            if route == "/trust/upgrade-policy":
                return 200, pol or policy()
            return (claim_status, None) if body and body["state"] == "STARTING" else (200, None)

        with (
            mock.patch.object(agent, "hub_call", hub),
            mock.patch.object(agent, "local_state", return_value=CLEAN),
            mock.patch.object(agent, "verify", return_value=verified),
            mock.patch.object(agent, "upgrade", return_value=upgrade_result) as up,
        ):
            code = agent.tick(self.kit, Path("/nonexistent"), dry_run)
        return code, calls, up

    def test_success_claims_then_reports(self):
        code, calls, up = self._run()
        self.assertEqual(code, 0)
        self.assertEqual([c[2]["state"] for c in calls[1:]], ["STARTING", "SUCCEEDED"])
        up.assert_called_once()

    def test_failure_reports_the_log_tail_and_exits_nonzero(self):
        code, calls, _ = self._run(upgrade_result=(False, "boom"))
        self.assertEqual(code, 1)
        self.assertEqual(calls[-1][2], {"state": "FAILED", "release": "v1.3.0", "detail": "boom"})

    def test_a_refused_claim_upgrades_nothing(self):
        code, calls, up = self._run(claim_status=409)
        self.assertEqual(code, 0)
        up.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_an_upgrade_that_does_not_come_up_is_a_failure(self):
        """`make` exiting 0 only says the deployment applied; SUCCEEDED requires the site to be up on it."""
        code, calls, _ = self._run(verified=(False, "did not come up on v1.3.0: unreachable"))
        self.assertEqual(code, 1)
        self.assertEqual(calls[-1][2]["state"], "FAILED")
        self.assertIn("did not come up", calls[-1][2]["detail"])

    def test_dry_run_changes_and_reports_nothing(self):
        code, calls, up = self._run(dry_run=True)
        self.assertEqual((code, len(calls)), (0, 1))
        up.assert_not_called()

    def test_off_is_just_the_one_read(self):
        code, calls, up = self._run(pol=policy(enabled=False))
        self.assertEqual((code, len(calls)), (0, 1))
        up.assert_not_called()

    def test_unreadable_hub_is_exit_2(self):
        with mock.patch.object(agent, "hub_call", side_effect=agent.urllib.error.URLError("down")):
            self.assertEqual(agent.tick(self.kit, Path("/nonexistent"), False), 2)


class Verify(unittest.TestCase):
    """Deciding whether the site actually came back up on the target, without real sleeping."""

    def _verify(self, reads, tag="v1.3.0", timeout_s=30):
        clock = {"t": 0.0}
        seq = list(reads)

        def now():
            return clock["t"]

        def sleep(s):
            clock["t"] += s

        def read(_kit):
            return seq.pop(0) if len(seq) > 1 else seq[0]

        with mock.patch.object(agent, "read_health", read):
            return agent.verify({}, tag, timeout_s=timeout_s, poll_s=10, sleep=sleep, now=now)

    def test_passes_as_soon_as_the_target_is_serving(self):
        ok, detail = self._verify([agent.Health(True, "ok", "v1.3.0", True, ())])
        self.assertTrue(ok)
        self.assertIn("v1.3.0", detail)

    def test_waits_through_the_restart_then_passes(self):
        """The site is down while containers are recreated; that is the normal path, not a failure."""
        ok, _ = self._verify(
            [
                agent.Health(False, None, None, None, ()),
                agent.Health(True, "ok", "v1.2.0", True, ()),
                agent.Health(True, "ok", "v1.3.0", True, ()),
            ]
        )
        self.assertTrue(ok)

    def test_still_on_the_old_release_at_the_deadline_fails(self):
        ok, detail = self._verify([agent.Health(True, "ok", "v1.2.0", True, ())])
        self.assertFalse(ok)
        self.assertIn("not v1.3.0", detail)

    def test_up_on_the_target_but_degraded_fails(self):
        ok, detail = self._verify([agent.Health(True, "degraded", "v1.3.0", True, ("poller",))])
        self.assertFalse(ok)
        self.assertIn("poller", detail)

    def test_never_coming_back_fails(self):
        ok, detail = self._verify([agent.Health(False, None, None, None, ())])
        self.assertFalse(ok)
        self.assertIn("never came back", detail)


class KitIdentity(unittest.TestCase):
    def test_kit_name_and_hub_come_from_the_filename_not_the_fl_slot(self):
        for name, expected in {
            ".env.GSTT.prod": ("GSTT", "prod"),
            ".env.GSTT.stag": ("GSTT", "stag"),
            ".env.KCH.production": ("KCH", "prod"),
            ".env.KCH.lza-prod": ("KCH", "prod"),
            ".env.KCH.lza-stag": ("KCH", "stag"),
        }.items():
            self.assertEqual(agent.kit_identity(Path("/etc/flip/trust") / name), expected)

    def test_refuses_what_is_not_a_kit_file(self):
        for name in (".env.GSTT.development", ".env.GSTT", "GSTT.production"):
            with self.assertRaises(ValueError):
                agent.kit_identity(Path(name))


class UpgradeRuns(unittest.TestCase):
    """The verb is run in the agent's checkout, after moving it to the tag, against the out-of-tree kit."""

    def test_checks_out_the_tag_then_runs_the_verb_with_the_kit_path(self):
        calls = []

        def fake_run(cmd, cwd, **kw):
            calls.append((cmd, cwd))
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        repo, kit = Path("/opt/flip/releases/FLIP"), Path("/etc/flip/trust/.env.GSTT.prod")
        with mock.patch.object(agent, "run", fake_run):
            ok, _ = agent.upgrade(repo, kit, "v1.3.0")
        self.assertTrue(ok)
        self.assertEqual([c[0][:2] for c in calls[:2]], [["git", "fetch"], ["git", "checkout"]])
        make = calls[-1][0]
        self.assertEqual(make[:2], ["make", "upgrade-onprem-trust"])
        for arg in ("KIT=GSTT", "HUB=prod", "TAG=v1.3.0", "YES=1", f"KIT_FILE={kit}"):
            self.assertIn(arg, make)
        self.assertTrue(all(c[1] == repo for c in calls))

    def test_a_failed_checkout_never_reaches_make(self):
        calls = []

        def fake_run(cmd, cwd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=1, stdout="", stderr="no such tag")

        with mock.patch.object(agent, "run", fake_run):
            ok, tail = agent.upgrade(Path("/r"), Path("/etc/flip/trust/.env.GSTT.prod"), "v9.9.9")
        self.assertFalse(ok)
        self.assertIn("no such tag", tail)
        self.assertNotIn("make", [c[0] for c in calls])


class KitFile(unittest.TestCase):
    def test_reads_values_and_ignores_comments(self):
        p = Path(tempfile.mkdtemp()) / "k"
        p.write_text("# c\nA=1\nB='two'\n\nC=x=y\n")
        self.assertEqual(agent.read_kit(p), {"A": "1", "B": "two", "C": "x=y"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
