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

"""Where a study's scan files sit in XNAT's archive, and where a download would put them (FLIP#1857).

For a client that mounts its project's directory of XNAT's archive instead of receiving a copy: it reads the files
in place and lays them out as a download does. The two layouts differ, and the scan's type, part of the download's
folder name, is in XNAT's database only:

- archive:  ``arc001/<session>/SCANS/<scan id>/<resource>/<file>``, under ``<archive>/<project>/``
- download: ``<session>/scans/<scan id>-<type>/resources/<resource>/files/<file>``
"""

import re
from pathlib import PurePosixPath
from typing import Any

import requests

from imaging_api.services.projects import get_experiment, get_experiment_field, get_project_from_central_hub_project_id
from imaging_api.utils.exceptions import NotFoundError, XnatFetchError
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

_NOT_A_WORD_CHARACTER = re.compile(r"\W", re.ASCII)  # Java's \W, which XNAT replaces with "_"


def scan_folder(scan_id: str, scan_type: str) -> str:
    """The scan's folder in a ``structure=default`` download, as XNAT names it (``RestFileUtils.getReMaps``)."""
    return f"{scan_id}-{_NOT_A_WORD_CHARACTER.sub('_', scan_type)}" if scan_type else scan_id


def _scan_types(experiment_response: dict[str, Any]) -> dict[str, str]:
    """Each scan's type, by scan ID, from the XNAT experiment response JSON."""
    return {
        str(scan["data_fields"]["ID"]): str(scan["data_fields"].get("type") or "")
        for child in experiment_response["items"][0].get("children", [])
        if child["field"] == "scans/scan"
        for scan in child["items"]
    }


def study_layout(
    central_hub_project_id: str, accession_id: str, resource_type: str, headers: dict[str, str]
) -> list[dict[str, str]]:
    """
    Lists the scan files a download of ``resource_type`` would hold, each with its place in the archive.

    Args:
        central_hub_project_id (str): Central Hub project ID. Corresponds to XNAT secondary ID.
        accession_id (str): Corresponds to XNAT experiment label, and to the session's archive directory.
        resource_type (str): XNAT resource type, one of DICOM, NIFTI, SEG or ALL.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[dict[str, str]]: One ``{"path", "archive"}`` per file: ``path`` in the download layout, rooted at the
        accession id, and ``archive`` relative to the project's own directory of XNAT's archive.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the project or the experiment is not found, or it has no
            such files.
        imaging_api.utils.exceptions.XnatFetchError: If XNAT's listing fails or names a file outside the session's
            scans in this project's archive.
        requests.RequestException: If a request fails or times out.
    """
    project_id = get_project_from_central_hub_project_id(central_hub_project_id, headers).ID
    experiment = get_experiment(project_id=project_id, experiment_id_or_label=accession_id, headers=headers)
    scan_types = _scan_types(experiment)

    segments: tuple[str, ...] = ("data", "experiments", get_experiment_field(experiment, "ID"), "scans", "ALL")
    if resource_type.upper() != "ALL":  # as the download does: XNAT has no resource labelled ALL
        segments += ("resources", resource_type)
    url = xnat_url(*segments, "files", query={"format": "json", "locator": "absolutePath"})
    response = requests.get(url, headers=headers, timeout=XNAT_REQUEST_TIMEOUT)
    if response.status_code == 404:
        raise NotFoundError(f"No {resource_type} files in XNAT for {accession_id}")
    if response.status_code != 200:
        raise XnatFetchError(f"XNAT file listing failed: {response.status_code} - {response.text[:200]}")

    files = []
    for row in response.json()["ResultSet"]["Result"]:
        archive = str(row["absolutePath"]).partition(f"/{project_id}/")[2]
        parts = PurePosixPath(archive).parts
        # <arc>/<session>/SCANS/<scan id>/<resource directory>/<file, which may be nested>
        if len(parts) < 6 or parts[1] != accession_id or parts[2] != "SCANS" or ".." in parts:
            raise XnatFetchError(f"XNAT lists a file outside {accession_id}'s scans in project {project_id}")
        scan_id, name = parts[3], "/".join(parts[5:])
        folder = scan_folder(scan_id, scan_types.get(scan_id, ""))
        label = row.get("collection") or row["cat_ID"]
        files.append({"path": f"{accession_id}/scans/{folder}/resources/{label}/files/{name}", "archive": archive})
    if not files:
        raise NotFoundError(f"No {resource_type} files in XNAT for {accession_id}")
    return files
