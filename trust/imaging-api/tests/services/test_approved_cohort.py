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

"""``imaging_api.services.approved_cohort``: a project's imaging follows its approved cohort (FLIP#1860)."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from imaging_api.routers.schemas import Project
from imaging_api.services import approved_cohort
from imaging_api.services.import_requests import PrearchiveSession
from imaging_api.utils.encryption import PROJECT_ID_CONTEXT, decrypt
from imaging_api.utils.exceptions import CohortBelowThresholdError, XnatFetchError

HUB_ID = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
XNAT_ID = "0d3c6f8a-1b2c-4d5e-8f90-a1b2c3d4e5f6"
PROJECT = Project(
    pi_firstname="", pi_lastname="", name="p", secondary_ID=HUB_ID, ID=XNAT_ID, URI=f"/data/projects/{XNAT_ID}"
)
DAY = 24 * 3600


def _session(label: str, subject: str, experiment_id: str | None = None, xsi_type: str = "xnat:ctSessionData") -> dict:
    return {
        "ID": experiment_id or f"XNAT_E_{label}",
        "label": label,
        "xsiType": xsi_type,
        "subject_ID": subject,
        "project": XNAT_ID,
    }


class Trust:
    """What a reconcile reads and changes: the served list, XNAT's sessions, the prearchive, and the clock."""

    def __init__(self) -> None:
        self.served: list[str] | Exception = []
        self.experiments: list[dict] = []
        self.prearchive: list[PrearchiveSession] = []
        self.deleted: list[str] = []
        self.now = 1000.0

    def delete(self, url: str, headers: dict[str, str]) -> bool:
        self.deleted.append(url.split("/data/", 1)[1])
        # XNAT removes a subject's sessions with it, and a session's assessors with it.
        self.experiments = [
            e for e in self.experiments if e["ID"] not in url and f"subjects/{e['subject_ID']}?" not in url
        ]
        return True

    async def reconcile(self, grace_seconds: float = DAY) -> None:
        await approved_cohort.reconcile({}, grace_seconds)


