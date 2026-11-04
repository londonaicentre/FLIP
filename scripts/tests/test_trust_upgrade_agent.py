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

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
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
        unknown = agent.Local("v1.2.0", checkout_clean=None, unsupported=False, health=HEALTHY)
        self.assertEqual(agent.decide(policy(), unknown, NOW).reason, agent.UNKNOWN_CHECKOUT)
        remote = agent.Local("v1.2.0", checkout_clean=True, unsupported=True, health=HEALTHY)
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
        report = calls[-1][2]
        self.assertEqual((report["state"], report["release"]), ("FAILED", "v1.3.0"))
        # The outcome of the automatic rollback is part of the report: "site is back on v1.2.0" and
        # "site is still on the release that broke it" are different things to have to fix.
        self.assertIn("boom", report["detail"])
        self.assertIn("rollback", report["detail"])

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

    def test_an_unnameable_kit_is_refused_before_anything_is_claimed(self):
        # kit_identity() would otherwise raise inside upgrade(), with the lease already taken,
        # and do it again on every expiry.
        bad = self.kit.parent / "settings.conf"
        bad.write_text(self.kit.read_text())
        calls = []
        with mock.patch.object(agent, "hub_call", lambda *a, **k: (calls.append(a), (200, policy()))[1]):
            code = agent.tick(bad, Path("/nonexistent"), False)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])

    def test_a_crash_mid_upgrade_still_reports_failed(self):
        # The lease is held from STARTING; an exit the hub never hears about parks the release
        # until the TTL expires and then retries it blind.
        calls = []

        def hub(kit, method, route, body=None):
            calls.append((method, route, body))
            return 200, (policy() if route == "/trust/upgrade-policy" else None)

        with (
            mock.patch.object(agent, "hub_call", hub),
            mock.patch.object(agent, "local_state", return_value=CLEAN),
            mock.patch.object(agent, "upgrade", side_effect=RuntimeError("docker died")),
            self.assertRaises(RuntimeError),
        ):
            agent.tick(self.kit, Path("/nonexistent"), False)
        self.assertEqual([c[2]["state"] for c in calls[1:]], ["STARTING", "FAILED"])
        self.assertIn("docker died", calls[-1][2]["detail"])

    def test_a_report_that_does_not_land_is_retried(self):
        attempts = []

        def hub(kit, method, route, body=None):
            if body and body["state"] == "FAILED":
                attempts.append(body)
                if len(attempts) < 3:
                    raise agent.urllib.error.URLError("hub down")
            return 200, (policy() if route == "/trust/upgrade-policy" else None)

        with (
            mock.patch.object(agent, "hub_call", hub),
            mock.patch.object(agent, "local_state", return_value=CLEAN),
            mock.patch.object(agent, "upgrade", return_value=(False, "boom")),
            mock.patch.object(agent.time, "sleep"),
        ):
            self.assertEqual(agent.tick(self.kit, Path("/nonexistent"), False), 1)
        self.assertEqual(len(attempts), 3)


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


class LeaseRenewal(unittest.TestCase):
    """A `make` that outlives the 2h lease must not lose it — the hub already allows renewal."""

    KIT = {
        "CENTRAL_HUB_API_URL": "https://hub.example/api",
        "TRUST_API_KEY_HEADER": "X-Key",  # pragma: allowlist secret
        "TRUST_API_KEY": "k",  # pragma: allowlist secret
    }

    def test_renews_while_the_upgrade_runs(self):
        calls = []

        def fake_hub_call(kit, method, route, body=None):
            calls.append((method, route, body))
            return 200, None

        with mock.patch.object(agent, "hub_call", fake_hub_call):
            with agent.LeaseRenewal(self.KIT, "v1.3.0", every=0.01):
                time.sleep(0.15)

        renewals = [c for c in calls if c[2] == {"state": "STARTING", "release": "v1.3.0"}]
        self.assertGreaterEqual(len(renewals), 2)
        self.assertEqual(renewals[0][1], "/trust/upgrade-status")

    def test_stops_renewing_once_the_upgrade_is_over(self):
        """A renewal arriving after the FAILED report would re-claim the release nobody cleared."""
        calls = []

        def fake_hub_call(kit, method, route, body=None):
            calls.append(body)
            return 200, None

        with mock.patch.object(agent, "hub_call", fake_hub_call):
            with agent.LeaseRenewal(self.KIT, "v1.3.0", every=0.01):
                time.sleep(0.05)
            seen = len(calls)
            time.sleep(0.1)

        self.assertEqual(len(calls), seen)

    def test_a_failed_renewal_does_not_break_the_upgrade(self):
        def fake_hub_call(*a, **k):
            raise urllib.error.URLError("hub unreachable")

        with mock.patch.object(agent, "hub_call", fake_hub_call):
            with agent.LeaseRenewal(self.KIT, "v1.3.0", every=0.01):
                time.sleep(0.08)
        # Reaching here at all is the assertion: the context manager swallowed it.

    def test_it_renews_before_the_interval_has_passed_nothing(self):
        with mock.patch.object(agent, "hub_call") as hub_call:
            with agent.LeaseRenewal(self.KIT, "v1.3.0", every=60):
                time.sleep(0.02)
        self.assertEqual(hub_call.call_args_list, [])


