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

from collections.abc import Iterator
from datetime import datetime
from threading import current_thread, main_thread
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from imaging_api.routers.schemas import Experiment, ImportStatus, ImportStudyResponse, Patient, Study
from imaging_api.services.import_requests import DirectArchiveSession, PacsRequest
from imaging_api.services.retrieval import (
    get_import_status,
    retrieve_images_for_project,
    retry_retrieve_images_for_project,
    select_study_for_accession,
)
from imaging_api.utils.encryption import PROJECT_ID_CONTEXT
from imaging_api.utils.exceptions import CohortBelowThresholdError, NotFoundError, XnatFetchError


@pytest.fixture
def headers():
    return {}


@pytest.fixture(autouse=True)
def default_pacs() -> Iterator[MagicMock]:
    """XNAT has a default query/retrieve PACS unless a test says otherwise (FLIP#1456 E1)."""
    with patch("imaging_api.services.retrieval.resolve_default_pacs_id", return_value=1) as resolved:
        yield resolved


def _make_study(accession_number: str = "ACC1", uid: str = "1.2.3.4") -> Study:
    return Study(
        studyInstanceUid=uid,
        studyDescription="Test",
        accessionNumber=accession_number,
        studyDate="2023-01-01",
        modalitiesInStudy=["CT"],
        referringPhysicianName="Dr. Test",
        patient=Patient(id="P1", name="Test Patient", sex="M"),
    )


def _make_import_response(accession_number: str = "ACC1", status: str = "QUEUED") -> ImportStudyResponse:
    return ImportStudyResponse(
        id=1,
        pacsId=1,
        status=status,
        accessionNumber=accession_number,
        queuedTime=100,
        created=100,
        priority=1,
    )


