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
Behavioural tests for ``configure-dcm2niix.sh``'s legacy-subscription cleanup.

The cleanup deletes *site-wide* dcm2niix subscriptions left by earlier versions of
the script. dcm2niix subscriptions are now **per-project**, created by imaging-api
from the project's ``dicom_to_nifti`` flag, and deleting one of those silently
stops DICOM->NIfTI conversion for that project: imports keep succeeding and simply
never produce NIfTI. Nothing surfaces in the UI.

Telling the two apart is subtler than it looks, which is why this is pinned:

- XNAT does **not** echo a top-level ``project-id`` for imaging-api's per-project
  subscriptions. The key is absent entirely, so ``.["project-id"] == null`` is true
  for them as well as for genuinely site-wide ones.
- Scoping by name does not separate them either: imaging-api names its per-project
  subscriptions ``DICOM-NIfTI Conversion``, one of the two names the cleanup matches.

So the only reliable discriminator is the project scope inside ``event-filter``.
Verified against four live trusts: every subscription present was per-project, and
a filter without the ``project-ids`` test selected all 12 of them for deletion.

The filter is extracted from the script rather than restated, so a change to the
script that drops the ``project-ids`` test fails here instead of on a real trust.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGURE_DCM2NIIX = REPO_ROOT / "trust/xnat/xnat/config/configure-dcm2niix.sh"

# The two historical spellings the cleanup matches: the retired site-wide JSON used
# "DICOM-NifTi Conversion", imaging-api uses "DICOM-NIfTI Conversion".
NAMES = ["DICOM-NifTi Conversion", "DICOM-NIfTI Conversion"]

# jq is not a test-only convenience — configure-dcm2niix.sh itself shells out to it,
# so a machine that cannot run jq cannot run the script under test either. Fail
# loudly rather than skip: a silently skipped guard test is indistinguishable from
# a passing one.
JQ = shutil.which("jq")


def _extract_cleanup_filter() -> str:
    """Pull the jq program out of the ``SITE_SUB_IDS=`` assignment in the script.

    Returns:
        The jq filter source, exactly as the script passes it.
    """
    source = CONFIGURE_DCM2NIIX.read_text()
    match = re.search(
        r"SITE_SUB_IDS=\$\(echo \"\$SUBS\" \| jq -r \\\n"
        r"\s*--argjson names '.*?' \\\n"
        r"\s*'(?P<filter>.*?)'\)",
        source,
        re.DOTALL,
    )
    assert match, (
        "Could not locate the SITE_SUB_IDS jq filter in configure-dcm2niix.sh. "
        "If the assignment was reformatted, update this extractor — do not delete "
        "the test: it is the only thing standing between a filter change and every "
        "project's conversion subscription."
    )
    return match.group("filter")


