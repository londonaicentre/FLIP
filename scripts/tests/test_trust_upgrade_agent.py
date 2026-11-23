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
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import trust_upgrade_agent as agent  # noqa: E402

NOW = datetime(2026, 10, 23, 2, 0)  # 03:00 BST
HEALTHY = agent.Health(reachable=True, status="ok", version="v1.2.0", hub_key_match=True, dead_tasks=())
CLEAN = agent.Local(current_release="v1.2.0", health=HEALTHY)
CLUSTER = agent.Cluster("flip-trust", "flip-trust", "oci://x/y", "GSTT", "prod")
KIT = {"CENTRAL_HUB_API_URL": "https://hub.example/api", "TRUST_API_KEY_HEADER": "X", "TRUST_API_KEY": "k"}


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

    def test_never_moves_to_a_release_candidate(self):
        d = agent.decide(policy(target_release="v1.3.0-rc.1"), CLEAN, NOW)
        self.assertEqual(d.action, "SKIP")
        self.assertIn("not a stable release", d.reason)

    def test_never_follows_the_hub_down(self):
        for target in ("v1.1.9", "v1.0.0", "v0.9.0"):
            d = agent.decide(policy(target_release=target), CLEAN, NOW)
            self.assertEqual(d.action, "SKIP", target)
            self.assertIn("older than v1.2.0", d.reason)

    def test_moves_off_a_release_candidate_to_its_release(self):
        on_rc = agent.Local(current_release="v1.3.0-rc.2", health=HEALTHY)
        self.assertEqual(agent.decide(policy(target_release="v1.3.0"), on_rc, NOW).action, "UPGRADE")

    def test_release_key_orders_versions_numerically(self):
        self.assertLess(agent.release_key("v1.9.0"), agent.release_key("v1.10.0"))
        self.assertLess(agent.release_key("v1.3.0-rc.1"), agent.release_key("v1.3.0"))
        self.assertIsNone(agent.release_key("sha-abc1234"))
        self.assertIsNone(agent.release_key(None))

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

    def test_a_blocker_is_reported_even_outside_the_window(self):
        sick = agent.Local("v1.2.0", agent.Health(False, None, None, None, ()))
        p = policy(maintenance_window={"start": "04:00", "end": "05:00"}, timezone="UTC")
        self.assertEqual(agent.decide(p, sick, NOW).action, "BLOCKED")

    def test_the_host_only_blockers_are_gone(self):
        """Auto-upgrade is Kubernetes-only: no checkout to dirty and no remotely driven host mode."""
        for name in ("LOCAL_MODIFICATIONS", "UNKNOWN_CHECKOUT", "UNSUPPORTED_MODE"):
            self.assertFalse(hasattr(agent, name), name)
        self.assertEqual([f.name for f in fields(agent.Local)], ["current_release", "health"])

    def test_an_unhealthy_site_is_never_touched(self):
        """Recreating every container on an already-broken site makes the two causes indistinguishable."""
        for health, needle in [
            (agent.Health(False, None, None, None, ()), "unreachable"),
            (agent.Health(True, "degraded", "v1.2.0", True, ("task",)), "degraded"),
            (agent.Health(True, "ok", "v1.2.0", True, ("poller",)), "poller"),
            (agent.Health(True, "ok", "v1.2.0", False, ()), "AES key"),
        ]:
            with self.subTest(health=health):
                d = agent.decide(policy(), agent.Local("v1.2.0", health), NOW)
                self.assertEqual((d.action, d.reason), ("BLOCKED", agent.SITE_UNHEALTHY))
                self.assertIn(needle, d.detail or "")

    def test_being_unhealthy_does_not_nag_when_there_is_nothing_to_do(self):
        """A disabled or already-current trust must not report a blocker it was never going to act on."""
        sick = agent.Local("v1.3.0", agent.Health(False, None, None, None, ()))
        self.assertEqual(agent.decide(policy(), sick, NOW).action, "SKIP")
        self.assertEqual(agent.decide(policy(enabled=False), sick, NOW).action, "SKIP")


