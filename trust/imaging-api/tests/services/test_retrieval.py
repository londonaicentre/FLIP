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

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from imaging_api.db.models import ExecutedPacsRequest, QueuedPacsRequest
from imaging_api.routers.schemas import Experiment, ImportStatus, ImportStudyResponse, Patient, Study
from imaging_api.services.retrieval import (
    get_import_status,
    retrieve_images_for_project,
    retry_retrieve_images_for_project,
)
from imaging_api.utils.exceptions import NotFoundError


@pytest.fixture
def headers():
    return {}


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
    mock_get_project, mock_encrypt, mock_get_accession_ids,
    mock_query, mock_queue, headers,
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
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
@patch("imaging_api.services.retrieval.get_project")
async def test_retrieve_images_no_studies_found(
    mock_get_project, mock_encrypt, mock_get_accession_ids, mock_query, headers,
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
    mock_get_project, mock_encrypt, mock_get_accession_ids, mock_query, headers,
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
async def test_retrieve_images_partial_queue_failure(
    mock_get_project, mock_encrypt, mock_get_accession_ids,
    mock_query, mock_queue, headers,
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
async def test_retrieve_images_multiple_studies_for_accession(
    mock_get_project, mock_encrypt, mock_get_accession_ids,
    mock_query, mock_queue, headers,
):
    """When multiple studies match an accession number, only the first is used."""
    mock_get_project.return_value = MagicMock()
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_query.return_value = [_make_study("ACC1", "1.2.3.1"), _make_study("ACC1", "1.2.3.2")]
    mock_queue.return_value = [_make_import_response("ACC1")]

    result = await retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is True
    # Only the first study UID should have been queued
    call_args = mock_queue.call_args[0][0]
    assert len(call_args.studies) == 1
    assert call_args.studies[0].study_instance_uid == "1.2.3.1"


# ===========================================================================
# get_import_status
# ===========================================================================


def _mock_get_session(direct_archive=None, executed=None, queued=None):
    """Return patch objects for the three DB queries and get_session.

    ``get_session`` is an async generator that yields a single ``AsyncSession``.
    We replace it with a function that returns a fresh async-generator each time
    it is called (it is called three times inside ``get_import_status``).
    """
    mock_session = MagicMock()

    async def fake_session():
        yield mock_session

    return (
        patch("imaging_api.services.retrieval.get_session", side_effect=lambda: fake_session()),
        patch("imaging_api.services.retrieval.get_direct_archive_sessions_by_project",
              new_callable=AsyncMock, return_value=direct_archive or []),
        patch("imaging_api.services.retrieval.get_executed_pacs_request_by_project",
              new_callable=AsyncMock, return_value=executed or []),
        patch("imaging_api.services.retrieval.get_queued_pacs_request_by_project",
              new_callable=AsyncMock, return_value=queued or []),
    )


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_experiments")
@patch("imaging_api.services.retrieval.get_accession_ids", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.encrypt")
async def test_get_import_status_all_successful(
    mock_encrypt, mock_get_accession_ids, mock_get_experiments, headers,
):
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1", "ACC2"]
    mock_get_experiments.return_value = [
        Experiment(ID="e1", label="ACC1", date="2023-01-01", project="proj1",
                   insert_date="2023-01-01", xsiType="xnat:ctScanData", URI="/exp/e1"),
        Experiment(ID="e2", label="ACC2", date="2023-01-01", project="proj1",
                   insert_date="2023-01-01", xsiType="xnat:ctScanData", URI="/exp/e2"),
    ]

    p_session, p_direct, p_executed, p_queued = _mock_get_session()
    with p_session, p_direct, p_executed, p_queued:
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
    mock_encrypt, mock_get_accession_ids, mock_get_experiments, headers,
):
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC_OK", "ACC_EXEC", "ACC_QUEUED", "ACC_UNKNOWN"]
    mock_get_experiments.return_value = [
        Experiment(ID="e1", label="ACC_OK", date="2023-01-01", project="proj1",
                   insert_date="2023-01-01", xsiType="xnat:ctScanData", URI="/exp/e1"),
    ]

    executed = [ExecutedPacsRequest(
        id=1, created=datetime(2023, 1, 1), accession_number="ACC_EXEC", status="EXECUTING", xnat_project="proj1",
    )]
    queued = [QueuedPacsRequest(
        id=2, created=datetime(2023, 1, 1), accession_number="ACC_QUEUED", status="QUEUED", xnat_project="proj1",
    )]

    p_session, p_direct, p_executed, p_queued = _mock_get_session(executed=executed, queued=queued)
    with p_session, p_direct, p_executed, p_queued:
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
    mock_encrypt, mock_get_accession_ids, mock_get_experiments, headers,
):
    mock_encrypt.return_value = "encrypted_id"
    mock_get_accession_ids.return_value = ["ACC1"]
    mock_get_experiments.return_value = []

    p_session, p_direct, p_executed, p_queued = _mock_get_session()
    with p_session, p_direct, p_executed, p_queued:
        status = await get_import_status("proj1", "SELECT *", headers)

    assert status.successful == []
    assert status.queue_failed == ["ACC1"]


# ===========================================================================
# retry_retrieve_images_for_project
# ===========================================================================


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_no_failures(mock_get_project, mock_get_status, headers):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=["ACC1"], failed=[], queue_failed=[], queued=[], processing=[],
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
    mock_get_project, mock_get_status, mock_query, mock_queue, headers,
):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=["ACC_OK"], failed=["ACC_FAIL"], queue_failed=["ACC_QF"],
        queued=[], processing=[],
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
        successful=[], failed=["ACC1"], queue_failed=[], queued=[], processing=[],
    )
    mock_query.side_effect = Exception("PACS down")

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_partial_queue_failure(
    mock_get_project, mock_get_status, mock_query, mock_queue, headers,
):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[], failed=["ACC1"], queue_failed=[], queued=[], processing=[],
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
        successful=[], failed=["ACC1"], queue_failed=[], queued=[], processing=[],
    )
    mock_query.return_value = []

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is False


@pytest.mark.asyncio
@patch("imaging_api.services.retrieval.queue_image_import_request")
@patch("imaging_api.services.retrieval.query_by_accession_number")
@patch("imaging_api.services.retrieval.get_import_status", new_callable=AsyncMock)
@patch("imaging_api.services.retrieval.get_project")
async def test_retry_multiple_studies_uses_first(
    mock_get_project, mock_get_status, mock_query, mock_queue, headers,
):
    mock_get_project.return_value = MagicMock()
    mock_get_status.return_value = ImportStatus(
        successful=[], failed=["ACC1"], queue_failed=[], queued=[], processing=[],
    )
    mock_query.return_value = [_make_study("ACC1", "1.2.3.1"), _make_study("ACC1", "1.2.3.2")]
    mock_queue.return_value = [_make_import_response("ACC1")]

    result = await retry_retrieve_images_for_project("proj1", "SELECT *", headers)
    assert result is True
    call_args = mock_queue.call_args[0][0]
    assert call_args.studies[0].study_instance_uid == "1.2.3.1"
