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
from flip_api.domain.schemas.trusts import AutoUpgradeSettings, UpgradeStatusInput
from flip_api.trusts_services.services import auto_upgrade as svc

SVC = "flip_api.trusts_services.services.auto_upgrade"


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
            return svc.record_status(trust.id, UpgradeStatusInput(state="STARTING", release=release), db), db

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
        svc.record_status(t.id, UpgradeStatusInput(state="SUCCEEDED", release="v1.3.0"), db)
        assert t.upgrade_lease_expires_at is None
        assert (t.last_upgrade_state, t.last_upgrade_release) == ("SUCCEEDED", "v1.3.0")

    def test_failure_keeps_the_log_tail_and_blocks_a_retry(self):
        t = _trust(auto_upgrade_enabled=True)
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(t.id, UpgradeStatusInput(state="FAILED", release="v1.3.0", detail="boom"), db)
        assert t.last_upgrade_detail == "boom"
        assert svc.failed_release(t) == "v1.3.0"

    def test_only_known_blockers_are_stored(self):
        t = _trust()
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(
            t.id, UpgradeStatusInput(state="BLOCKED", release="v1.3.0", blocker="LOCAL_MODIFICATIONS"), db
        )
        assert t.upgrade_blocker == "LOCAL_MODIFICATIONS"
        svc.record_status(t.id, UpgradeStatusInput(state="BLOCKED", release="v1.3.0", blocker="<script>"), db)
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

    def test_an_older_agent_sending_the_code_as_detail_still_works(self):
        """Agents upgrade on their own schedule, so the pre-`blocker` payload must still land."""
        t = _trust()
        db = MagicMock()
        db.exec.return_value.one.return_value = t
        svc.record_status(t.id, UpgradeStatusInput(state="BLOCKED", release="v1.3.0", detail="UNSUPPORTED_MODE"), db)
        assert t.upgrade_blocker == "UNSUPPORTED_MODE"


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