def _selected_ids(subscriptions: list[dict]) -> list[int]:
    """Run the script's own cleanup filter over a subscription listing.

    Args:
        subscriptions: The payload XNAT's ``/xapi/events/subscriptions`` returns.

    Returns:
        The subscription ids the cleanup would DELETE.
    """
    assert JQ, "jq is required (configure-dcm2niix.sh itself depends on it)"
    result = subprocess.run(
        [JQ, "-r", "--argjson", "names", json.dumps(NAMES), _extract_cleanup_filter()],
        input=json.dumps(subscriptions),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"jq failed: {result.stderr}"
    return [int(line) for line in result.stdout.split() if line.strip()]


def _per_project_subscription(sub_id: int, project_id: str) -> dict:
    """Build a subscription in the exact shape XNAT returns for a per-project one.

    Captured from four live trusts: no top-level ``project-id`` key at all, and the
    project scope carried in ``event-filter.project-ids``.

    Args:
        sub_id: Subscription id.
        project_id: The FLIP project UUID the subscription is scoped to.

    Returns:
        The subscription dict.
    """
    return {
        "id": sub_id,
        "name": "DICOM-NIfTI Conversion",
        "active": True,
        "event-filter": {
            "event-type": "org.nrg.xnat.eventservice.events.ScanEvent",
            "status": "CREATED",
            "project-ids": [project_id],
        },
    }


def test_per_project_subscriptions_are_never_deleted() -> None:
    """The live shape — no ``project-id`` key, project scope in ``event-filter``."""
    subscriptions = [
        _per_project_subscription(1, "baf8ac77-51ec-41d6-bd1c-f2fbd5e4fe2c"),
        _per_project_subscription(2, "9d8d2bba-5be2-4fb4-88a5-49d020070282"),
        _per_project_subscription(3, "2209de0f-f0c9-448f-bdc6-632bdbe15a33"),
    ]

    assert _selected_ids(subscriptions) == [], (
        "The cleanup selected per-project subscriptions. On a populated trust this "
        "deletes every project's DICOM->NIfTI conversion; imports then succeed and "
        "silently never convert."
    )


def test_legacy_site_wide_subscription_is_still_deleted() -> None:
    """The thing the cleanup exists for: the retired site-wide subscription.

    Shaped as the retired ``dcm2niix_event.json`` was — ``project-id`` an empty
    string and no ``project-ids`` key anywhere in ``event-filter``.
    """
    subscriptions = [
        {
            "id": 99,
            "name": "DICOM-NifTi Conversion",
            "project-id": "",
            "event-filter": {
                "event-type": "org.nrg.xnat.eventservice.events.ScanEvent",
                "status": "CREATED",
                "payload-filter": '(@.resources.length() > 0 && "DICOM" in @.resources[*].label)',
            },
        }
    ]

    assert _selected_ids(subscriptions) == [99]


def test_foreign_site_wide_subscriptions_are_left_alone() -> None:
    """Name scoping still holds: only dcm2niix's own subscriptions are cleaned up.

    Covers the export_mask subscription from ``configure-export-mask.sh`` and an
    operator's own — both site-wide, neither this script's business.
    """
    subscriptions = [
        {
            "id": 10,
            "name": "Convert exported OHIF masks to NIfTI",
            "project-id": "",
            "event-filter": {"event-type": "org.nrg.xnat.eventservice.events.ImageAssessorEvent"},
        },
        {
            "id": 11,
            "name": "Operator's own thing",
            "project-id": "",
            "event-filter": {"event-type": "org.nrg.xnat.eventservice.events.ScanEvent"},
        },
    ]

    assert _selected_ids(subscriptions) == []


def test_mixed_listing_deletes_only_the_legacy_site_wide_one() -> None:
    """The realistic re-run case: a populated trust that also carries the legacy row."""
    subscriptions = [
        _per_project_subscription(1, "b2103a25-2452-464a-8b8d-a3d33b0c5522"),
        {
            "id": 99,
            "name": "DICOM-NifTi Conversion",
            "project-id": "",
            "event-filter": {"event-type": "org.nrg.xnat.eventservice.events.ScanEvent"},
        },
        _per_project_subscription(2, "4beda198-1e20-46e2-89a8-2a7621ba4c70"),
        {
            "id": 10,
            "name": "Convert exported OHIF masks to NIfTI",
            "project-id": "",
            "event-filter": {"event-type": "org.nrg.xnat.eventservice.events.ImageAssessorEvent"},
        },
    ]

    assert _selected_ids(subscriptions) == [99]


@pytest.mark.parametrize("missing", ["project-ids-test"])
def test_filter_still_contains_the_project_scope_test(missing: str) -> None:
    """Guard the extractor itself: the filter must test ``project-ids``.

    If the assignment is reformatted such that the extractor silently matches a
    filter without the project-scope test, the behavioural tests above would still
    pass only by luck. Pin the discriminator explicitly.
    """
    assert "project-ids" in _extract_cleanup_filter(), (
        f"The cleanup filter no longer tests {missing}; per-project subscriptions "
        "are indistinguishable from site-wide ones without it."
    )
