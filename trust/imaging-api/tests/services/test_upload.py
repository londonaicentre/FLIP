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

import os
import tempfile
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
import requests

from imaging_api.config import get_settings
from imaging_api.services.upload import (
    create_xnat_scan,
    get_scan_xsi_types_from_experiment_response,
    upload_data_to_xnat,
    upload_file_to_xnat,
    xnat_kept_existing_file,
)
from imaging_api.utils.exceptions import AlreadyExistsError

XNAT_URL = get_settings().XNAT_URL

# The body of XNAT's 200 for a file it kept instead of writing.
_DUPLICATES_BODY = b'{"duplicates": ["scan.nii"]}'


def _xnat_put_response(
    status_code: int = 200, body: bytes = b"", content_type: str = "text/plain"
) -> requests.Response:
    """Build the response XNAT gives a file PUT. The defaults are a clean write: an empty 200.

    Args:
        status_code (int): HTTP status.
        body (bytes): Raw response body.
        content_type (str): Content-Type header.

    Returns:
        requests.Response: A real response object, so ``ok`` and the body behave as in production.
    """
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    response.headers["Content-Type"] = content_type
    return response


def _experiment_response(scans: tuple[tuple[str, str], ...] = (("SCAN1", "xnat:crScanData"),)) -> dict:
    """Build XNAT's experiment document (``GET .../experiments/{label}?format=json``), trimmed to what the upload reads.

    Args:
        scans (tuple[tuple[str, str], ...]): ``(scan ID, scan xsi:type)`` for each existing scan, in order. An empty
            session has no ``scans/scan`` child.

    Returns:
        dict: The experiment document.
    """
    items = [{"meta": {"xsi:type": xsi_type}, "data_fields": {"ID": scan_id}} for scan_id, xsi_type in scans]
    children = [{"field": "scans/scan", "items": items}] if scans else []
    return {
        "items": [
            {"meta": {"xsi:type": "xnat:crSessionData"}, "data_fields": {"subject_ID": "SUBJ1"}, "children": children}
        ]
    }


# ── create_xnat_scan ──


@patch("imaging_api.services.upload.requests.put")
def test_create_xnat_scan_sends_the_given_xsi_type(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=200)

    create_xnat_scan(
        project_id="test",
        subject_id="XNAT_S00002",
        experiment_id_or_label="XNAT_E00002",
        scan_id="1_2_826_0_1_3680043_8_274_1_1_8323329_1189734_1740750875_622789",
        xsi_type="xnat:dxScanData",
        headers=headers,
    )

    assert urlsplit(mock_put.call_args.args[0]).query == "xsiType=xnat%3AdxScanData"


@patch("imaging_api.services.upload.requests.put")
def test_create_xnat_scan_failure(mock_put, headers):
    mock_put.return_value = MagicMock(status_code=500, text="Internal error")
    with pytest.raises(Exception, match="Error creating scan"):
        create_xnat_scan("PROJ", "SUBJ", "EXP", "SCAN1", "xnat:crScanData", headers)


# ── get_scan_xsi_types_from_experiment_response ──


def test_get_scan_xsi_types_reads_every_scan_and_nothing_else():
    response = _experiment_response((("1", "xnat:mrScanData"), ("3", "xnat:otherDicomScanData")))
    share = {"meta": {"xsi:type": "xnat:experimentData_share"}, "data_fields": {"project": "OTHER"}}
    response["items"][0]["children"].insert(0, {"field": "sharing/share", "items": [share]})

    assert get_scan_xsi_types_from_experiment_response(response) == {
        "1": "xnat:mrScanData",
        "3": "xnat:otherDicomScanData",
    }
    assert get_scan_xsi_types_from_experiment_response(_experiment_response(())) == {}


@pytest.mark.parametrize("response", [{}, {"items": []}, {"items": None}])
def test_get_scan_xsi_types_refuses_a_response_that_is_not_an_experiment_document(response):
    with pytest.raises(Exception, match="Failed to parse XNAT experiment data JSON"):
        get_scan_xsi_types_from_experiment_response(response)


# ── xnat_kept_existing_file ──


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        (_DUPLICATES_BODY, "text/html", True),
        (b"", "text/plain", False),
        (b"not json", "text/html", False),
        (b"{}", "application/json", False),
        (b'{"duplicates": []}', "text/html", False),
        (b'["scan.nii"]', "application/json", False),
    ],
)
def test_xnat_kept_existing_file_parses_the_body_whatever_the_content_type(body, content_type, expected):
    assert xnat_kept_existing_file(_xnat_put_response(body=body, content_type=content_type)) is expected