class Rollback(unittest.TestCase):
    """A failed attempt puts the site back, once, and says so in the failure report."""

    KIT = {}  # read_health is patched; nothing here talks to the hub
    PREV = "v0.12.0"
    TARGET = "v0.13.0"

    def _rollback(self, upgrade_result=(True, "applied"), verified=(True, "up"), previous=PREV):
        run_upgrade = mock.Mock(return_value=upgrade_result)
        with mock.patch.object(agent, "verify", return_value=verified) as verify:
            rolled, how = agent.rollback(self.KIT, run_upgrade, previous, self.TARGET)
        return (rolled, how), run_upgrade, verify

    def test_it_moves_the_site_back_to_the_previous_release(self):
        (rolled, how), run_upgrade, _ = self._rollback()
        self.assertTrue(rolled)
        self.assertIn(self.PREV, how)
        # The tag asked for is the previous release, not the one that failed.
        run_upgrade.assert_called_once_with(self.PREV)

    def test_it_verifies_the_site_is_back_on_the_previous_release(self):
        _, _, verify = self._rollback()
        self.assertEqual(verify.call_args[0][1], self.PREV)
        self.assertLess(
            verify.call_args[1]["timeout_s"],
            agent.VERIFY_TIMEOUT_S,
            "a return to a release that already worked is judged sooner than a forward upgrade",
        )

    def test_an_applied_rollback_that_does_not_come_up_is_not_reported_as_a_success(self):
        (rolled, how), _, _ = self._rollback(verified=(False, "trust-api reports v0.11.0"))
        self.assertFalse(rolled)
        self.assertIn("did not come up", how)

    def test_a_failed_rollback_is_reported_with_its_log(self):
        (rolled, how), _, _ = self._rollback(upgrade_result=(False, "boom"))
        self.assertFalse(rolled)
        self.assertIn("failed", how)
        self.assertIn("boom", how)

    def test_there_is_nothing_to_roll_back_to_on_a_first_upgrade(self):
        """No previous release means nothing is known to work, so the attempt stands as it is."""
        (rolled, how), run_upgrade, _ = self._rollback(previous=None)
        self.assertFalse(rolled)
        self.assertIn("nothing to roll back to", how)
        run_upgrade.assert_not_called()

    def test_a_previous_release_that_is_not_a_tag_is_not_rolled_back_to(self):
        (rolled, how), run_upgrade, _ = self._rollback(previous="sha-abc1234")
        self.assertFalse(rolled)
        self.assertIn("not a tag", how)
        run_upgrade.assert_not_called()

    def test_it_does_not_re_run_the_release_that_just_failed(self):
        (rolled, how), run_upgrade, _ = self._rollback(previous=self.TARGET)
        self.assertFalse(rolled)
        self.assertIn("nothing to roll back to", how)
        run_upgrade.assert_not_called()


