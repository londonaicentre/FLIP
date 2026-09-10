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
"""
``configure-dcm2niix.sh`` must never delete an event subscription.

dcm2niix subscriptions are per-project, created by imaging-api from the project's
``dicom_to_nifti`` flag. Deleting one silently stops DICOM->NIfTI conversion for
that project: imports keep succeeding and simply never convert, which surfaces
much later as training with no data, pointing nowhere near the cause.

The script used to carry a cleanup step for the site-wide subscription that older
versions of it created. That subscription stopped being created in 88adb78b
(2026-03-24), so the cleanup had nothing legitimate left to find — but it could
still match live per-project subscriptions, because:

- XNAT does not echo a top-level ``project-id`` for them (the key is absent), so
  any "is this site-wide?" test based on that field says yes; and
- imaging-api names them ``DICOM-NIfTI Conversion``, which was one of the names the
  cleanup matched.

Checked against four running trusts: it selected 12 of 12 subscriptions, all of them
live per-project rules, with no genuine leftover among them. The step was removed
rather than repaired — a cleanup for something nothing creates can only misfire.

This test exists so it does not come back. It reads the script rather than any
snapshot of it, so re-adding a delete in any form fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGURE_DCM2NIIX = REPO_ROOT / "trust/xnat/xnat/config/configure-dcm2niix.sh"
CONFIGURE_EXPORT_MASK = REPO_ROOT / "trust/xnat/xnat/config/configure-export-mask.sh"

# Matches a DELETE aimed at the event-subscription endpoint, however it is spelled:
# `-X DELETE "$XNAT_URL/xapi/events/subscription/$ID"`, `--request DELETE …`, or with
# the URL built up in a variable first.
_SUBSCRIPTION_DELETE = re.compile(
    r"(-X\s+DELETE|--request\s+DELETE)(?!.*\n?.*xapi/commands)", re.IGNORECASE
)
_SUBSCRIPTION_ENDPOINT = re.compile(r"xapi/events/subscription", re.IGNORECASE)


def _strip_comments(script: str) -> str:
    """Drop comment lines so prose about deletion does not trip the scan.

    Args:
        script: Full shell-script source.

    Returns:
        The source with whole-line comments removed.
    """
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def test_dcm2niix_script_deletes_no_event_subscription() -> None:
    """The guard: no executable line may DELETE an event subscription."""
    code = _strip_comments(CONFIGURE_DCM2NIIX.read_text())

    offending = [
        line
        for line in code.splitlines()
        if _SUBSCRIPTION_ENDPOINT.search(line) and re.search(r"DELETE", line, re.IGNORECASE)
    ]

    assert not offending, (
        "configure-dcm2niix.sh deletes an event subscription again:\n"
        + "\n".join(f"  {line.strip()}" for line in offending)
        + "\n\nPer-project subscriptions are indistinguishable from the retired site-wide one "
        "by name or by project-id (XNAT omits that key for them), so any such delete removes "
        "live conversion rules. Nothing has created a site-wide subscription since 2026-03-24."
    )


def test_dcm2niix_script_does_not_enumerate_subscriptions_for_deletion() -> None:
    """Belt and braces: it should not even fetch the subscription list.

    The removed cleanup started by GETting ``/xapi/events/subscriptions``. Nothing
    else in this script has any reason to, so a reappearing read is the first step
    of a reappearing delete and worth catching on its own.
    """
    code = _strip_comments(CONFIGURE_DCM2NIIX.read_text())

    assert "xapi/events/subscriptions" not in code, (
        "configure-dcm2niix.sh reads the event-subscription list again. This script "
        "creates and enables a command; it has no business enumerating subscriptions."
    )


def test_export_mask_script_still_manages_only_its_own_subscription() -> None:
    """The contrast case — deleting is fine when it is your own, matched by name.

    ``configure-export-mask.sh`` legitimately replaces its own subscription so a
    re-run does not accumulate duplicates. That delete is name-matched to the
    export_mask subscription it just created, never a blanket sweep, and this test
    documents the difference rather than forbidding deletion outright.
    """
    code = _strip_comments(CONFIGURE_EXPORT_MASK.read_text())

    assert "EXPORT_MASK_SUBSCRIPTION_NAME" in code, (
        "configure-export-mask.sh no longer scopes its subscription delete by name; "
        "an unscoped delete there would sweep up per-project dcm2niix subscriptions."
    )
    deletes = [line for line in code.splitlines() if _SUBSCRIPTION_ENDPOINT.search(line) and "DELETE" in line]
    assert deletes, "configure-export-mask.sh should still replace its own subscription on re-run."