class Tick(unittest.TestCase):
    """The order of side effects: claim before touching anything, report whatever happened."""

    def setUp(self):
        patcher = mock.patch.object(agent, "cluster_kit", return_value=KIT)
        patcher.start()
        self.addCleanup(patcher.stop)
        drift = mock.patch.object(agent, "governance_drift", return_value=None)
        drift.start()
        self.addCleanup(drift.stop)

    def _run(self, claim_status=200, upgrade_result=(True, ""), pol=None, dry_run=False, verified=(True, "on v1.3.0")):
        calls = []

        def hub(kit, method, route, body=None):
            calls.append((method, route, body))
            if route == "/trust/upgrade-policy":
                return 200, pol or policy()
            return (claim_status, None) if body and body["state"] == "STARTING" else (200, None)

        with (
            mock.patch.object(agent, "hub_call", hub),
            mock.patch.object(agent, "cluster_state", return_value=CLEAN),
            mock.patch.object(agent, "verify", return_value=verified),
            mock.patch.object(agent, "cluster_upgrade", return_value=upgrade_result) as up,
        ):
            code = agent.tick(CLUSTER, dry_run)
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
        # The report says the agent stopped and where the site was, so the operator knows the way back.
        self.assertIn("boom", report["detail"])
        self.assertIn("nothing was rolled back", report["detail"])

    def test_a_refused_claim_upgrades_nothing(self):
        code, calls, up = self._run(claim_status=409)
        self.assertEqual(code, 0)
        up.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_an_upgrade_that_does_not_come_up_is_a_failure(self):
        """helm exiting 0 only says the release applied; SUCCEEDED requires the site to be up on it."""
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
            self.assertEqual(agent.tick(CLUSTER, False), 2)

    def test_a_crash_mid_upgrade_still_reports_failed(self):
        # The lease is held from STARTING; an exit the hub never hears about parks the release
        # until the TTL expires and then retries it blind.
        calls = []

        def hub(kit, method, route, body=None):
            calls.append((method, route, body))
            return 200, (policy() if route == "/trust/upgrade-policy" else None)

        with (
            mock.patch.object(agent, "hub_call", hub),
            mock.patch.object(agent, "cluster_state", return_value=CLEAN),
            mock.patch.object(agent, "cluster_upgrade", side_effect=RuntimeError("helm died")),
            self.assertRaises(RuntimeError),
        ):
            agent.tick(CLUSTER, False)
        self.assertEqual([c[2]["state"] for c in calls[1:]], ["STARTING", "FAILED"])
        self.assertIn("helm died", calls[-1][2]["detail"])

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
            mock.patch.object(agent, "cluster_state", return_value=CLEAN),
            mock.patch.object(agent, "cluster_upgrade", return_value=(False, "boom")),
            mock.patch.object(agent.time, "sleep"),
        ):
            self.assertEqual(agent.tick(CLUSTER, False), 1)
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


class StoppedNote(unittest.TestCase):
    """The failure report names the release the site was on and leaves the way back to an operator."""

    def test_it_names_the_previous_release(self):
        note = agent.stopped_note("v0.12.0", "v0.13.0")
        self.assertIn("nothing was rolled back", note)
        self.assertIn("on v0.12.0", note)
        self.assertIn("v0.13.0", note)

    def test_an_unknown_previous_release_is_said_so(self):
        for previous in (None, "v0.13.0"):
            self.assertIn("unknown release", agent.stopped_note(previous, "v0.13.0"))

    def test_the_agent_has_no_rollback_path(self):
        """A failure stops (#1644 epic): redeploying the old images past a migration is the case to avoid."""
        self.assertFalse(hasattr(agent, "rollback"))
        self.assertFalse(hasattr(agent, "ROLLBACK_VERIFY_TIMEOUT_S"))


class TickStops(unittest.TestCase):
    """A failed attempt is reported and left where it is: helm runs once, never for the old release."""

    def _tick(self, upgrade_result, verify_result=(True, "v0.13.0")):
        policy = {"enabled": True, "target_release": "v0.13.0", "lease": None}
        local = agent.Local("v0.12.0", agent.Health(True, "ok", "v0.12.0", True, ()))
        reports = []

        def fake_hub_call(kit, method, route, body=None):
            if body and body.get("state") in ("SUCCEEDED", "FAILED", "BLOCKED"):
                reports.append(body)
            return (200, policy if method == "GET" else None)

        with (
            mock.patch.object(agent, "cluster_kit", return_value=KIT),
            mock.patch.object(agent, "hub_call", fake_hub_call),
            mock.patch.object(agent, "cluster_state", return_value=local),
            mock.patch.object(agent, "cluster_upgrade", return_value=upgrade_result) as up,
            mock.patch.object(agent, "governance_drift", return_value=None),
            mock.patch.object(agent, "verify", return_value=verify_result),
            mock.patch.object(agent, "LeaseRenewal"),
        ):
            rc = agent.tick(CLUSTER, False)
        return rc, reports, up

    def test_a_failed_upgrade_stops_and_reports_failed(self):
        rc, reports, up = self._tick((False, "apply failed"))
        assert rc == 1
        assert [c.args[1] for c in up.call_args_list] == ["v0.13.0"]
        assert reports[-1]["state"] == "FAILED"
        assert "apply failed" in reports[-1]["detail"]
        assert "on v0.12.0" in reports[-1]["detail"]

    def test_a_site_that_does_not_come_up_is_left_where_it_is(self):
        rc, reports, up = self._tick((True, "applied"), verify_result=(False, "never came back"))
        assert rc == 1
        assert up.call_count == 1
        assert reports[-1]["state"] == "FAILED"
        assert "nothing was rolled back" in reports[-1]["detail"]

    def test_a_successful_upgrade_reports_succeeded(self):
        rc, reports, up = self._tick((True, "applied"), verify_result=(True, "v0.13.0"))
        assert rc == 0
        assert up.call_count == 1
        assert reports[-1]["state"] == "SUCCEEDED"