class TickRollsBack(unittest.TestCase):
    """The tick rolls back on either failure — the verb's, or the site not coming up."""

    KIT_FILE = Path("/nonexistent")
    REPO = Path("/nope")

    def _tick(self, upgrade_result, verify_result=(True, "v0.13.0")):
        policy = {"enabled": True, "target_release": "v0.13.0", "lease": None}
        local = agent.Local("v0.12.0", True, False, agent.Health(True, "ok", "v0.12.0", True, ()))
        reports = []

        def fake_hub_call(kit, method, route, body=None):
            if body and body.get("state") in ("SUCCEEDED", "FAILED", "BLOCKED"):
                reports.append(body)
            return (200, policy if method == "GET" else None)

        with (
            mock.patch.object(agent, "read_kit", return_value={}),
            mock.patch.object(agent, "kit_identity", return_value=("GSTT", "prod")),
            mock.patch.object(agent, "hub_call", fake_hub_call),
            mock.patch.object(agent, "local_state", return_value=local),
            mock.patch.object(agent, "upgrade", return_value=upgrade_result),
            mock.patch.object(agent, "verify", return_value=verify_result),
            mock.patch.object(agent, "rollback", return_value=(True, "rolled back to v0.12.0")) as rollback,
            mock.patch.object(agent, "LeaseRenewal"),
        ):
            rc = agent.tick(self.KIT_FILE, self.REPO, False)
        return rc, reports, rollback

    def test_a_failed_upgrade_rolls_back_and_reports_failed(self):
        rc, reports, rollback = self._tick((False, "apply failed"))
        self.assertEqual(rc, 1)
        rollback.assert_called_once()
        self.assertEqual(reports[-1]["state"], "FAILED")
        self.assertIn("rolled back to v0.12.0", reports[-1]["detail"])

    def test_a_site_that_does_not_come_up_rolls_back_too(self):
        rc, reports, rollback = self._tick((True, "applied"), verify_result=(False, "never came back"))
        self.assertEqual(rc, 1)
        rollback.assert_called_once()
        self.assertEqual(reports[-1]["state"], "FAILED")

    def test_the_rollback_target_is_the_release_the_site_was_already_on(self):
        _, _, rollback = self._tick((False, "apply failed"))
        self.assertEqual(rollback.call_args[0][2], "v0.12.0")

    def test_a_successful_upgrade_is_not_rolled_back(self):
        rc, reports, rollback = self._tick((True, "applied"), verify_result=(True, "v0.13.0"))
        self.assertEqual(rc, 0)
        rollback.assert_not_called()
        self.assertEqual(reports[-1]["state"], "SUCCEEDED")