# ── upload_file_to_xnat ──


def test_upload_nonexistent_file_to_xnat(headers):
    file_path = "tests/data/test.dcm"

    with pytest.raises(FileNotFoundError) as exc_info:
        upload_file_to_xnat(
            project_id="test",
            subject_id="XNAT_S00002",
            experiment_id_or_label="XNAT_E00002",
            scan_id="1_2_826_0_1_3680043_8_274_1_1_8323329_1189734_1740750875_622789",
            resource_id="DICOM",
            file_path=file_path,
            exist_ok=True,
            headers=headers,
        )

    assert str(exc_info.value) == f"[Errno 2] No such file or directory: '{file_path}'"


@patch("imaging_api.services.upload.requests.put")
def test_upload_file_to_xnat(mock_put, headers):
    mock_put.return_value = _xnat_put_response()

    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(b"Hello, world!")
        temp_file_path = temp_file.name

    try:
        uploaded_file = upload_file_to_xnat(
            project_id="test",
            subject_id="XNAT_S00002",
            experiment_id_or_label="XNAT_E00002",
            scan_id="TEST_SCAN",
            resource_id="TEST_FILES",
            file_path=temp_file_path,
            exist_ok=False,
            headers=headers,
        )
        temp_file_name = os.path.basename(temp_file_path)
        assert (
            uploaded_file == f"{XNAT_URL}/data/projects/test/subjects/XNAT_S00002/"
            f"experiments/XNAT_E00002/scans/TEST_SCAN/"
            f"resources/TEST_FILES/files/{temp_file_name}?inbody=true"
        )
    finally:
        os.remove(temp_file_path)


@patch("imaging_api.services.upload.requests.put")
def test_upload_file_to_xnat_201_is_success(mock_put, headers):
    mock_put.return_value = _xnat_put_response(status_code=201)

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"data")
        temp_path = f.name

    try:
        uploaded_file = upload_file_to_xnat(
            "PROJ", "SUBJ", "EXP", "SCAN1", "DICOM", temp_path, exist_ok=False, headers=headers
        )
        assert uploaded_file.endswith(f"/files/{os.path.basename(temp_path)}?inbody=true")
    finally:
        os.remove(temp_path)


@pytest.mark.parametrize("status_code", [401, 403, 404, 413, 500])
@patch("imaging_api.services.upload.requests.put")
def test_upload_file_to_xnat_rejected_put_raises(mock_put, status_code, headers):
    # A PUT that XNAT rejects must raise rather than be reported as an uploaded file (#1387)
    mock_put.return_value = MagicMock(status_code=status_code, ok=False, text="rejected")

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"data")
        temp_path = f.name

    try:
        with pytest.raises(Exception, match=f"Error uploading file .*: {status_code} rejected"):
            upload_file_to_xnat("PROJ", "SUBJ", "EXP", "SCAN1", "DICOM", temp_path, exist_ok=False, headers=headers)
    finally:
        os.remove(temp_path)


@pytest.mark.parametrize(
    ("exist_ok", "expected_query"),
    [(True, "inbody=true&overwrite=true"), (False, "inbody=true")],
)
def test_upload_sends_overwrite_only_when_exist_ok_and_no_pre_check(
    exist_ok, expected_query, tmp_path, sent_xnat_requests
):
    file_path = tmp_path / "scan.nii"
    file_path.write_bytes(b"image-data")

    uploaded_url = upload_file_to_xnat(
        "PROJ", "SUBJ", "EXP", "SCAN1", "NIFTI", str(file_path), exist_ok=exist_ok, headers={}
    )

    assert [request.method for request in sent_xnat_requests] == ["PUT"]
    assert urlsplit(sent_xnat_requests[0].url).query == expected_query
    assert uploaded_url == sent_xnat_requests[0].url


@patch("imaging_api.services.upload.requests.put")
def test_upload_file_exist_ok_false_already_exists(mock_put, headers, tmp_path):
    mock_put.return_value = _xnat_put_response(body=_DUPLICATES_BODY, content_type="text/html")
    file_path = tmp_path / "scan.nii"
    file_path.write_bytes(b"data")

    with pytest.raises(AlreadyExistsError, match="File already exists on XNAT"):
        upload_file_to_xnat("PROJ", "SUBJ", "EXP", "SCAN1", "DICOM", str(file_path), exist_ok=False, headers=headers)

    assert "overwrite" not in urlsplit(mock_put.call_args.args[0]).query