@pytest.fixture
def trust(tmp_path: Path) -> Iterator[Trust]:
    trust = Trust()

    async def get_accession_ids(encrypted_project_id: str, query: str) -> list[str]:
        assert decrypt(encrypted_project_id, context=PROJECT_ID_CONTEXT) == HUB_ID
        assert query == ""
        if isinstance(trust.served, Exception):
            raise trust.served
        return trust.served

    with (
        patch.object(approved_cohort, "get_accession_ids", side_effect=get_accession_ids),
        patch.object(approved_cohort, "get_all_projects", return_value=[PROJECT]),
        patch.object(approved_cohort, "_experiments", side_effect=lambda project_id, headers: list(trust.experiments)),
        patch.object(approved_cohort, "get_prearchive_sessions", side_effect=lambda *_: trust.prearchive),
        patch.object(approved_cohort, "delete_prearchive_session", return_value=True) as delete_prearchive,
        patch.object(approved_cohort, "_delete", side_effect=trust.delete),
        patch.object(approved_cohort, "BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
        patch.object(approved_cohort.time, "monotonic", side_effect=lambda: trust.now),
        patch.dict(approved_cohort._served, clear=True),
        patch.dict(approved_cohort._left_at, clear=True),
    ):
        trust.delete_prearchive = delete_prearchive  # type: ignore[attr-defined]
        yield trust


def _cache(tmp_path: Path, net: str, accession: str) -> Path:
    study = tmp_path / net / HUB_ID / accession
    (study / "scans").mkdir(parents=True)
    return study


@pytest.mark.asyncio
async def test_a_download_is_served_only_for_an_accession_on_the_list(trust):
    trust.served = ["A1", "A2"]

    assert await approved_cohort.is_served(HUB_ID, "A1")
    assert not await approved_cohort.is_served(HUB_ID, "A3")


@pytest.mark.asyncio
async def test_a_refused_list_serves_nothing(trust):
    trust.served = CohortBelowThresholdError("refused")

    assert not await approved_cohort.is_served(HUB_ID, "A1")


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_is_an_error_not_an_empty_list(trust):
    trust.served = RuntimeError("data-access-api is down")

    with pytest.raises(RuntimeError):
        await approved_cohort.is_served(HUB_ID, "A1")


@pytest.mark.asyncio
async def test_the_list_is_read_again_once_it_is_a_minute_old(trust):
    trust.served = ["A1"]
    assert await approved_cohort.is_served(HUB_ID, "A1")

    trust.served = []
    trust.now += approved_cohort.SERVED_MAX_AGE_SECONDS - 1
    assert await approved_cohort.is_served(HUB_ID, "A1")
    trust.now += 1
    assert not await approved_cohort.is_served(HUB_ID, "A1")


@pytest.mark.asyncio
async def test_a_reconcile_refreshes_the_list_downloads_check(trust):
    trust.served = ["A1"]
    assert await approved_cohort.is_served(HUB_ID, "A1")

    trust.served = []
    await trust.reconcile()

    assert not await approved_cohort.is_served(HUB_ID, "A1")


@pytest.mark.asyncio
async def test_the_cached_copy_of_an_accession_that_left_goes_at_once_in_every_net(trust, tmp_path):
    kept, gone_1, gone_2 = (
        _cache(tmp_path, "net-1", "A1"),
        _cache(tmp_path, "net-1", "A2"),
        _cache(tmp_path, "net-2", "A2"),
    )
    trust.served = ["A1"]

    await trust.reconcile()

    assert kept.is_dir()
    assert not gone_1.exists()
    assert not gone_2.exists()


@pytest.mark.asyncio
async def test_a_cached_copy_that_cannot_be_removed_does_not_stop_the_xnat_removal(trust):
    trust.served = []
    trust.experiments = [_session("A1", "S1")]

    with (
        patch.object(approved_cohort, "remove_accessions_except", return_value=([], ["net-1/p/A1"])),
        patch.object(approved_cohort, "logger") as logger,
    ):
        await trust.reconcile(grace_seconds=0)

    assert "net-1/p/A1" in logger.warning.call_args.args[0]
    assert len(trust.deleted) == 2


@pytest.mark.asyncio
async def test_a_session_stays_in_xnat_until_its_accession_has_been_off_the_list_for_the_grace_period(trust):
    trust.served = ["A1"]
    trust.experiments = [_session("A1", "S1"), _session("A2", "S2")]

    with patch.object(approved_cohort, "logger") as logger:
        await trust.reconcile()
        trust.now += DAY - 1
        await trust.reconcile()
    assert trust.deleted == []
    # The trust is told once, a day ahead.
    assert [call.args[0] for call in logger.info.call_args_list] == [
        f"Accession A2 of project {HUB_ID} is off the approved cohort; "
        "its XNAT session is removed if it stays off for 24 h"
    ]

    trust.now += 1
    await trust.reconcile()

    assert trust.deleted == [
        f"projects/{XNAT_ID}/subjects/S2/experiments/XNAT_E_A2?removeFiles=true",
        f"projects/{XNAT_ID}/subjects/S2?removeFiles=true",
    ]


@pytest.mark.asyncio
async def test_an_accession_that_comes_back_within_the_grace_period_starts_the_wait_again(trust):
    """A list read while the trust reloads its OMOP tables is short for a while."""
    trust.experiments = [_session("A1", "S1")]
    trust.served = []
    await trust.reconcile()

    trust.now += DAY / 2
    trust.served = ["A1"]
    await trust.reconcile()

    trust.now += DAY / 2
    trust.served = []
    await trust.reconcile()

    assert trust.deleted == []


@pytest.mark.asyncio
async def test_a_subject_keeps_their_record_while_another_of_their_studies_is_in_the_cohort(trust):
    trust.served = ["A1"]
    trust.experiments = [_session("A1", "S1"), _session("A2", "S1")]

    await trust.reconcile(grace_seconds=0)

    assert trust.deleted == [f"projects/{XNAT_ID}/subjects/S1/experiments/XNAT_E_A2?removeFiles=true"]


@pytest.mark.asyncio
async def test_an_assessor_is_never_removed_on_its_own(trust):
    """A segmentation is an experiment too, under a label that is no accession; it goes with its session."""
    trust.served = ["A1"]
    trust.experiments = [_session("A1", "S1"), _session("RoiCollection_1", "S1", xsi_type="icr:roiCollectionData")]

    await trust.reconcile(grace_seconds=0)

    assert trust.deleted == []


@pytest.mark.asyncio
async def test_a_prearchive_session_of_an_accession_that_left_is_deleted(trust):
    trust.served = ["A1"]
    trust.prearchive = [
        PrearchiveSession(project=XNAT_ID, name="A1", status="ERROR", url="/prearchive/projects/p/1/A1"),
        PrearchiveSession(project=XNAT_ID, folderName="A2", status="ERROR", url="/prearchive/projects/p/1/A2"),
    ]

    await trust.reconcile(grace_seconds=0)

    trust.delete_prearchive.assert_called_once_with("/prearchive/projects/p/1/A2", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [CohortBelowThresholdError("refused"), RuntimeError("down")])
async def test_nothing_is_removed_while_the_list_is_refused_or_cannot_be_read(trust, tmp_path, answer):
    cached = _cache(tmp_path, "net-1", "A1")
    trust.experiments = [_session("A1", "S1")]
    trust.served = answer

    await trust.reconcile(grace_seconds=0)
    await trust.reconcile(grace_seconds=0)

    assert trust.deleted == []
    assert cached.is_dir()


@pytest.mark.asyncio
async def test_the_wait_starts_when_an_accession_is_first_seen_off_the_list(trust):
    """Time during which the list could not be read is not time the accession was known to be off it."""
    trust.experiments = [_session("A1", "S1")]
    trust.served = RuntimeError("down")
    await trust.reconcile()

    trust.now += 2 * DAY
    trust.served = []
    await trust.reconcile()

    assert trust.deleted == []


@pytest.mark.asyncio
async def test_an_approved_cohort_with_no_imaging_empties_the_project(trust):
    """An empty list is an answer, unlike a refusal: the approved cohort has no study in it."""
    trust.experiments = [_session("A1", "S1")]
    trust.served = []

    await trust.reconcile(grace_seconds=0)

    assert len(trust.deleted) == 2


@pytest.mark.asyncio
async def test_a_project_that_fails_does_not_stop_the_others(trust):
    other = PROJECT.model_copy(update={"ID": "other", "secondary_ID": HUB_ID})
    trust.experiments = [_session("A2", "S2")]
    trust.served = []
    calls = iter([XnatFetchError("XNAT is restarting"), trust.experiments, []])

    def experiments(project_id, headers):
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with (
        patch.object(approved_cohort, "get_all_projects", return_value=[other, PROJECT]),
        patch.object(approved_cohort, "_experiments", side_effect=experiments),
    ):
        await trust.reconcile(grace_seconds=0)

    assert trust.deleted[0] == f"projects/{XNAT_ID}/subjects/S2/experiments/XNAT_E_A2?removeFiles=true"


def test_only_experiments_the_project_owns_are_listed():
    """One shared into the project belongs to another project, which answers for it."""
    rows = [_session("A1", "S1"), {**_session("A2", "S2"), "project": "another"}]
    response = MagicMock(status_code=200)
    response.json.return_value = {"ResultSet": {"Result": rows}}

    with patch.object(approved_cohort.requests, "get", return_value=response) as get:
        assert approved_cohort._experiments(XNAT_ID, {}) == rows[:1]

    assert f"project={XNAT_ID}" in get.call_args.args[0]


@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), MagicMock(status_code=500)])
def test_a_listing_xnat_cannot_give_removes_nothing(failure):
    with (
        patch.object(approved_cohort.requests, "get", side_effect=[failure]),
        pytest.raises(XnatFetchError),
    ):
        approved_cohort._experiments(XNAT_ID, {})


def test_a_failed_delete_is_reported_without_the_study_it_names():
    with (
        patch.object(approved_cohort.requests, "delete", return_value=MagicMock(status_code=403)),
        patch.object(approved_cohort, "logger") as logger,
    ):
        assert not approved_cohort._delete("http://xnat/data/projects/p/subjects/S1/experiments/E1", {})

    assert "E1" not in str(logger.warning.call_args)


@pytest.mark.asyncio
async def test_the_loop_survives_a_pass_that_fails():
    """XNAT is often not up yet when imaging-api starts."""
    passes = AsyncMock(side_effect=[XnatFetchError("XNAT is starting"), None])
    sleeps = AsyncMock(side_effect=[None, StopAsyncIteration])

    with (
        patch.object(approved_cohort, "get_xnat_auth_headers", return_value={}),
        patch.object(approved_cohort, "reconcile", passes),
        patch.object(approved_cohort.asyncio, "sleep", sleeps),
        pytest.raises(StopAsyncIteration),
    ):
        await approved_cohort.run_reconcile()

    assert passes.await_count == 2
    sleeps.assert_awaited_with(15 * 60)