class ClusterKit(unittest.TestCase):
    """The pod gets one key from a projected volume and the rest from its env — never a kit file."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "trust-api-key").write_text("k\n")
        self.cluster = agent.Cluster("flip-trust", "flip-trust", "oci://x/y", "GSTT", "prod", self.dir)
        self.env = {
            "CENTRAL_HUB_API_URL": "https://hub.example/api",
            "TRUST_API_KEY_HEADER": "X-Key",  # pragma: allowlist secret
        }

    def test_reads_the_projected_key_and_the_rest_from_the_env(self):
        with mock.patch.dict("os.environ", self.env, clear=False):
            kit = agent.cluster_kit(self.cluster)
        self.assertEqual(kit["TRUST_API_KEY"], "k")
        self.assertEqual(kit["CENTRAL_HUB_API_URL"], "https://hub.example/api")
        self.assertEqual(kit["TRUST_API_KEY_HEADER"], "X-Key")

    def test_a_missing_projection_fails_before_anything_is_claimed(self):
        self.cluster = agent.Cluster("ns", "rel", "oci://x", "GSTT", "prod", self.dir / "absent")
        with mock.patch.dict("os.environ", self.env, clear=False):
            with self.assertRaises(ValueError):
                agent.cluster_kit(self.cluster)

    def test_an_empty_hub_url_fails_rather_than_building_a_relative_url(self):
        with mock.patch.dict("os.environ", {"CENTRAL_HUB_API_URL": ""}, clear=False):
            with self.assertRaises(ValueError):
                agent.cluster_kit(self.cluster)

    def test_the_header_defaults_rather_than_going_out_unheaded(self):
        with mock.patch.dict("os.environ", {"CENTRAL_HUB_API_URL": "https://h/api"}, clear=False):
            os.environ.pop("TRUST_API_KEY_HEADER", None)
            kit = agent.cluster_kit(self.cluster)
        self.assertEqual(kit["TRUST_API_KEY_HEADER"], agent.DEFAULT_TRUST_API_KEY_HEADER)


class ClusterState(unittest.TestCase):
    """Helm's record is the release the next upgrade acts on, so it wins over /health."""

    CLUSTER = agent.Cluster("flip-trust", "flip-trust", "oci://x/y", "GSTT", "prod")

    def _state(self, helm_json, health_version=None, returncode=0):
        health = agent.Health(True, "ok", health_version, True, ())
        with (
            mock.patch.object(agent, "cluster_kit", return_value={}),
            mock.patch.object(agent, "read_health", return_value=health),
            mock.patch.object(agent, "run", return_value=mock.Mock(returncode=returncode, stdout=helm_json, stderr="")),
        ):
            return agent.cluster_state(self.CLUSTER)

    def test_reads_the_release_tag_from_app_version(self):
        local = self._state('[{"name": "flip-trust", "app_version": "v0.13.0"}]')
        self.assertEqual(local.current_release, "v0.13.0")

    def test_a_cluster_has_no_checkout_to_be_dirty(self):
        """`cluster mode` must never report LOCAL_MODIFICATIONS, which has no meaning here."""
        self.assertTrue(self._state('[{"app_version": "v0.13.0"}]').checkout_clean)

    def test_it_is_not_the_unsupported_remote_docker_case(self):
        """The host agent refuses a DOCKER_HOST trust; the cluster agent is the supported case."""
        self.assertFalse(self._state('[{"app_version": "v0.13.0"}]').unsupported)

    def test_a_helm_that_cannot_be_read_falls_back_to_health(self):
        local = self._state("", health_version="v0.12.0", returncode=1)
        self.assertEqual(local.current_release, "v0.12.0")

    def test_an_empty_release_list_is_not_a_release(self):
        local = self._state("[]", health_version="v0.12.0")
        self.assertEqual(local.current_release, "v0.12.0")

    def test_malformed_helm_output_does_not_crash_the_tick(self):
        self.assertEqual(self._state("not json", health_version="v0.12.0").current_release, "v0.12.0")


class ClusterUpgrade(unittest.TestCase):
    """helm pull of the published chart, then an upgrade that keeps the live values."""

    CLUSTER = agent.Cluster(
        "flip-trust", "flip-trust", "oci://ghcr.io/londonaicentre/charts/flip-trust", "GSTT", "prod"
    )

    def _run(self, pull_ok=True):
        calls = []

        def fake_run(cmd, cwd, **kw):
            calls.append((cmd, kw.get("env", {})))
            ok = pull_ok or cmd[:2] != ["helm", "pull"]
            return mock.Mock(returncode=0 if ok else 1, stdout="pulled" if ok else "", stderr="boom")

        with mock.patch.object(agent, "run", fake_run):
            result = agent.cluster_upgrade(self.CLUSTER, "v0.13.0")
        return result, calls

    def test_pulls_the_chart_version_the_release_tag_names(self):
        _, calls = self._run()
        pull = calls[0][0]
        self.assertEqual(pull[:3], ["helm", "pull", "oci://ghcr.io/londonaicentre/charts/flip-trust"])
        # The chart's `version` is SemVer and cannot carry the release tag's leading v.
        self.assertEqual(pull[pull.index("--version") + 1], "0.13.0")

    def test_upgrades_the_release_and_moves_only_the_global_tag(self):
        _, calls = self._run()
        upgrade = calls[1][0]
        self.assertEqual(upgrade[:3], ["helm", "upgrade", "flip-trust"])
        self.assertIn("--reuse-values", upgrade)
        self.assertEqual(upgrade[upgrade.index("--set") + 1], "global.image.tag=v0.13.0")

    def test_it_never_installs(self):
        """A release the configmap driver cannot see must fail, not be installed a second time."""
        _, calls = self._run()
        upgrade = calls[1][0]
        self.assertNotIn("--install", upgrade)

    def test_both_calls_use_the_configmap_driver(self):
        _, calls = self._run()
        for cmd, env in calls:
            self.assertEqual(env.get("HELM_DRIVER"), "configmap", cmd)

    def test_a_failed_pull_never_reaches_helm_upgrade(self):
        (ok, detail), calls = self._run(pull_ok=False)
        self.assertFalse(ok)
        self.assertIn("helm pull", detail)
        self.assertEqual(len(calls), 1)