# ===========================================================================
# retrieve_images_for_project
# ===========================================================================


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_success(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1", "ACC2"]
    mock_query.side_effect = [
        [_make_study("ACC1", "1.2.3.1")],
        [_make_study("ACC2", "1.2.3.2")],
    ]
    mock_queue.return_value = [
        _make_import_response("ACC1"),
        _make_import_response("ACC2"),
    ]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is True
    mock_queue.assert_called_once()
    mock_encrypt.assert_called_once_with(ANY, context=PROJECT_ID_CONTEXT)


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_project_not_found(mock_get_project, headers):
    mock_get_project.side_effect = NotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await retrieve_images_for_project("missing", "SELECT *", headers)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_project_generic_error(mock_get_project, headers):
    mock_get_project.side_effect = RuntimeError("boom")

    with pytest.raises(HTTPException) as exc_info:
        await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_below_threshold_queues_nothing(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    """A below-threshold cohort is a settled outcome, not a crash.

    This runs as a background task after the create-project response has been sent, so letting
    the exception escape would surface only as a traceback in the server log while the hub
    still recorded the imaging project as created. Return False and queue nothing instead.
    """
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.side_effect = CohortBelowThresholdError("cohort below minimum size")

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)

    assert result is False
    mock_query.assert_not_called()
    mock_queue.assert_not_called()


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_below_threshold_raises_403(mock_encrypt, mock_get_accession_ids, headers):
    """The status path has a caller waiting, so the refusal must reach it as a 403 with a
    readable reason — trust-api relays this detail to the hub for the per-trust status."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.side_effect = CohortBelowThresholdError("cohort below minimum size")

    with pytest.raises(HTTPException) as exc_info:
        await get_import_status("proj1", "SELECT *", headers)

    assert exc_info.value.status_code == 403
    assert "below the trust's minimum size" in exc_info.value.detail


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_no_studies_found(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    headers,
):
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.return_value = []

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_query_exception_skips_study(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    headers,
):
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.side_effect = Exception("PACS timeout")

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_skips_study_with_unsafe_accession_number(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    """A PACS-returned accession number that fails the #908 path-segment rule must be
    skipped like a failed query, not abort the whole batch: the remaining studies queue."""
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1", "ACC 2", "ACC3"]
    mock_query.side_effect = [
        [_make_study("ACC1", "1.2.3.1")],
        [_make_study("ACC 2", "1.2.3.2")],
        [_make_study("ACC3", "1.2.3.3")],
    ]
    mock_queue.return_value = [_make_import_response("ACC1"), _make_import_response("ACC3")]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)

    assert result is True
    mock_queue.assert_called_once()
    queued = [study.accession_number for study in mock_queue.call_args[0][0].studies]
    assert queued == ["ACC1", "ACC3"]


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_partial_queue_failure(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.return_value = [_make_study("ACC1")]
    mock_queue.return_value = [_make_import_response("ACC1", status="FAILED")]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_two_studies_under_one_accession_are_skipped_not_guessed(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    """A PACS holding two studies under the cohort's accession is ambiguous: neither is imported."""
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.return_value = [_make_study("ACC1", "1.2.3.1"), _make_study("ACC1", "1.2.3.2")]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False
    mock_queue.assert_not_called()


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_takes_the_exact_accession_not_the_first_answer(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    """A PACS may match loosely (Orthanc folds case); only the study with the exact accession is the cohort's."""
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.return_value = [_make_study("acc1", "1.2.3.1"), _make_study("ACC1", "1.2.3.2")]
    mock_queue.return_value = [_make_import_response("ACC1")]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is True
    call_args = mock_queue.call_args[0][0]
    assert [s.study_instance_uid for s in call_args.studies] == ["1.2.3.2"]


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_skips_an_answer_with_no_exact_accession(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_query,
    mock_queue,
    headers,
):
    """Studies the PACS returned for a loose match are not the cohort's: nothing is imported."""
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.return_value = [_make_study("acc1", "1.2.3.1"), _make_study("ACC10", "1.2.3.2")]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False
    mock_queue.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "expected_uid"),
    [
        ([], None),
        ([("ACC1", "1.2.3.1")], "1.2.3.1"),
        ([("acc1", "1.2.3.1"), ("ACC1", "1.2.3.2")], "1.2.3.2"),
        ([("acc1", "1.2.3.1")], None),
        ([("ACC1", "1.2.3.1"), ("ACC1", "1.2.3.2")], None),
    ],
    ids=["empty", "exact", "exact-among-loose", "loose-only", "duplicate-accession"],
)
def test_select_study_for_accession(answer, expected_uid):
    studies = [_make_study(acc, uid) for acc, uid in answer]
    selected = select_study_for_accession("ACC1", studies, "1/1")
    assert (selected.study_instance_uid if selected else None) == expected_uid


# ===========================================================================
# get_import_status
# ===========================================================================


