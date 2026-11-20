# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Upgrade notification emails (FLIP#1640).

The load-bearing property under test is the *negative* one: nothing in here may
raise, because every caller is mid-way through recording an upgrade state the
operator depends on.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from flip_api.private_services.upgrade_notifications import (
    _trim,
    notify_upgrade_finished,
    notify_upgrade_started,
)

TRUST_ID = uuid4()
ADMIN_EMAIL = "platform-admin@example.com"

MODULE = "flip_api.private_services.upgrade_notifications"


def _trust():
    trust = MagicMock()
    trust.id = TRUST_ID
    trust.name = "Trust_1"
    trust.code = "T1"
    return trust


def _user(email, disabled=False):
    user = MagicMock()
    user.id = uuid4()
    user.email = email
    user.is_disabled = disabled
    return user


@pytest.fixture
def send():
    with patch(f"{MODULE}.send_templated_email") as mock:
        yield mock


@pytest.fixture
def settings():
    with patch(f"{MODULE}.get_settings") as mock:
        mock.return_value.AWS_SES_ADMIN_EMAIL_ADDRESS = ADMIN_EMAIL
        yield mock


@pytest.fixture
def identity():
    """The identity provider seam: admin addresses live there, not in a local table."""
    with patch(f"{MODULE}.build_identity_provider") as mock:
        mock.return_value.list_users.return_value = []
        yield mock


def _db(admin_ids):
    """A Session whose single exec() returns this trust's Trust Admin user ids."""
    db = MagicMock()
    db.exec.return_value.all.return_value = admin_ids
    return db


def _recipients_of(send):
    return [call.kwargs["recipient"] for call in send.call_args_list]


class TestRecipients:
    def test_trust_admins_and_the_platform_admin_are_both_told(self, send, settings, identity):
        alice, bob = _user("alice@example.com"), _user("bob@example.com")
        identity.return_value.list_users.return_value = [alice, bob]

        notify_upgrade_started(_trust(), "v1.3.0", _db([alice.id, bob.id]))

        assert _recipients_of(send) == ["alice@example.com", "bob@example.com", ADMIN_EMAIL]

    def test_a_disabled_admin_is_dropped(self, send, settings, identity):
        """A revoked admin should stop receiving a trust's operational mail."""
        alice, gone = _user("alice@example.com"), _user("gone@example.com", disabled=True)
        identity.return_value.list_users.return_value = [alice, gone]

        notify_upgrade_started(_trust(), "v1.3.0", _db([alice.id, gone.id]))

        assert _recipients_of(send) == ["alice@example.com", ADMIN_EMAIL]

    def test_an_unknown_admin_id_is_dropped(self, send, settings, identity):
        """A UserRole row can outlive the identity it points at."""
        identity.return_value.list_users.return_value = []

        notify_upgrade_started(_trust(), "v1.3.0", _db([uuid4()]))

        assert _recipients_of(send) == [ADMIN_EMAIL]

    def test_the_platform_admin_is_not_emailed_twice(self, send, settings, identity):
        """On a small deployment the trust admin and the platform mailbox coincide."""
        admin = _user(ADMIN_EMAIL.upper())
        identity.return_value.list_users.return_value = [admin]

        notify_upgrade_started(_trust(), "v1.3.0", _db([admin.id]))

        assert _recipients_of(send) == [ADMIN_EMAIL.upper()]

    def test_the_identity_provider_is_not_called_when_there_are_no_admins(self, send, settings, identity):
        """A trust with no admins should not pay for a provider round trip."""
        notify_upgrade_started(_trust(), "v1.3.0", _db([]))

        identity.return_value.list_users.assert_not_called()
        assert _recipients_of(send) == [ADMIN_EMAIL]


class TestContent:
    def test_the_started_email_names_the_trust_and_release(self, send, settings, identity):
        notify_upgrade_started(_trust(), "v1.3.0", _db([]))

        kwargs = send.call_args.kwargs
        assert kwargs["template_name"] == "flip-trust-upgrade-started"
        assert kwargs["template_data"] == {"trust_name": "Trust_1", "trust_code": "T1", "release": "v1.3.0"}

    def test_a_failure_carries_the_detail_and_an_unset_succeeded_flag(self, send, settings, identity):
        notify_upgrade_finished(_trust(), "v1.3.0", "FAILED", "boom", _db([]))

        kwargs = send.call_args.kwargs
        assert kwargs["template_name"] == "flip-trust-upgrade-finished"
        assert kwargs["template_data"]["succeeded"] is False
        assert kwargs["template_data"]["state"] == "FAILED"
        assert kwargs["template_data"]["detail"] == "boom"

    def test_a_success_sets_the_flag_and_sends_no_detail(self, send, settings, identity):
        notify_upgrade_finished(_trust(), "v1.3.0", "SUCCEEDED", None, _db([]))

        data = send.call_args.kwargs["template_data"]
        assert data["succeeded"] is True
        assert data["detail"] == ""

    def test_a_long_log_tail_is_trimmed_from_the_front(self):
        """The failure is at the end of a log capture, so the head is what gets dropped."""
        trimmed = _trim("x" * 4000 + "THE ERROR")

        assert trimmed.startswith("…")
        assert trimmed.endswith("THE ERROR")
        assert len(trimmed) <= 1201


class TestFailuresAreNeverPropagated:
    """No email fault may abort the upgrade record its caller just committed."""

    def test_one_rejected_address_does_not_suppress_the_others(self, send, settings, identity):
        alice = _user("alice@example.com")
        identity.return_value.list_users.return_value = [alice]
        send.side_effect = [Exception("rejected"), None]

        notify_upgrade_started(_trust(), "v1.3.0", _db([alice.id]))

        assert _recipients_of(send) == ["alice@example.com", ADMIN_EMAIL]

    def test_a_dead_identity_provider_is_swallowed(self, send, settings, identity):
        identity.side_effect = Exception("provider down")

        notify_upgrade_finished(_trust(), "v1.3.0", "SUCCEEDED", None, _db([uuid4()]))

        send.assert_not_called()

    def test_a_dead_email_backend_is_swallowed(self, send, settings, identity):
        send.side_effect = Exception("SES unreachable")

        notify_upgrade_started(_trust(), "v1.3.0", _db([]))