class ClusterTick(unittest.TestCase):
    """`tick` picks the backend from the mode; the host path is untouched."""

    CLUSTER = agent.Cluster("flip-trust", "flip-trust", "oci://x/y", "GSTT", "prod")

    def test_cluster_mode_never_touches_a_checkout(self):
        policy = {"enabled": True, "target_release": "v0.13.0", "lease": None}
        local = agent.Local("v0.12.0", True, False, agent.Health(True, "ok", "v0.12.0", True, ()))
        with (
            mock.patch.object(agent, "cluster_kit", return_value={}),
            mock.patch.object(agent, "hub_call", side_effect=[(200, policy), (409, None)]),
            mock.patch.object(agent, "cluster_state", return_value=local),
            mock.patch.object(agent, "upgrade") as host_upgrade,
            mock.patch.object(agent, "checkout") as host_checkout,
        ):
            rc = agent.tick(Path("/nonexistent"), Path("/nope"), False, self.CLUSTER)
        self.assertEqual(rc, 0)
        host_upgrade.assert_not_called()
        host_checkout.assert_not_called()

    def test_the_cluster_upgrade_is_what_runs(self):
        policy = {"enabled": True, "target_release": "v0.13.0", "lease": None}
        local = agent.Local("v0.12.0", True, False, agent.Health(True, "ok", "v0.12.0", True, ()))
        with (
            mock.patch.object(agent, "cluster_kit", return_value={}),
            mock.patch.object(agent, "hub_call", side_effect=[(200, policy), (200, None), (200, None)]),
            mock.patch.object(agent, "cluster_state", return_value=local),
            mock.patch.object(agent, "cluster_upgrade", return_value=(True, "fine")) as upgrade,
            mock.patch.object(agent, "verify", return_value=(True, "v0.13.0")),
            mock.patch.object(agent, "governance_drift", return_value=None),
            mock.patch.object(agent, "LeaseRenewal"),
        ):
            rc = agent.tick(Path("/nonexistent"), Path("/nope"), False, self.CLUSTER)
        self.assertEqual(rc, 0)
        upgrade.assert_called_once_with(self.CLUSTER, "v0.13.0")