def _import_records(direct_archive=None, executed=None, queued=None):
    """Patch the three XNAT reads get_import_status classifies a cohort by."""
    return patch.multiple(
        "imaging_api.services.retrieval",
        get_direct_archive_sessions=MagicMock(return_value=direct_archive or []),
        get_executed_import_requests=MagicMock(return_value=executed or []),
        get_queued_import_requests=MagicMock(return_value=queued or []),
    )


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_all_successful(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1", "ACC2"]
    mock_get_experiments.return_value = [
        Experiment(
            ID="e1",
            label="ACC1",
            date="2023-01-01",
            project="proj1",
            insert_date="2023-01-01",
            xsiType="xnat:ctScanData",
            URI="/exp/e1",
        ),
        Experiment(
            ID="e2",
            label="ACC2",
            date="2023-01-01",
            project="proj1",
            insert_date="2023-01-01",
            xsiType="xnat:ctScanData",
            URI="/exp/e2",
        ),
    ]

    with _import_records():
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.successful == ["ACC1", "ACC2"]
    assert status.queued == []
    assert status.processing == []
    assert status.queue_failed == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_mixed(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_OK", "ACC_EXEC", "ACC_QUEUED", "ACC_UNKNOWN"]
    mock_get_experiments.return_value = [
        Experiment(
            ID="e1",
            label="ACC_OK",
            date="2023-01-01",
            project="proj1",
            insert_date="2023-01-01",
            xsiType="xnat:ctScanData",
            URI="/exp/e1",
        ),
    ]

    executed = [_executed("ACC_EXEC", "EXECUTING")]
    queued = [_queued("ACC_QUEUED")]

    with _import_records(executed=executed, queued=queued):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.successful == ["ACC_OK"]
    assert status.processing == ["ACC_EXEC"]
    assert status.queued == ["ACC_QUEUED"]
    assert status.queue_failed == ["ACC_UNKNOWN"]


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_no_experiments(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_get_experiments.return_value = []

    with _import_records():
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.successful == []
    assert status.queue_failed == ["ACC1"]


# Built from the JSON XNAT serves (times in epoch milliseconds), so a wrong alias on the models fails
# here rather than reading as None.
_CREATED_MS = int(datetime(2023, 1, 1).timestamp() * 1000)


def _executed(accession_number: str, status: str) -> PacsRequest:
    return PacsRequest.model_validate(
        {"id": 1, "xnatProject": "proj1", "accessionNumber": accession_number, "status": status, "created": _CREATED_MS}
    )


def _queued(accession_number: str, status: str = "QUEUED") -> PacsRequest:
    return PacsRequest.model_validate(
        {"id": 2, "xnatProject": "proj1", "accessionNumber": accession_number, "status": status, "created": _CREATED_MS}
    )


def _direct_archive(accession_number: str, status: str, *, named: bool = True) -> DirectArchiveSession:
    # XNAT leaves a null name out of the JSON, so an unnamed session has no "name" key at all.
    session = {"id": 1, "project": "proj1", "folderName": accession_number, "status": status}
    if named:
        session["name"] = accession_number
    return DirectArchiveSession.model_validate(session)


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_executed_failed_is_failed(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """A PACS retrieval that errored (executed status FAILED) is `failed`, not `processing`."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_FAIL"]
    mock_get_experiments.return_value = []

    with _import_records(executed=[_executed("ACC_FAIL", "FAILED")]):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.failed == ["ACC_FAIL"]
    assert status.processing == []
    assert status.queue_failed == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_direct_archive_error_is_failed(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """A directArchive build that errored (status ERROR) is `failed`, not `queue_failed`/`processing`."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_ARCH_FAIL"]
    mock_get_experiments.return_value = []

    with _import_records(
        direct_archive=[_direct_archive("ACC_ARCH_FAIL", "ERROR")],
    ):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.failed == ["ACC_ARCH_FAIL"]
    assert status.processing == []
    assert status.queue_failed == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_direct_archive_error_overrides_received(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """A study whose transfer RECEIVED but whose directArchive build then errored is `failed`.

    This is the failure mode (study left the queue but never became a queryable experiment)
    that was previously reported as `processing` forever.
    """
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_X"]
    mock_get_experiments.return_value = []

    with _import_records(
        direct_archive=[_direct_archive("ACC_X", "ERROR")],
        executed=[_executed("ACC_X", "RECEIVED")],
    ):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.failed == ["ACC_X"]
    assert status.processing == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_direct_archive_receiving_is_processing(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """A directArchive build still in progress (RECEIVING) is `processing`, not `failed`/`queue_failed`."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_RECV"]
    mock_get_experiments.return_value = []

    with _import_records(
        direct_archive=[_direct_archive("ACC_RECV", "RECEIVING")],
    ):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.processing == ["ACC_RECV"]
    assert status.failed == []
    assert status.queue_failed == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_direct_archive_error_without_name_falls_back_to_folder_name(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """A directArchive ERROR whose `name` is NULL is still attributed via its folder_name.

    XNAT populates `name` for DQR imports, but it can be NULL for other archive rows; folder_name
    carries the same accession label, so a NULL-named archive failure is recovered, not dropped.
    """
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_X"]
    mock_get_experiments.return_value = []

    with _import_records(direct_archive=[_direct_archive("ACC_X", "ERROR", named=False)]):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.failed == ["ACC_X"]
    assert status.queue_failed == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_failed_precedes_queued(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """A terminal-failed accession that also has a (re-)queued row is reported `failed`, not `queued`."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC"]
    mock_get_experiments.return_value = []

    with _import_records(executed=[_executed("ACC", "FAILED")], queued=[_queued("ACC")]):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.failed == ["ACC"]
    assert status.queued == []


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_experiment_present_beats_stale_direct_archive_error(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    headers,
):
    """`successful` (archived as an experiment) wins over a stale directArchive ERROR for the same accession."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC"]
    mock_get_experiments.return_value = [
        Experiment(
            ID="e1",
            label="ACC",
            date="2023-01-01",
            project="proj1",
            insert_date="2023-01-01",
            xsiType="xnat:ctScanData",
            URI="/exp/e1",
        ),
    ]

    with _import_records(direct_archive=[_direct_archive("ACC", "ERROR")]):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.successful == ["ACC"]
    assert status.failed == []


def _xnat_json(body) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = body
    return response


def _fake_xnat(method: str, url: str, **kwargs) -> MagicMock:
    """XNAT's answers to the REST reads, shaped as DQR 3.0.0 and xnat-web 1.10.0 serve them."""
    if "/xapi/dqr/import/queue" in url:
        return _xnat_json(
            [
                {"id": 31, "xnatProject": "proj1", "accessionNumber": "ACC_Q", "status": "QUEUED"},
                {"id": 30, "xnatProject": "other", "accessionNumber": "ACC_OTHER_Q", "status": "QUEUED"},
            ]
        )
    if "/xapi/dqr/import/history" in url:
        return _xnat_json(
            [
                {"id": 22, "xnatProject": "proj1", "accessionNumber": "ACC_F", "status": "FAILED", "errorMessage": "x"},
                {"id": 21, "xnatProject": "proj1", "accessionNumber": "ACC_R", "status": "RECEIVED"},
                {"id": 20, "xnatProject": "other", "accessionNumber": "ACC_OTHER_F", "status": "FAILED"},
            ]
        )
    if "/xapi/direct-archive" in url:
        # An archive failure with no name: attributed by its folder name.
        return _xnat_json([{"id": 5, "project": "proj1", "folderName": "ACC_D", "status": "ERROR"}])
    if "/data/projects" in url:
        return _xnat_json({"ResultSet": {"Result": [{"ID": "proj1"}]}})
    raise AssertionError(f"unexpected XNAT {method} {url}")


def _experiment(label: str) -> Experiment:
    return Experiment.model_validate(
        {
            "ID": f"e-{label}",
            "label": label,
            "date": "2023-01-01",
            "project": "proj1",
            "insert_date": "2023-01-01",
            "xsiType": "xnat:ctScanData",
            "URI": f"/exp/e-{label}",
        }
    )


@pytest.mark.asyncio
@patch("imaging_api.services.import_requests.requests.request", side_effect=_fake_xnat)
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_classifies_xnat_rest_records(
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    mock_request,
    headers,
):
    """The REST reads feed the classification as the table reads did, another project's rows ignored."""
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = [
        "ACC_OK",
        "ACC_Q",
        "ACC_F",
        "ACC_R",
        "ACC_D",
        "ACC_OTHER_Q",
        "ACC_OTHER_F",
        "ACC_NONE",
    ]
    mock_get_experiments.return_value = [_experiment("ACC_OK")]

    status = await get_import_status("proj1", "SELECT *", headers)

    assert status == ImportStatus(
        successful=["ACC_OK"],
        failed=["ACC_F", "ACC_D"],
        processing=["ACC_R"],
        queued=["ACC_Q"],
        queue_failed=["ACC_OTHER_Q", "ACC_OTHER_F", "ACC_NONE"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cohort", [["ACC1", "ACC2", "ACC1"], []], ids=["all-archived", "empty-cohort"])
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_reads_no_dqr_record_when_every_accession_is_archived(
    mock_encrypt, mock_get_accession_ids, mock_get_experiments, cohort, headers
):
    """An archived accession is `successful` whatever DQR holds, so a finished project reads none of it."""
    mock_get_accession_ids.return_value = cohort
    mock_get_experiments.return_value = [_experiment("ACC1"), _experiment("ACC2")]

    with patch("imaging_api.services.retrieval._read_import_records") as read:
        status = await get_import_status("proj1", "SELECT *", headers)

    read.assert_not_called()
    assert status == ImportStatus(successful=cohort)


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_reads_dqr_off_the_event_loop_once_one_accession_is_not_archived(
    mock_encrypt, mock_get_accession_ids, mock_get_experiments, headers
):
    """The DQR reads are blocking calls on a single-worker service, so they must not run on the loop."""
    mock_get_accession_ids.return_value = ["ACC1", "ACC2"]
    mock_get_experiments.return_value = [_experiment("ACC1")]
    threads = []

    def read(project_id, hdrs):
        threads.append(current_thread())
        return [], [], []

    with patch("imaging_api.services.retrieval._read_import_records", side_effect=read):
        status = await get_import_status("proj1", "SELECT *", headers)

    assert len(threads) == 1
    assert threads[0] is not main_thread()
    assert status == ImportStatus(successful=["ACC1"], queue_failed=["ACC2"])


# ===========================================================================
# retry_retrieve_images_for_project
# ===========================================================================


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_no_failures(mock_get_project, mock_get_status, headers):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=["ACC1"],
        failed=[],
        queue_failed=[],
        queued=[],
        processing=[],
    )

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is True


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_project_not_found(mock_get_project, headers):
    mock_get_project.side_effect = NotFoundError("not found")

    with pytest.raises(HTTPException) as exc_info:
        await retry_retrieve_images_for_project("missing", "SELECT *", headers)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_project_generic_error(mock_get_project, headers):
    mock_get_project.side_effect = RuntimeError("boom")

    with pytest.raises(HTTPException) as exc_info:
        await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_requeues_failed_studies(
    mock_get_project,
    mock_get_status,
    mock_query,
    mock_queue,
    headers,
):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=["ACC_OK"],
        failed=["ACC_FAIL"],
        queue_failed=["ACC_QF"],
        queued=[],
        processing=[],
    )
    mock_query.side_effect = [
        [_make_study("ACC_FAIL", "1.2.3.1")],
        [_make_study("ACC_QF", "1.2.3.2")],
    ]
    mock_queue.return_value = [
        _make_import_response("ACC_FAIL"),
        _make_import_response("ACC_QF"),
    ]

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is True
    mock_queue.assert_called_once()
    call_args = mock_queue.call_args[0][0]
    assert len(call_args.studies) == 2


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_all_queries_fail(mock_get_project, mock_get_status, mock_query, headers):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[],
        failed=["ACC1"],
        queue_failed=[],
        queued=[],
        processing=[],
    )
    mock_query.side_effect = Exception("PACS down")

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_skips_study_with_unsafe_accession_number(
    mock_get_project,
    mock_get_status,
    mock_query,
    mock_queue,
    headers,
):
    """Same guarantee as the first-pass import: one unsafe accession number does not
    take the rest of the retry batch down with it."""
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[],
        failed=["ACC_FAIL", "ACC BAD"],
        queue_failed=[],
        queued=[],
        processing=[],
    )
    mock_query.side_effect = [
        [_make_study("ACC_FAIL", "1.2.3.1")],
        [_make_study("ACC BAD", "1.2.3.2")],
    ]
    mock_queue.return_value = [_make_import_response("ACC_FAIL")]

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)

    assert result is True
    mock_queue.assert_called_once()
    queued = [study.accession_number for study in mock_queue.call_args[0][0].studies]
    assert queued == ["ACC_FAIL"]


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_partial_queue_failure(
    mock_get_project,
    mock_get_status,
    mock_query,
    mock_queue,
    headers,
):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[],
        failed=["ACC1"],
        queue_failed=[],
        queued=[],
        processing=[],
    )
    mock_query.return_value = [_make_study("ACC1")]
    mock_queue.return_value = [_make_import_response("ACC1", status="FAILED")]

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_no_study_found_for_accession(mock_get_project, mock_get_status, mock_query, headers):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[],
        failed=["ACC1"],
        queue_failed=[],
        queued=[],
        processing=[],
    )
    mock_query.return_value = []

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_two_studies_under_one_accession_are_skipped_not_guessed(
    mock_get_project,
    mock_get_status,
    mock_query,
    mock_queue,
    headers,
):
    """The retry path narrows the PACS answer the same way the first import does."""
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[],
        failed=["ACC1"],
        queue_failed=[],
        queued=[],
        processing=[],
    )
    mock_query.return_value = [_make_study("ACC1", "1.2.3.1"), _make_study("ACC1", "1.2.3.2")]

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False
    mock_queue.assert_not_called()


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_requeues_terminal_failures_end_to_end(
    mock_get_project,
    mock_encrypt,
    mock_get_accession_ids,
    mock_get_experiments,
    mock_query,
    mock_queue,
    headers,
):
    """Producer->consumer seam: a real terminal failure flows from get_import_status `failed` into the re-queue.

    No get_import_status mock — an executed FAILED request must be classified `failed` by the producer
    and then re-queried + re-queued by retry. Guards against the two halves drifting apart.
    """
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_FAIL"]
    mock_get_experiments.return_value = []
    mock_query.return_value = [_make_study("ACC_FAIL")]
    mock_queue.return_value = [_make_import_response("ACC_FAIL")]

    with _import_records(executed=[_executed("ACC_FAIL", "FAILED")]):
        result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)

    assert result is True
    mock_query.assert_called_once_with("ACC_FAIL", headers)
    assert mock_queue.call_args[0][0].studies[0].accession_number == "ACC_FAIL"


