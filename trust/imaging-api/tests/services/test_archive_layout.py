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

"""Where a study's files sit in XNAT's archive and in a download (FLIP#1857)."""

from unittest.mock import MagicMock, patch

import pytest

from imaging_api.services.archive_layout import scan_folder, study_layout
from imaging_api.utils.exceptions import NotFoundError, XnatFetchError

PROJECT = "7e2cc411-ba93-48af-8474-79e56c266013"
ARCHIVE = f"/data/xnat/archive/{PROJECT}/arc001/FAK03765414"


def _experiment(scans: dict[str, str]) -> dict:
    """XNAT's experiment response, as far as the layout reads it."""
    items = [{"data_fields": {"ID": scan_id, "type": scan_type}, "meta": {}} for scan_id, scan_type in scans.items()]
    return {"items": [{"data_fields": {"ID": "XNAT_E00601"}, "children": [{"field": "scans/scan", "items": items}]}]}


def _listing(*rows: tuple[str, str, str]) -> MagicMock:
    """XNAT's file listing with ``locator=absolutePath``: (resource label, resource id, absolute path)."""
    result = [
        {"collection": label, "cat_ID": cat_id, "absolutePath": path, "Name": "n"} for label, cat_id, path in rows
    ]
    return MagicMock(status_code=200, json=lambda: {"ResultSet": {"Result": result}})


@pytest.fixture
def xnat():
    """A project and a session with scan 1 of type "unknown"; the test supplies the file listing."""
    with (
        patch("imaging_api.services.archive_layout.get_project_from_central_hub_project_id") as project,
        patch("imaging_api.services.archive_layout.get_experiment", return_value=_experiment({"1": "unknown"})) as exp,
        patch("imaging_api.services.archive_layout.requests.get") as get,
    ):
        project.return_value.ID = PROJECT
        yield MagicMock(project=project, experiment=exp, get=get)


def test_each_file_is_named_in_the_archive_and_in_the_download_layout(xnat):
    """The rig's spleen session: what is on disk, and what XNAT's own zip of it holds."""
    xnat.get.return_value = _listing(
        ("NIFTI", "919", f"{ARCHIVE}/SCANS/1/NIFTI/input_000000_1.nii.gz"),
        ("NIFTI", "919", f"{ARCHIVE}/SCANS/1/NIFTI/label_000000_1.nii.gz"),
    )

    files = study_layout("hub-project", "FAK03765414", "NIFTI", {})

    assert files == [
        {
            "path": "FAK03765414/scans/1-unknown/resources/NIFTI/files/input_000000_1.nii.gz",
            "archive": "arc001/FAK03765414/SCANS/1/NIFTI/input_000000_1.nii.gz",
        },
        {
            "path": "FAK03765414/scans/1-unknown/resources/NIFTI/files/label_000000_1.nii.gz",
            "archive": "arc001/FAK03765414/SCANS/1/NIFTI/label_000000_1.nii.gz",
        },
    ]
    url = xnat.get.call_args.args[0]
    assert url.endswith(
        "/data/experiments/XNAT_E00601/scans/ALL/resources/NIFTI/files?format=json&locator=absolutePath"
    )
    assert xnat.experiment.call_args.kwargs == {
        "project_id": PROJECT,
        "experiment_id_or_label": "FAK03765414",
        "headers": {},
    }


def test_all_lists_every_resource_like_the_download(xnat):
    xnat.get.return_value = _listing(
        ("DICOM", "918", f"{ARCHIVE}/SCANS/1/DICOM/1-90-160nwt3.dcm"),
        ("NIFTI", "919", f"{ARCHIVE}/SCANS/1/NIFTI/nested/a.nii.gz"),
    )

    files = study_layout("hub-project", "FAK03765414", "ALL", {})

    assert [f["path"] for f in files] == [
        "FAK03765414/scans/1-unknown/resources/DICOM/files/1-90-160nwt3.dcm",
        "FAK03765414/scans/1-unknown/resources/NIFTI/files/nested/a.nii.gz",
    ]
    assert "/scans/ALL/files?" in xnat.get.call_args.args[0]


@pytest.mark.parametrize(
    ("scan_type", "folder"),
    [
        ("unknown", "7-unknown"),
        ("t2_tse_tra", "7-t2_tse_tra"),
        ("T2 W (axial).v2-A", "7-T2_W__axial__v2_A"),
        ('a/b\\c:d*e?f"g<h>i|j', "7-a_b_c_d_e_f_g_h_i_j"),
        ("ünï", "7-_n_"),
        ("", "7"),
    ],
)
def test_the_scan_folder_is_named_as_xnat_names_it(scan_type, folder):
    """XNAT: ``id + '-' + type.replaceAll("\\\\W", "_")`` when the scan has a type, else the id."""
    assert scan_folder("7", scan_type) == folder


def test_a_resource_without_a_label_is_named_by_its_id(xnat):
    xnat.get.return_value = _listing(("", "920", f"{ARCHIVE}/SCANS/1/920/a.bin"))

    [file] = study_layout("hub-project", "FAK03765414", "ALL", {})

    assert file["path"] == "FAK03765414/scans/1-unknown/resources/920/files/a.bin"


@pytest.mark.parametrize(
    "path",
    [
        "/data/xnat/archive/another-project/arc001/FAK03765414/SCANS/1/NIFTI/a.nii.gz",
        f"/data/xnat/archive/{PROJECT}/arc001/OTHER_SESSION/SCANS/1/NIFTI/a.nii.gz",
        f"{ARCHIVE}/ASSESSORS/seg/SEG/a.dcm",
        f"{ARCHIVE}/SCANS/1/NIFTI/../../../../other/a.nii.gz",
        f"{ARCHIVE}/SCANS/1/NIFTI",
    ],
)
def test_a_file_outside_the_sessions_scans_in_this_project_is_refused(xnat, path):
    xnat.get.return_value = _listing(("NIFTI", "919", path))

    with pytest.raises(XnatFetchError):
        study_layout("hub-project", "FAK03765414", "NIFTI", {})


@pytest.mark.parametrize("response", [MagicMock(status_code=404), _listing()])
def test_a_study_with_no_such_files_is_not_found(xnat, response):
    xnat.get.return_value = response

    with pytest.raises(NotFoundError):
        study_layout("hub-project", "FAK03765414", "SEG", {})


def test_a_failed_listing_is_an_xnat_error(xnat):
    xnat.get.return_value = MagicMock(status_code=500, text="boom")

    with pytest.raises(XnatFetchError, match="500"):
        study_layout("hub-project", "FAK03765414", "NIFTI", {})