# ── upload_data_to_xnat ──


class TestUploadDataToXnat:
    @pytest.mark.asyncio
    @patch("imaging_api.services.upload.upload_file_to_xnat")
    @patch("imaging_api.services.upload.create_xnat_scan")
    @patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.upload.get_experiment", return_value=_experiment_response())
    @patch("imaging_api.services.upload.get_project_from_central_hub_project_id")
    async def test_success(self, mock_get_project, mock_get_exp, mock_get_subj, mock_create_scan, mock_upload, headers):
        mock_get_project.return_value = MagicMock(ID="PROJ1")
        mock_upload.return_value = f"{XNAT_URL}/data/projects/PROJ1/file.nii"

        with tempfile.TemporaryDirectory() as tmp_dir:
            upload_dir = os.path.join(tmp_dir, "net1", "upload")
            os.makedirs(upload_dir)
            with open(os.path.join(upload_dir, "scan.nii"), "w") as f:
                f.write("nifti-data")

            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                result = await upload_data_to_xnat(
                    central_hub_project_id="hub-proj-1",
                    accession_id="ACC123",
                    net_id="net1",
                    scan_id="SCAN1",
                    resource_id="NIFTI",
                    files_relative_paths_to_upload=["scan.nii"],
                    exist_ok=False,
                    headers=headers,
                )

            assert len(result) == 1

    @staticmethod
    def _seed_upload_file(tmp_dir: str) -> None:
        upload_dir = os.path.join(tmp_dir, "net1", "upload")
        os.makedirs(upload_dir, exist_ok=True)
        with open(os.path.join(upload_dir, "scan.nii"), "w") as f:
            f.write("nifti-data")

    @staticmethod
    def _seed_download_sentinel(tmp_dir: str, net_id: str, project: str, accession: str) -> str:
        accession_dir = os.path.join(tmp_dir, net_id, project, accession)
        os.makedirs(accession_dir, exist_ok=True)
        sentinel = os.path.join(accession_dir, ".flip_complete-scan-NIFTI")
        with open(sentinel, "w"):
            pass
        return sentinel

    @pytest.mark.asyncio
    @patch("imaging_api.services.upload.upload_file_to_xnat", return_value="url")
    @patch("imaging_api.services.upload.create_xnat_scan")
    @patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.upload.get_experiment", return_value=_experiment_response())
    @patch("imaging_api.services.upload.get_project_from_central_hub_project_id")
    async def test_upload_invalidates_cached_downloads_across_nets(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_create_scan, mock_upload, headers
    ):
        """An upload changes the accession's XNAT content, so every net's cached download of
        (project, accession) must be invalidated; other accessions' caches must survive."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        with tempfile.TemporaryDirectory() as tmp_dir:
            self._seed_upload_file(tmp_dir)
            stale_net1 = self._seed_download_sentinel(tmp_dir, "net1", "hub-proj-1", "ACC123")
            stale_net2 = self._seed_download_sentinel(tmp_dir, "net2", "hub-proj-1", "ACC123")
            survivor = self._seed_download_sentinel(tmp_dir, "net1", "hub-proj-1", "ACC999")

            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                result = await upload_data_to_xnat(
                    central_hub_project_id="hub-proj-1",
                    accession_id="ACC123",
                    net_id="net1",
                    scan_id="SCAN1",
                    resource_id="NIFTI",
                    files_relative_paths_to_upload=["scan.nii"],
                    exist_ok=False,
                    headers=headers,
                )

            assert len(result) == 1
            assert not os.path.exists(stale_net1)
            assert not os.path.exists(stale_net2)
            assert os.path.exists(survivor)

    @pytest.mark.asyncio
    @patch("imaging_api.services.upload.upload_file_to_xnat", return_value="url")
    @patch("imaging_api.services.upload.create_xnat_scan")
    @patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.upload.get_experiment", return_value=_experiment_response())
    @patch("imaging_api.services.upload.get_project_from_central_hub_project_id")
    async def test_upload_with_no_cached_downloads_succeeds(
        self, mock_get_project, mock_get_exp, mock_get_subj, mock_create_scan, mock_upload, headers
    ):
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        with tempfile.TemporaryDirectory() as tmp_dir:
            self._seed_upload_file(tmp_dir)

            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                result = await upload_data_to_xnat(
                    central_hub_project_id="hub-proj-1",
                    accession_id="ACC123",
                    net_id="net1",
                    scan_id="SCAN1",
                    resource_id="NIFTI",
                    files_relative_paths_to_upload=["scan.nii"],
                    exist_ok=False,
                    headers=headers,
                )

            assert len(result) == 1

    @pytest.mark.asyncio
    @patch("imaging_api.services.upload.invalidate_accession", side_effect=OSError("stale NFS handle"))
    @patch("imaging_api.services.upload.upload_file_to_xnat", return_value="url")
    @patch("imaging_api.services.upload.create_xnat_scan")
    @patch("imaging_api.services.upload.get_subject_id_from_experiment_response", return_value="SUBJ1")
    @patch("imaging_api.services.upload.get_experiment", return_value=_experiment_response())
    @patch("imaging_api.services.upload.get_project_from_central_hub_project_id")
    async def test_invalidation_failure_does_not_fail_completed_upload(
        self,
        mock_get_project,
        mock_get_exp,
        mock_get_subj,
        mock_create_scan,
        mock_upload,
        mock_invalidate,
        headers,
    ):
        """Invalidation is best-effort: the XNAT upload already succeeded, and failing the
        request would push the client into retrying a completed upload."""
        mock_get_project.return_value = MagicMock(ID="PROJ1")

        with tempfile.TemporaryDirectory() as tmp_dir:
            self._seed_upload_file(tmp_dir)

            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                result = await upload_data_to_xnat(
                    central_hub_project_id="hub-proj-1",
                    accession_id="ACC123",
                    net_id="net1",
                    scan_id="SCAN1",
                    resource_id="NIFTI",
                    files_relative_paths_to_upload=["scan.nii"],
                    exist_ok=False,
                    headers=headers,
                )

            assert len(result) == 1
            mock_invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_not_found(self, headers):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                with pytest.raises(FileNotFoundError, match="File not found"):
                    await upload_data_to_xnat(
                        central_hub_project_id="hub-proj-1",
                        accession_id="ACC123",
                        net_id="net1",
                        scan_id="SCAN1",
                        resource_id="NIFTI",
                        files_relative_paths_to_upload=["nonexistent.nii"],
                        exist_ok=False,
                        headers=headers,
                    )

    @pytest.mark.asyncio
    async def test_net_id_path_traversal_is_rejected(self, headers):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", tmp_dir):
                with pytest.raises(ValueError, match="Path traversal detected in net ID"):
                    await upload_data_to_xnat(
                        central_hub_project_id="hub-proj-1",
                        accession_id="ACC123",
                        net_id="../escape",
                        scan_id="SCAN1",
                        resource_id="NIFTI",
                        files_relative_paths_to_upload=["scan.nii"],
                        exist_ok=False,
                        headers=headers,
                    )


# ── upload_data_to_xnat: the requests it sends ──


_SCANS_PATH = "/data/projects/PROJ1/subjects/SUBJ1/experiments/ACC123/scans"


async def _upload_into(experiment_response: dict, scan_id: str, tmp_path, sent: list) -> list[tuple[str, str, str]]:
    """Upload one file into ``scan_id`` of the session that ``experiment_response`` describes.

    Returns:
        list[tuple[str, str, str]]: The method, path and query of every request sent to XNAT.
    """
    upload_dir = tmp_path / "net1" / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "scan.nii").write_bytes(b"nifti-data")
    with (
        patch("imaging_api.services.upload.BASE_IMAGES_DOWNLOAD_DIR", str(tmp_path)),
        patch(
            "imaging_api.services.upload.get_project_from_central_hub_project_id", return_value=MagicMock(ID="PROJ1")
        ),
        patch("imaging_api.services.upload.get_experiment", return_value=experiment_response),
    ):
        await upload_data_to_xnat("hub-proj-1", "ACC123", "net1", scan_id, "NIFTI", ["scan.nii"], False, {})
    urls = [(str(r.method), urlsplit(str(r.url))) for r in sent]
    return [(method, url.path, url.query) for method, url in urls]


@pytest.mark.asyncio
async def test_upload_into_an_existing_scan_sends_only_the_file_put(tmp_path, sent_xnat_requests):
    response = _experiment_response((("SCAN1", "xnat:dxScanData"),))

    sent = await _upload_into(response, "SCAN1", tmp_path, sent_xnat_requests)

    assert sent == [("PUT", f"{_SCANS_PATH}/SCAN1/resources/NIFTI/files/scan.nii", "inbody=true")]


@pytest.mark.asyncio
async def test_upload_into_a_new_scan_creates_it_with_the_type_of_the_sessions_first_scan(tmp_path, sent_xnat_requests):
    response = _experiment_response((("1", "xnat:ctScanData"), ("2", "xnat:otherDicomScanData")))

    sent = await _upload_into(response, "NEW", tmp_path, sent_xnat_requests)

    assert sent == [
        ("PUT", f"{_SCANS_PATH}/NEW", "xsiType=xnat%3ActScanData"),
        ("PUT", f"{_SCANS_PATH}/NEW/resources/NIFTI/files/scan.nii", "inbody=true"),
    ]


@pytest.mark.parametrize("sink", ["scan", "upload"])
def test_identifiers_stay_in_one_segment_at_http_boundary(sink, xnat_path_segment, tmp_path, sent_xnat_requests):
    raw, encoded = xnat_path_segment
    headers = {"X-Request-ID": "trace-1"}
    if sink == "scan":
        create_xnat_scan(raw, raw, raw, raw, "xnat:crScanData", headers)
        expected_path = f"/data/projects/{encoded}/subjects/{encoded}/experiments/{encoded}/scans/{encoded}"
        expected_query = "xsiType=xnat%3AcrScanData"
    else:
        file_path = tmp_path / "image ?#% α.nii.gz"
        file_path.write_bytes(b"image-data")
        uploaded_url = upload_file_to_xnat(raw, raw, raw, raw, raw, str(file_path), exist_ok=False, headers=headers)
        expected_path = (
            f"/data/projects/{encoded}/subjects/{encoded}/experiments/{encoded}/scans/{encoded}/resources/{encoded}"
            "/files/image%20%3F%23%25%20%CE%B1.nii.gz"
        )
        expected_query = "inbody=true"
        assert [request.method for request in sent_xnat_requests] == ["PUT"]
        assert uploaded_url == sent_xnat_requests[0].url

    assert len(sent_xnat_requests) == 1
    for request in sent_xnat_requests:
        parsed = urlsplit(request.url)
        assert parsed.path == expected_path
        assert parsed.query == expected_query
        assert parsed.fragment == ""
        assert request.headers["X-Request-ID"] == "trace-1"


@pytest.mark.parametrize("invalid", ["", ".", ".."])
@pytest.mark.parametrize(
    ("sink", "field"),
    [
        ("scan", "project_id"),
        ("scan", "subject_id"),
        ("scan", "experiment_id_or_label"),
        ("scan", "scan_id"),
        ("upload", "project_id"),
        ("upload", "subject_id"),
        ("upload", "experiment_id_or_label"),
        ("upload", "scan_id"),
        ("upload", "resource_id"),
    ],
)
def test_empty_and_dot_segments_fail_before_http(sink, field, invalid, tmp_path, sent_xnat_requests):
    kwargs = {
        "project_id": "PROJ",
        "subject_id": "SUBJ",
        "experiment_id_or_label": "EXP",
        "scan_id": "SCAN",
        "headers": {},
        field: invalid,
    }
    call = {"scan": create_xnat_scan, "upload": upload_file_to_xnat}[sink]
    if sink == "scan":
        kwargs["xsi_type"] = "xnat:crScanData"
    else:
        kwargs.setdefault("resource_id", "NIFTI")
    if sink == "upload":
        file_path = tmp_path / "image.nii.gz"
        file_path.write_bytes(b"image-data")
        kwargs.update(file_path=str(file_path), exist_ok=False)
    with pytest.raises(ValueError, match="empty or a dot-segment"):
        call(**kwargs)
    assert sent_xnat_requests == []


@pytest.mark.parametrize("file_path", ["", ".", ".."])
def test_unusable_upload_filename_fails_before_http(file_path, sent_xnat_requests):
    with pytest.raises(ValueError, match="empty or a dot-segment"):
        upload_file_to_xnat("PROJ", "SUBJ", "EXP", "SCAN", "NIFTI", file_path, exist_ok=False, headers={})
    assert sent_xnat_requests == []