class GovernanceDrift(unittest.TestCase):
    """The agent can write ConfigMaps for Helm's records, so it checks it did not change the document."""

    DOC = "[fl_privacy]\nminimum_cohort_size = 10\n"
    CLUSTER = agent.Cluster(
        namespace="flip-trust", release="flip-trust", chart_repository="oci://x", kit_name="GSTT", hub="prod"
    )

    def _drift(self, values=None, items=None, helm_rc=0, kubectl_rc=0):
        values_cm = mock.Mock(returncode=helm_rc, stdout=json.dumps(values or {}), stderr="boom")
        items_json = json.dumps({"items": items if items is not None else [{"data": {agent.GOVERNANCE_KEY: self.DOC}}]})
        kubectl_cm = mock.Mock(returncode=kubectl_rc, stdout=items_json, stderr="nope")
        calls = []

        def fake_run(cmd, cwd, env=None):
            calls.append(cmd)
            return values_cm if cmd[0] == "helm" else kubectl_cm

        with mock.patch.object(agent, "run", fake_run):
            return agent.governance_drift(self.CLUSTER), calls

    def test_a_document_that_matches_is_not_drift(self):
        drift, _ = self._drift(values={"governance": {"document": self.DOC}})
        self.assertIsNone(drift)

    def test_a_document_changed_in_the_cluster_is_drift(self):
        drift, _ = self._drift(
            values={"governance": {"document": self.DOC}},
            items=[{"data": {agent.GOVERNANCE_KEY: "[fl_privacy]\nminimum_cohort_size = 1\n"}}],
        )
        self.assertIn("changed outside Helm", drift)

    def test_the_comparison_ignores_the_indentation_helm_renders_with(self):
        drift, _ = self._drift(
            values={"governance": {"document": self.DOC}},
            items=[{"data": {agent.GOVERNANCE_KEY: "    " + self.DOC.replace("\n", "\n    ").rstrip()}}],
        )
        self.assertIsNone(drift)

    def test_a_missing_configmap_is_drift(self):
        drift, _ = self._drift(values={"governance": {"document": self.DOC}}, items=[])
        self.assertIn("no governance ConfigMap", drift)

    def test_two_configmaps_with_the_label_are_drift(self):
        item = {"data": {agent.GOVERNANCE_KEY: self.DOC}}
        drift, _ = self._drift(values={"governance": {"document": self.DOC}}, items=[item, item])
        self.assertIn("2 ConfigMaps", drift)

    def test_a_trust_with_no_governance_document_has_nothing_to_compare(self):
        drift, calls = self._drift(values={"governance": {}})
        self.assertIsNone(drift)
        self.assertEqual([c[0] for c in calls], ["helm"], "no document means no ConfigMap read either")

    def test_values_it_cannot_read_are_reported_rather_than_passed(self):
        drift, _ = self._drift(helm_rc=1)
        self.assertIn("could not read the release values", drift)

    def test_values_that_are_not_json_are_reported(self):
        values_cm = mock.Mock(returncode=0, stdout="not json", stderr="")
        with mock.patch.object(agent, "run", return_value=values_cm):
            drift = agent.governance_drift(self.CLUSTER)
        self.assertIn("not JSON", drift)


class TickChecksGovernance(unittest.TestCase):
    """A drifted document does not get reported as a successful upgrade — and does not roll back."""

    def _tick(self, drift):
        policy = {"enabled": True, "target_release": "v0.13.0", "lease": None}
        local = agent.Local("v0.12.0", True, False, agent.Health(True, "ok", "v0.12.0", True, ()))
        cluster = agent.Cluster(
            namespace="ns", release="flip-trust", chart_repository="oci://x", kit_name="G", hub="prod"
        )
        reports = []

        def fake_hub_call(kit, method, route, body=None):
            if body and body.get("state") in ("SUCCEEDED", "FAILED", "BLOCKED"):
                reports.append(body)
            return (200, policy if method == "GET" else None)

        with (
            mock.patch.object(agent, "cluster_kit", return_value={}),
            mock.patch.object(agent, "hub_call", fake_hub_call),
            mock.patch.object(agent, "cluster_state", return_value=local),
            mock.patch.object(agent, "cluster_upgrade", return_value=(True, "applied")),
            mock.patch.object(agent, "verify", return_value=(True, "up")),
            mock.patch.object(agent, "rollback", return_value=(True, "rolled back")) as rollback,
            mock.patch.object(agent, "governance_drift", return_value=drift),
            mock.patch.object(agent, "LeaseRenewal"),
        ):
            rc = agent.tick(Path("/nonexistent"), Path("/nope"), False, cluster)
        return rc, reports, rollback

    def test_drift_stops_the_success_report(self):
        rc, reports, _ = self._tick("the governance document in the cluster does not match ...")
        self.assertEqual(rc, 1)
        self.assertEqual(reports[-1]["state"], "FAILED")
        self.assertIn("governance document", reports[-1]["detail"])

    def test_drift_does_not_roll_the_release_back(self):
        """The document is not changed by the upgrade, so a rollback would revert what the site means to say."""
        _, _, rollback = self._tick("drift")
        rollback.assert_not_called()

    def test_no_drift_reports_success(self):
        rc, reports, _ = self._tick(None)
        self.assertEqual(rc, 0)
        self.assertEqual(reports[-1]["state"], "SUCCEEDED")


class KitFile(unittest.TestCase):
    def test_reads_values_and_ignores_comments(self):
        p = Path(tempfile.mkdtemp()) / "k"
        p.write_text("# c\nA=1\nB='two'\n\nC=x=y\n")
        self.assertEqual(agent.read_kit(p), {"A": "1", "B": "two", "C": "x=y"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