class TrustApiBase(unittest.TestCase):
    def test_the_service_url_loses_its_trailing_slash(self):
        self.assertEqual(agent.trust_api_base({"TRUST_API_URL": "http://t:8000/"}), "http://t:8000")

    def test_there_is_no_loopback_fallback(self):
        """A pod has its own loopback, where nothing listens; cluster_kit refuses an empty URL first."""
        with self.assertRaises(KeyError):
            agent.trust_api_base({"TRUST_API_PORT": "8020"})


class ClusterKit(unittest.TestCase):
    """The pod gets one key from a projected volume and the rest from its env — never a kit file."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "trust-api-key").write_text("k\n")
        self.cluster = agent.Cluster("flip-trust", "flip-trust", "oci://x/y", "GSTT", "prod", self.dir)
        self.env = {
            "CENTRAL_HUB_API_URL": "https://hub.example/api",
            "TRUST_API_KEY_HEADER": "X-Key",  # pragma: allowlist secret
            "TRUST_API_URL": "http://trust-api.flip-trust.svc:8000",
        }

    def test_reads_the_projected_key_and_the_rest_from_the_env(self):
        with mock.patch.dict("os.environ", self.env, clear=False):
            kit = agent.cluster_kit(self.cluster)
        self.assertEqual(kit["TRUST_API_KEY"], "k")
        self.assertEqual(kit["CENTRAL_HUB_API_URL"], "https://hub.example/api")
        self.assertEqual(kit["TRUST_API_KEY_HEADER"], "X-Key")

    def test_health_is_read_from_the_trust_api_service_not_the_pods_loopback(self):
        """FLIP#1644, found on kind: from loopback every tick reported a healthy site as SITE_UNHEALTHY."""
        with mock.patch.dict("os.environ", self.env, clear=False):
            kit = agent.cluster_kit(self.cluster)
        self.assertEqual(agent.trust_api_base(kit), "http://trust-api.flip-trust.svc:8000")

    def test_an_empty_trust_api_url_fails_rather_than_reading_loopback(self):
        with mock.patch.dict("os.environ", {**self.env, "TRUST_API_URL": ""}, clear=False):
            with self.assertRaises(ValueError):
                agent.cluster_kit(self.cluster)

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
        env = {"CENTRAL_HUB_API_URL": "https://h/api", "TRUST_API_URL": "http://t:8000"}
        with mock.patch.dict("os.environ", env, clear=False):
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
        # The new chart's defaults with the stored overrides on top; plain --reuse-values would
        # render with the previous chart's defaults and drop any value the release adds.
        self.assertIn("--reset-then-reuse-values", upgrade)
        self.assertNotIn("--reuse-values", upgrade)
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
    """`tick` runs the cluster upgrade; there is no other backend."""

    CLUSTER = agent.Cluster("flip-trust", "flip-trust", "oci://x/y", "GSTT", "prod")

    def test_the_agent_has_no_host_path(self):
        """The Compose host installer was dropped: no checkout, no `make`, no kit file."""
        for name in ("upgrade", "checkout", "local_state", "kit_identity", "read_kit", "DEFAULT_CHECKOUT"):
            self.assertFalse(hasattr(agent, name), name)

    def test_the_compose_host_installer_is_gone(self):
        """No installer, make target or systemd unit for a Compose host: auto-upgrade is Kubernetes-only."""
        repo = SCRIPTS_DIR.parent
        self.assertFalse((SCRIPTS_DIR / "setup_auto_upgrade.py").exists())
        for makefile in (repo / "Makefile", repo / "trust" / "Makefile"):
            text = makefile.read_text()
            self.assertNotIn("setup-auto-upgrade", text, makefile)
            self.assertNotIn("flip-trust-upgrade.timer", text, makefile)

    def test_the_cluster_upgrade_is_what_runs(self):
        policy = {"enabled": True, "target_release": "v0.13.0", "lease": None}
        local = agent.Local("v0.12.0", agent.Health(True, "ok", "v0.12.0", True, ()))
        with (
            mock.patch.object(agent, "cluster_kit", return_value={}),
            mock.patch.object(agent, "hub_call", side_effect=[(200, policy), (200, None), (200, None)]),
            mock.patch.object(agent, "cluster_state", return_value=local),
            mock.patch.object(agent, "cluster_upgrade", return_value=(True, "fine")) as upgrade,
            mock.patch.object(agent, "verify", return_value=(True, "v0.13.0")),
            mock.patch.object(agent, "governance_drift", return_value=None),
            mock.patch.object(agent, "LeaseRenewal"),
        ):
            rc = agent.tick(self.CLUSTER, False)
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
        local = agent.Local("v0.12.0", agent.Health(True, "ok", "v0.12.0", True, ()))
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
            mock.patch.object(agent, "cluster_upgrade", return_value=(True, "applied")) as up,
            mock.patch.object(agent, "verify", return_value=(True, "up")),
            mock.patch.object(agent, "governance_drift", return_value=drift),
            mock.patch.object(agent, "LeaseRenewal"),
        ):
            rc = agent.tick(cluster, False)
        return rc, reports, up

    def test_drift_stops_the_success_report(self):
        rc, reports, _ = self._tick("the governance document in the cluster does not match ...")
        self.assertEqual(rc, 1)
        self.assertEqual(reports[-1]["state"], "FAILED")
        self.assertIn("governance document", reports[-1]["detail"])

    def test_drift_does_not_move_the_release(self):
        """The document is not changed by the upgrade, so moving the release would not fix it."""
        _, _, up = self._tick("drift")
        assert up.call_count == 1

    def test_no_drift_reports_success(self):
        rc, reports, _ = self._tick(None)
        self.assertEqual(rc, 0)
        self.assertEqual(reports[-1]["state"], "SUCCEEDED")