# ===========================================================================
# A missing default query/retrieve PACS ends the run before its queries (FLIP#1456 E1)
# ===========================================================================

UNRESOLVABLE_PACS = [
    NotFoundError("XNAT has no PACS flagged as the default query/retrieve PACS"),
    XnatFetchError("Could not list the PACS registered in XNAT: HTTP 503"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", UNRESOLVABLE_PACS, ids=["no default", "listing failed"])
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_without_a_default_pacs_logs_why_and_queries_nothing(
    mock_get_project, mock_encrypt, mock_get_accession_ids, mock_query, mock_queue, error, default_pacs, headers, caplog
):
    """Otherwise every C-FIND is DQR's 404 and the run ends "No studies found": a data problem in disguise."""
    mock_get_accession_ids.return_value = ["ACC1", "ACC2"]
    default_pacs.side_effect = error

    assert await retrieve_images_for_project("proj1", "SELECT *", headers) is False

    assert f"Not importing images for project proj1: {error}" in caplog.text
    assert not mock_query.called
    assert not mock_queue.called


@pytest.mark.asyncio
@pytest.mark.parametrize("error", UNRESOLVABLE_PACS, ids=["no default", "listing failed"])
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_without_a_default_pacs_logs_why_and_queries_nothing(
    mock_get_project, mock_get_status, mock_query, mock_queue, error, default_pacs, headers, caplog
):
    mock_get_status.return_value = ImportStatus(failed=["ACC1"], queue_failed=["ACC2"])
    default_pacs.side_effect = error

    assert await retry_retrieve_images_for_project("proj1", "SELECT *", headers) is False

    assert f"Not importing images for project proj1: {error}" in caplog.text
    assert not mock_query.called
    assert not mock_queue.called


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_the_default_pacs_is_checked_once_per_run_not_per_accession(
    mock_get_project, mock_encrypt, mock_get_accession_ids, mock_query, mock_queue, default_pacs, headers
):
    accessions = ["ACC1", "ACC2", "ACC3"]
    mock_get_accession_ids.return_value = accessions
    mock_query.side_effect = [[_make_study(a, f"1.2.3.{n}")] for n, a in enumerate(accessions)]
    mock_queue.return_value = [_make_import_response(a) for a in accessions]

    assert await retrieve_images_for_project("proj1", "SELECT *", headers) is True

    default_pacs.assert_called_once_with(headers)
    assert mock_query.call_count == 3


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_a_retry_with_nothing_to_retry_does_not_look_up_the_pacs(
    mock_get_project, mock_get_status, default_pacs, headers
):
    mock_get_status.return_value = ImportStatus(successful=["ACC1"])

    assert await retry_retrieve_images_for_project("proj1", "SELECT *", headers) is True
    assert not default_pacs.called
