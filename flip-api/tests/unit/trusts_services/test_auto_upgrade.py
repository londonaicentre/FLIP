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

"""Unit tests for the automatic-upgrade rules: window, target, status and the lease claim."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from flip_api.db.models.main_models import Trust
from flip_api.domain.schemas.trusts import (
    AutoUpgradeLastAttempt,
    AutoUpgradeSettings,
    AutoUpgradeView,
    UpgradeReportState,
    UpgradeStatusInput,
    UpgradeViewState,
)
from flip_api.trusts_services.services import auto_upgrade as svc

SVC = "flip_api.trusts_services.services.auto_upgrade"


@pytest.fixture(autouse=True)
def _emails_never_send(monkeypatch):
    """Email is tested on its own (test_upgrade_notifications); here it only has to not run.

    The real path reaches SES, so a suite that let it through was making a network call on every
    STARTING/FAILED test and only passing because the notifier swallows its own failures.
    """
    monkeypatch.setattr(f"{SVC}.notify_upgrade_started", lambda *a, **k: None)
    monkeypatch.setattr(f"{SVC}.notify_upgrade_finished", lambda *a, **k: None)


def _trust(**kw) -> Trust:
    base = {
        "id": uuid.uuid4(),
        "name": "GSTT",
        "services_health": {"trust-api": {"status": "healthy", "version": "v1.2.0", "response_ms": None}},
        "upgrade_agent_seen_at": datetime.utcnow(),
    }
    return Trust(**{**base, **kw})


class TestInWindow:
    def test_no_window_is_always_open(self):
        assert svc.in_window(datetime(2026, 10, 23, 12, 0), None, None, None)

    def test_daytime_window(self):
        # 03:30 UTC in October is 04:30 BST.
        now = datetime(2026, 10, 23, 3, 30)
        assert svc.in_window(now, "01:00", "05:00", "Europe/London")
        assert not svc.in_window(now, "01:00", "04:00", "Europe/London")

    def test_end_is_exclusive(self):
        assert svc.in_window(datetime(2026, 10, 23, 4, 59), "01:00", "05:00", "UTC")
        assert not svc.in_window(datetime(2026, 10, 23, 5, 0), "01:00", "05:00", "UTC")

    @pytest.mark.parametrize(("hour", "expected"), [(23, True), (2, True), (12, False), (4, False)])
    def test_overnight_window(self, hour: int, expected: bool):
        assert svc.in_window(datetime(2026, 10, 23, hour, 0), "22:00", "04:00", "UTC") is expected

    def test_follows_daylight_saving(self):
        # Clocks go back at 01:00 UTC on 25 Oct 2026: 00:30 UTC is 01:30 BST on the 24th, but 00:30 GMT on the 26th.
        assert svc.in_window(datetime(2026, 10, 24, 0, 30), "01:00", "05:00", "Europe/London")
        assert not svc.in_window(datetime(2026, 10, 26, 0, 30), "01:00", "05:00", "Europe/London")


class TestTarget:
    @pytest.mark.parametrize(
        ("identity", "expected"),
        [("v1.3.0", "v1.3.0"), ("v1.3.0-rc.1", "v1.3.0-rc.1"), ("sha-abc1234", None), (None, None)],
    )
    def test_only_a_release_tag_is_a_target(self, identity, expected):
        with patch(f"{SVC}.build_identity", return_value=identity):
            assert svc.target_release() == expected

    def test_current_release_reads_trust_api_version(self):
        assert svc.current_release(_trust()) == "v1.2.0"
        assert svc.current_release(_trust(services_health=None)) is None
        assert svc.current_release(_trust(services_health={"trust-api": {"version": "sha-abc1234"}})) is None


class TestSettingsValidation:
    def test_window_needs_both_ends_and_a_zone(self):
        with pytest.raises(ValidationError):
            AutoUpgradeSettings(enabled=True, windowStart="01:00")
        with pytest.raises(ValidationError):
            AutoUpgradeSettings(enabled=True, windowStart="01:00", windowEnd="05:00")

    def test_rejects_bad_times_zone_and_empty_window(self):
        with pytest.raises(ValidationError):
            AutoUpgradeSettings(enabled=True, windowStart="25:00", windowEnd="05:00", timezone="UTC")
        with pytest.raises(ValidationError):
            AutoUpgradeSettings(enabled=True, windowStart="01:00", windowEnd="05:00", timezone="Mars/Base")
        with pytest.raises(ValidationError):
            AutoUpgradeSettings(enabled=True, windowStart="01:00", windowEnd="01:00", timezone="UTC")

    def test_no_window_is_valid(self):
        assert AutoUpgradeSettings(enabled=False).window_start is None


class TestView:
    def _view(self, trust: Trust, busy: bool = False):
        with patch(f"{SVC}.target_release", return_value="v1.3.0"), patch(f"{SVC}.is_busy", return_value=busy):
            return svc.view_for_admin(trust, MagicMock())

    def test_pending_with_a_running_job(self):
        v = self._view(_trust(auto_upgrade_enabled=True, upgrade_pending_since=datetime(2026, 10, 20)), busy=True)
        assert (v.state, v.blockers) == ("PENDING", ["JOBS_RUNNING"])
        assert v.pending_since is not None

    def test_switch_off_is_pending_without_blockers(self):
        v = self._view(_trust(auto_upgrade_enabled=False))
        assert (v.state, v.blockers) == ("PENDING", [])

    def test_agent_offline_is_a_blocker_when_stale(self):
        v = self._view(_trust(auto_upgrade_enabled=True, upgrade_agent_seen_at=datetime.utcnow() - timedelta(hours=9)))
        assert "AGENT_OFFLINE" in v.blockers

    @pytest.mark.parametrize("code", ["LOCAL_MODIFICATIONS", "UNSUPPORTED_MODE", "UNKNOWN_CHECKOUT", "SITE_UNHEALTHY"])
    def test_every_reportable_blocker_reaches_the_admin(self, code: str):
        """Storing a blocker the view never reads would leave the admin with a silent PENDING."""
        v = self._view(
            _trust(
                auto_upgrade_enabled=True,
                upgrade_blocker=code,
                upgrade_blocker_detail="trust-api reported dead_tasks=3",
                upgrade_blocker_at=datetime.utcnow(),
            )
        )
        assert code in v.blockers
        assert v.blocker_detail == "trust-api reported dead_tasks=3"

    def test_a_stale_blocker_is_dropped_with_its_detail(self):
        v = self._view(
            _trust(
                auto_upgrade_enabled=True,
                upgrade_blocker="SITE_UNHEALTHY",
                upgrade_blocker_detail="trust-api reported dead_tasks=3",
                upgrade_blocker_at=datetime.utcnow() - timedelta(hours=9),
            )
        )
        assert "SITE_UNHEALTHY" not in v.blockers
        assert v.blocker_detail is None

    def test_lease_means_in_progress(self):
        v = self._view(
            _trust(auto_upgrade_enabled=True, upgrade_lease_expires_at=datetime.utcnow() + timedelta(hours=1))
        )
        assert v.state == "IN_PROGRESS"

    def test_expired_lease_does_not(self):
        v = self._view(
            _trust(auto_upgrade_enabled=True, upgrade_lease_expires_at=datetime.utcnow() - timedelta(minutes=1))
        )
        assert v.state == "PENDING"

    def test_failure_wins_and_carries_the_log(self):
        v = self._view(
            _trust(
                auto_upgrade_enabled=True,
                last_upgrade_state="FAILED",
                last_upgrade_release="v1.3.0",
                last_upgrade_at=datetime(2026, 10, 21, 2, 10),
                last_upgrade_detail="xnat-web unhealthy",
            )
        )
        assert v.state == "FAILED"
        assert v.last_attempt is not None
        assert v.last_attempt.detail == "xnat-web unhealthy"

    def test_caught_up_after_success(self):
        t = _trust(
            services_health={"trust-api": {"version": "v1.3.0"}},
            last_upgrade_state="SUCCEEDED",
            last_upgrade_release="v1.3.0",
            last_upgrade_at=datetime(2026, 10, 22, 2, 41),
        )
        assert self._view(t).state == "SUCCEEDED"


class TestClaim:
    def _claim(self, trust: Trust, release: str = "v1.3.0", busy: bool = False):
        db = MagicMock()
        db.exec.return_value.one.return_value = trust
        with patch(f"{SVC}.is_busy", return_value=busy):
            return svc.record_status(
                trust.id, UpgradeStatusInput(state=UpgradeReportState.STARTING, release=release), db
            ), db

    def test_grants_a_two_hour_lease(self):
        t = _trust(auto_upgrade_enabled=True)
        lease, db = self._claim(t)
        assert lease is not None
        assert lease.release == "v1.3.0"
        assert timedelta(hours=1, minutes=59) < t.upgrade_lease_expires_at - datetime.utcnow() <= timedelta(hours=2)
        db.commit.assert_called_once()

    @pytest.mark.parametrize(
        "trust_kw",
        [
            {"auto_upgrade_enabled": False},
            {"auto_upgrade_enabled": True, "last_upgrade_state": "FAILED", "last_upgrade_release": "v1.3.0"},
            {
                "auto_upgrade_enabled": True,
                "upgrade_lease_release": "v1.2.5",
                "upgrade_lease_expires_at": datetime.utcnow() + timedelta(hours=1),
            },
        ],
    )
    def test_refusals(self, trust_kw):
        with pytest.raises(HTTPException) as exc:
            self._claim(_trust(**trust_kw))
        assert exc.value.status_code == 409

    def test_refused_when_a_job_landed_in_between(self):
        with pytest.raises(HTTPException) as exc:
            self._claim(_trust(auto_upgrade_enabled=True), busy=True)
        assert exc.value.status_code == 409

    def test_a_failed_release_may_be_retried_once_cleared(self):
        t = _trust(auto_upgrade_enabled=True, last_upgrade_state="FAILED", last_upgrade_release="v1.3.0")
        svc.clear_failure(t, MagicMock())
        lease, _ = self._claim(t)
        assert lease is not None

    def test_clearing_with_nothing_failed_is_a_conflict(self):
        with pytest.raises(HTTPException) as exc:
            svc.clear_failure(_trust(), MagicMock())
        assert exc.value.status_code == 409

    def test_success_releases_the_lease_and_records_the_attempt(self):
        t = _trust(
            auto_upgrade_enabled=True,
            upgrade_lease_release="v1.3.0",
            upgrade_lease_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(t.id, UpgradeStatusInput(state=UpgradeReportState.SUCCEEDED, release="v1.3.0"), db)
        assert t.upgrade_lease_expires_at is None
        assert (t.last_upgrade_state, t.last_upgrade_release) == ("SUCCEEDED", "v1.3.0")

    def test_failure_keeps_the_log_tail_and_blocks_a_retry(self):
        t = _trust(auto_upgrade_enabled=True)
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(
            t.id, UpgradeStatusInput(state=UpgradeReportState.FAILED, release="v1.3.0", detail="boom"), db
        )
        assert t.last_upgrade_detail == "boom"
        assert svc.failed_release(t) == "v1.3.0"

    def test_only_known_blockers_are_stored(self):
        t = _trust()
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(
            t.id,
            UpgradeStatusInput(state=UpgradeReportState.BLOCKED, release="v1.3.0", blocker="LOCAL_MODIFICATIONS"),
            db,
        )
        assert t.upgrade_blocker == "LOCAL_MODIFICATIONS"
        svc.record_status(
            t.id, UpgradeStatusInput(state=UpgradeReportState.BLOCKED, release="v1.3.0", blocker="<script>"), db
        )
        assert t.upgrade_blocker is None

    def test_a_blocker_is_read_from_its_own_field_not_the_prose(self):
        """The agent sends a human-readable `detail` alongside the code; matching on the prose
        silently loses the blocker and empties the portal's card."""
        t = _trust()
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(
            t.id,
            UpgradeStatusInput(
                state="BLOCKED",
                release="v1.3.0",
                blocker="SITE_UNHEALTHY",
                detail="trust-api reported dead_tasks=3",
            ),
            db,
        )
        assert t.upgrade_blocker == "SITE_UNHEALTHY"
        assert t.upgrade_blocker_detail == "trust-api reported dead_tasks=3"

    def test_prose_in_detail_is_never_read_as_a_code(self):
        """`detail` is free text an admin reads; only `blocker` may set the machine-readable code."""
        t = _trust()
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(
            t.id, UpgradeStatusInput(state=UpgradeReportState.BLOCKED, release="v1.3.0", detail="UNSUPPORTED_MODE"), db
        )
        assert t.upgrade_blocker is None
        assert t.upgrade_blocker_detail == "UNSUPPORTED_MODE"

    def test_a_stale_terminal_report_does_not_free_another_releases_lease(self):
        """A late FAILED from the agent that lost its lease must not unlock the upgrade now running."""
        t = _trust(
            auto_upgrade_enabled=True,
            upgrade_lease_release="v1.4.0",
            upgrade_lease_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(t.id, UpgradeStatusInput(state=UpgradeReportState.FAILED, release="v1.3.0"), db)
        assert t.upgrade_lease_release == "v1.4.0"
        assert t.upgrade_lease_expires_at is not None
        # The attempt is still recorded: it is the truth about v1.3.0.
        assert (t.last_upgrade_release, t.last_upgrade_state) == ("v1.3.0", "FAILED")

    def test_its_own_terminal_report_clears_its_lease(self):
        t = _trust(
            auto_upgrade_enabled=True,
            upgrade_lease_release="v1.3.0",
            upgrade_lease_expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(t.id, UpgradeStatusInput(state=UpgradeReportState.SUCCEEDED, release="v1.3.0"), db)
        assert (t.upgrade_lease_release, t.upgrade_lease_expires_at) == (None, None)


class TestRoutes:
    """The agent routes authenticate by trust key; the portal routes by user and trust-scoped permission."""

    def _client(self, trust: Trust, db: MagicMock):
        from fastapi.testclient import TestClient

        from flip_api.auth.access_manager import authenticate_trust
        from flip_api.auth.dependencies import verify_token
        from flip_api.db.database import get_session
        from flip_api.main import app

        app.dependency_overrides[authenticate_trust] = lambda: trust
        app.dependency_overrides[get_session] = lambda: db
        app.dependency_overrides[verify_token] = lambda: uuid.uuid4()
        return TestClient(app), app

    def test_trust_key_can_set_the_switch_only(self):
        t = _trust(auto_upgrade_enabled=False, auto_upgrade_window_start="01:00", auto_upgrade_window_end="05:00")
        client, app = self._client(t, MagicMock())
        try:
            with patch(f"{SVC}.is_busy", return_value=False):
                r = client.put("/api/trust/upgrade-policy", json={"enabled": True})
        finally:
            app.dependency_overrides.clear()
        assert r.status_code == 200
        assert t.auto_upgrade_enabled is True
        assert t.auto_upgrade_window_start == "01:00"

    def test_admin_routes_refuse_a_user_without_the_permission(self):
        t = _trust()
        db = MagicMock()
        db.get.return_value = t
        client, app = self._client(t, db)
        try:
            with (
                patch("flip_api.trusts_services.trust_auto_upgrade.has_trust_permissions", return_value=False),
                patch("flip_api.trusts_services.trust_auto_upgrade.has_permissions", return_value=False),
            ):
                get = client.get(f"/api/trust/{t.id}/auto-upgrade")
                put = client.put(f"/api/trust/{t.id}/auto-upgrade", json={"enabled": True})
        finally:
            app.dependency_overrides.clear()
        assert (get.status_code, put.status_code) == (403, 403)

    def test_admin_get_uses_the_camel_case_the_ui_reads(self):
        t = _trust(auto_upgrade_enabled=True)
        db = MagicMock()
        db.get.return_value = t
        client, app = self._client(t, db)
        try:
            with (
                patch("flip_api.trusts_services.trust_auto_upgrade.has_trust_permissions", return_value=True),
                patch(f"{SVC}.target_release", return_value="v1.3.0"),
                patch(f"{SVC}.is_busy", return_value=True),
            ):
                r = client.get(f"/api/trust/{t.id}/auto-upgrade")
        finally:
            app.dependency_overrides.clear()
        body = r.json()
        assert body["targetRelease"] == "v1.3.0"
        assert body["currentRelease"] == "v1.2.0"
        assert body["blockers"] == ["JOBS_RUNNING"]


class TestFleet:
    """The Connection page's upgrade column: one row per trust, and admins only."""

    def _client(self, db: MagicMock):
        from fastapi.testclient import TestClient

        from flip_api.auth.dependencies import verify_token
        from flip_api.db.database import get_session
        from flip_api.main import app

        app.dependency_overrides[get_session] = lambda: db
        app.dependency_overrides[verify_token] = lambda: uuid.uuid4()
        return TestClient(app), app

    def _get(self, db: MagicMock, *, admin: bool):
        client, app = self._client(db)
        route = "flip_api.trusts_services.trust_auto_upgrade.has_permissions"
        try:
            with patch(route, return_value=admin):
                return client.get("/api/trusts/auto-upgrade")
        finally:
            app.dependency_overrides.clear()

    def test_an_admin_gets_a_row_for_every_trust(self):
        oncs = [_trust(name="GSTT"), _trust(name="KCH"), _trust(name="UCLH")]
        db = MagicMock()
        db.exec.return_value.all.return_value = oncs
        view = AutoUpgradeView(enabled=True, state=UpgradeViewState.PENDING, current_release="v0.12.0")
        with patch(f"{SVC}.view_for_admin", return_value=view):
            r = self._get(db, admin=True)
        assert r.status_code == 200
        assert [row["name"] for row in r.json()] == ["GSTT", "KCH", "UCLH"]

    def test_the_row_carries_the_same_view_the_trust_card_gets(self):
        """One definition of what "failed" means: the column is not a second projection of the row."""
        trust = _trust(name="GSTT", code="GSTT")
        db = MagicMock()
        db.exec.return_value.all.return_value = [trust]
        view = AutoUpgradeView(
            enabled=True,
            state=UpgradeViewState.FAILED,
            last_attempt=AutoUpgradeLastAttempt(state="FAILED", release="v0.13.0", at="x"),
        )
        with patch(f"{SVC}.view_for_admin", return_value=view):
            r = self._get(db, admin=True)
        row = r.json()[0]
        assert (row["trust_id"], row["code"]) == (str(trust.id), "GSTT")
        assert row["view"]["state"] == "FAILED"
        assert row["view"]["lastAttempt"]["release"] == "v0.13.0"

    def test_a_trust_admin_is_refused_the_fleet(self):
        r = self._get(MagicMock(), admin=False)
        assert r.status_code == 403

    def test_the_fleet_is_one_query_not_one_per_trust(self):
        db = MagicMock()
        db.exec.return_value.all.return_value = []
        with patch(f"{SVC}.view_for_admin"):
            self._get(db, admin=True)
        assert db.exec.call_count == 1


class TestAgentTick:
    """`note_agent_tick` is what makes a silent agent visible, and what dates the Waiting card.

    Both facts are read by the view (`AGENT_OFFLINE`, `pendingSince`), so a function that never
    stamped them would leave an agent that stopped an hour after install looking healthy forever.
    """

    def test_it_stamps_the_tick(self):
        t = _trust(upgrade_agent_seen_at=None)
        svc.note_agent_tick(t, MagicMock())
        assert t.upgrade_agent_seen_at is not None

    def test_the_first_tick_behind_the_hub_dates_the_wait(self):
        t = _trust(services_health={"trust-api": {"version": "v1.2.0"}})
        with patch(f"{SVC}.target_release", return_value="v1.3.0"):
            svc.note_agent_tick(t, MagicMock())
        assert t.upgrade_pending_since is not None

    def test_a_later_tick_does_not_redate_the_wait(self):
        """The card says "waiting since" — re-dating it hourly would make a week-old gap look new."""
        first = datetime.utcnow() - timedelta(days=2)
        t = _trust(services_health={"trust-api": {"version": "v1.2.0"}}, upgrade_pending_since=first)
        with patch(f"{SVC}.target_release", return_value="v1.3.0"):
            svc.note_agent_tick(t, MagicMock())
        assert t.upgrade_pending_since == first

    def test_catching_up_clears_the_wait(self):
        t = _trust(
            services_health={"trust-api": {"version": "v1.3.0"}},
            upgrade_pending_since=datetime.utcnow() - timedelta(days=2),
        )
        with patch(f"{SVC}.target_release", return_value="v1.3.0"):
            svc.note_agent_tick(t, MagicMock())
        assert t.upgrade_pending_since is None


class TestApplySettings:
    def test_the_window_and_zone_round_trip(self):
        """A dropped zone would silently turn a window into "always open" (in_window needs all three)."""
        t = _trust()
        settings = AutoUpgradeSettings(enabled=True, windowStart="01:00", windowEnd="05:00", timezone="Europe/London")
        svc.apply_settings(t, settings, MagicMock())
        assert t.auto_upgrade_enabled is True
        assert (t.auto_upgrade_window_start, t.auto_upgrade_window_end) == ("01:00", "05:00")
        assert t.auto_upgrade_timezone == "Europe/London"

    def test_clearing_the_window_stores_none(self):
        t = _trust(auto_upgrade_window_start="01:00", auto_upgrade_window_end="02:00", auto_upgrade_timezone="UTC")
        svc.apply_settings(t, AutoUpgradeSettings(enabled=True), MagicMock())
        assert (t.auto_upgrade_window_start, t.auto_upgrade_window_end, t.auto_upgrade_timezone) == (None, None, None)


class TestWindowStart:
    def test_the_start_is_inclusive(self):
        """The end is exclusive; the start has to be inclusive or the first minute of the window is unreachable."""
        assert svc.in_window(datetime(2026, 10, 23, 1, 0), "01:00", "05:00", "UTC")

    def test_the_minute_before_the_start_is_outside(self):
        assert not svc.in_window(datetime(2026, 10, 23, 0, 59), "01:00", "05:00", "UTC")


class TestRenewal:
    def test_the_same_release_renews_its_own_lease(self):
        """A long upgrade needs to re-post STARTING; the hub must let the holder extend, not refuse it."""
        t = _trust(
            auto_upgrade_enabled=True,
            upgrade_lease_release="v1.3.0",
            upgrade_lease_expires_at=datetime.utcnow() + timedelta(minutes=2),
        )
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        with patch(f"{SVC}.is_busy", return_value=False):
            lease = svc.record_status(t.id, UpgradeStatusInput(state=UpgradeReportState.STARTING, release="v1.3.0"), db)
        assert lease is not None
        assert t.upgrade_lease_expires_at > datetime.utcnow() + timedelta(hours=1)
        db.commit.assert_called_once()


class TestManualMove:
    def test_a_manual_move_is_not_reported_as_the_agents_success(self):
        """An operator upgrading by hand leaves the agent's last SUCCEEDED at the old release."""
        t = _trust(
            services_health={"trust-api": {"version": "v1.3.0"}},
            last_upgrade_state="SUCCEEDED",
            last_upgrade_release="v1.2.0",
            last_upgrade_at=datetime(2026, 10, 22, 2, 41),
        )
        # The trust now runs v1.3.0, which is the hub's release, but the agent's last success was
        # v1.2.0 — an operator did this one. Reporting SUCCEEDED would credit the agent.
        with patch(f"{SVC}.target_release", return_value="v1.3.0"), patch(f"{SVC}.is_busy", return_value=False):
            assert svc.view_for_admin(t, MagicMock()).state == "UP_TO_DATE"


class TestAgentRoutesThroughTheApp:
    """The two routes the host agent uses, driven through the app rather than called as functions.

    They were only ever exercised as service calls, so nothing checked the wire shape the agent
    actually parses (snake_case, the lease object, which HTTP status a refusal gives).
    """

    def _client(self, trust: Trust, db: MagicMock):
        from fastapi.testclient import TestClient

        from flip_api.auth.access_manager import authenticate_trust
        from flip_api.db.database import get_session
        from flip_api.main import app

        app.dependency_overrides[authenticate_trust] = lambda: trust
        app.dependency_overrides[get_session] = lambda: db
        return TestClient(app), app

    def test_the_policy_is_machine_shaped_snake_case(self):
        t = _trust(auto_upgrade_enabled=True)
        db = MagicMock()
        client, app = self._client(t, db)
        try:
            with patch(f"{SVC}.target_release", return_value="v1.3.0"), patch(f"{SVC}.is_busy", return_value=False):
                r = client.get("/api/trust/upgrade-policy")
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["target_release"] == "v1.3.0"
        assert body["lease"] is None
        assert body["failed_release"] is None
        # The agent reads these keys by name; a camel-case alias would silently send nothing.
        assert set(body) >= {"enabled", "target_release", "maintenance_window", "timezone", "is_busy", "lease"}

    def test_a_starting_report_returns_the_lease_the_agent_keeps(self):
        t = _trust(auto_upgrade_enabled=True)
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        client, app = self._client(t, db)
        try:
            with (
                patch(f"{SVC}.is_busy", return_value=False),
                patch(f"{SVC}.notify_upgrade_started") as notify,
            ):
                r = client.post("/api/trust/upgrade-status", json={"state": "STARTING", "release": "v1.3.0"})
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 200
        assert r.json()["release"] == "v1.3.0"
        assert r.json()["expires_at"]
        notify.assert_called_once()

    def test_a_refused_claim_is_409_not_500(self):
        t = _trust(auto_upgrade_enabled=False)
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        client, app = self._client(t, db)
        try:
            r = client.post("/api/trust/upgrade-status", json={"state": "STARTING", "release": "v1.3.0"})
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 409

    def test_an_unknown_state_is_rejected_by_the_schema(self):
        """The enum is the wire contract: a typo must fail here, not be stored as a status."""
        t = _trust(auto_upgrade_enabled=True)
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        client, app = self._client(t, db)
        try:
            r = client.post("/api/trust/upgrade-status", json={"state": "UPGRADING", "release": "v1.3.0"})
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 422