CLUSTER_ARGS = [
    "tick",
    "--namespace",
    "ns",
    "--release",
    "flip-trust",
    "--chart-repository",
    "oci://x/y",
    "--kit-name",
    "GSTT",
    "--hub",
    "prod",
]


class Budget(unittest.TestCase):
    """Finish and report before the Job deadline kills the pod (#1644 review)."""

    def tearDown(self):
        agent.set_budget(None)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def test_no_budget_means_none_left_to_count(self):
        agent.set_budget(None)
        self.assertIsNone(agent.remaining_budget())

    def test_the_budget_keeps_a_reserve_for_the_report(self):
        agent.set_budget(1000, now=lambda: 0.0)
        self.assertEqual(agent.remaining_budget(now=lambda: 100.0), 1000 - 100 - agent.REPORT_RESERVE_S)
        self.assertEqual(agent.remaining_budget(now=lambda: 5000.0), 0.0)

    def test_a_command_is_stopped_when_the_budget_runs_out(self):
        agent.set_budget(1000)
        with mock.patch.object(agent.subprocess, "run", side_effect=subprocess.TimeoutExpired(["helm"], 1)) as sp:
            out = agent.run(["helm", "upgrade"], Path("."))
        self.assertEqual(out.returncode, 124)
        self.assertIn("time budget", out.stderr)
        self.assertGreater(sp.call_args.kwargs["timeout"], 0)

    def test_without_a_budget_commands_get_no_timeout(self):
        agent.set_budget(None)
        done = subprocess.CompletedProcess(["helm"], 0, "", "")
        with mock.patch.object(agent.subprocess, "run", return_value=done) as sp:
            agent.run(["helm", "version"], Path("."))
        self.assertNotIn("timeout", sp.call_args.kwargs)

    def test_the_health_wait_is_capped_by_the_budget(self):
        agent.set_budget(agent.REPORT_RESERVE_S + 30)
        unhealthy = agent.Health(reachable=False, status=None, version=None, hub_key_match=None, dead_tasks=())
        clock = iter(range(0, 10_000, 10))
        with mock.patch.object(agent, "read_health", return_value=unhealthy):
            ok, why = agent.verify({}, "v1.3.0", sleep=lambda _s: None, now=lambda: next(clock))
        self.assertFalse(ok)
        self.assertRegex(why, r"within (29|30)s")

    def test_sigterm_becomes_systemexit_so_the_report_still_goes_out(self):
        with self.assertRaises(SystemExit):
            agent._terminated(signal.SIGTERM, None)

    def test_a_budget_smaller_than_the_reserve_is_refused(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            agent.main([*CLUSTER_ARGS, "--budget-seconds", str(agent.REPORT_RESERVE_S)])

    def test_the_cluster_arguments_are_required(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            agent.main(["tick", "--namespace", "ns"])

    def test_the_host_arguments_are_gone(self):
        for flag in ("--kit-file", "--checkout", "--mode"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                agent.main([*CLUSTER_ARGS, flag, "x"])

    def test_main_builds_the_cluster_from_its_arguments(self):
        with mock.patch.object(agent, "tick", return_value=0) as tick:
            self.assertEqual(agent.main([*CLUSTER_ARGS, "--dry-run"]), 0)
        tick.assert_called_once_with(agent.Cluster("ns", "flip-trust", "oci://x/y", "GSTT", "prod"), True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
