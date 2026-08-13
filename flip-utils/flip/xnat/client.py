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

"""A small XNAT REST client, scoped to what data enrichment needs.

Deliberately built on ``requests`` (already a core dependency) rather than a full XNAT client
library, so this module stays import-light inside the FL images that vendor ``flip``. The endpoint
shapes mirror those the Trust imaging-api already uses in
``imaging_api/services/upload.py`` and ``imaging_api/services/projects.py``.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from flip.exceptions import XnatError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60
"""Per-request timeout. Generous, because label volumes can be tens of megabytes."""


@dataclass(frozen=True)
class XnatScan:
    """One scan in an XNAT project, addressed by everything needed to write a resource file.

    Attributes:
        accession_id (str): The experiment label, which FLIP sets to the study's accession number.
        subject_id (str): The XNAT subject ID owning the experiment.
        experiment_id (str): The XNAT experiment ID.
        scan_id (str): The scan ID within the experiment.
    """

    accession_id: str
    subject_id: str
    experiment_id: str
    scan_id: str


class XnatClient:
    """Authenticated XNAT REST session exposing the handful of calls enrichment needs."""

    def __init__(self, server: str, user: str, password: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        """
        Args:
            server (str): Base URL of the XNAT server, e.g. ``http://10.0.0.5:8080``.
            user (str): XNAT username.
            password (str): XNAT password.
            timeout (int): Per-request timeout in seconds.
        """
        self.server = server.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.auth = (user, password)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "XnatClient":
        """Build a client from ``XNAT_HOST`` / ``XNAT_USER`` / ``XNAT_PASS``.

        These are the variables the XNAT Container Service injects into jobs, so the same script
        runs unchanged on a workstation and inside a container.

        Args:
            **kwargs: Forwarded to :class:`XnatClient` (e.g. ``timeout``).

        Returns:
            XnatClient: A configured client.

        Raises:
            XnatError: If any of the three variables is missing or empty.
        """
        server, user, password = os.getenv("XNAT_HOST"), os.getenv("XNAT_USER"), os.getenv("XNAT_PASS")
        missing = [
            name for name, value in (("XNAT_HOST", server), ("XNAT_USER", user), ("XNAT_PASS", password)) if not value
        ]
        if missing:
            raise XnatError(f"Missing XNAT credentials in environment: {', '.join(missing)}")
        # mypy: the `missing` check above proves these are non-empty strings.
        return cls(server=str(server), user=str(user), password=str(password), **kwargs)

    @classmethod
    def from_config_file(cls, path: str | Path, **kwargs: Any) -> "XnatClient":
        """Build a client from a JSON credentials file.

        The format matches the ``.xnat.cfg`` convention already used across FLIP's XNAT tooling::

            {"server": "http://...", "user": "...", "password": "..."}

        Args:
            path (str | Path): Path to the JSON credentials file.
            **kwargs: Forwarded to :class:`XnatClient` (e.g. ``timeout``).

        Returns:
            XnatClient: A configured client.

        Raises:
            XnatError: If the file is missing, unreadable, or lacks a required key.
        """
        try:
            config = json.loads(Path(path).read_text())
        except OSError as err:
            raise XnatError(f"Could not read XNAT credentials file {path}: {err}") from err
        except json.JSONDecodeError as err:
            raise XnatError(f"XNAT credentials file {path} is not valid JSON: {err}") from err

        missing = [key for key in ("server", "user", "password") if not config.get(key)]
        if missing:
            raise XnatError(f"XNAT credentials file {path} is missing key(s): {', '.join(missing)}")

        return cls(server=config["server"], user=config["user"], password=config["password"], **kwargs)

    def _get_result_rows(
        self,
        path: str,
        params: dict[str, str] | None = None,
        not_found_ok: bool = False,
    ) -> list[dict[str, Any]]:
        """GET an XNAT listing endpoint and return its ``ResultSet.Result`` rows.

        Args:
            path (str): Server-relative path, e.g. ``/data/projects``.
            params (dict[str, str] | None): Extra query parameters; ``format=json`` is always added.
            not_found_ok (bool): Treat a 404 as an empty result instead of an error. Only 404 —
                every other status still raises, so an expired session or a server fault can never
                masquerade as "this resource is empty".

        Returns:
            list[dict[str, Any]]: The result rows, empty if XNAT returned none.

        Raises:
            XnatError: If the request fails, or returns a non-200 status that ``not_found_ok``
                does not cover.
        """
        query = {"format": "json", **(params or {})}
        url = f"{self.server}{path}"
        try:
            response = self._session.get(url, params=query, timeout=self.timeout)
        except requests.RequestException as err:
            raise XnatError(f"XNAT request to {url} failed: {err}") from err

        if response.status_code == 404 and not_found_ok:
            return []
        if response.status_code != 200:
            raise XnatError(f"XNAT request to {url} returned {response.status_code}: {response.text}")

        rows = response.json().get("ResultSet", {}).get("Result", [])
        return list(rows)

    def resolve_project_by_flip_project_id(self, flip_project_id: str) -> str:
        """Find the XNAT project whose ``secondary_ID`` is the FLIP Central Hub project id.

        FLIP stores its own project id in the XNAT project's ``secondary_ID``; XNAT's primary id is
        unrelated and differs per Trust. Mirrors
        ``imaging_api/services/projects.py::get_project_from_central_hub_project_id``.

        Args:
            flip_project_id (str): The FLIP Central Hub project id (a UUID).

        Returns:
            str: The XNAT project id.

        Raises:
            XnatError: If no XNAT project carries that ``secondary_ID``.
        """
        rows = self._get_result_rows("/data/projects", {"columns": "ID,secondary_ID"})
        for row in rows:
            if row.get("secondary_ID") == flip_project_id:
                project_id = row.get("ID") or row.get("id")
                logger.info(f"Resolved FLIP project {flip_project_id} to XNAT project {project_id}")
                return str(project_id)

        raise XnatError(
            f"No XNAT project at {self.server} has secondary_ID={flip_project_id!r} "
            f"(checked {len(rows)} project(s)). Has the image pull for this project run at this Trust?"
        )

    def list_scans(self, project_id: str) -> list[XnatScan]:
        """List every scan in an XNAT project.

        Args:
            project_id (str): The XNAT project id.

        Returns:
            list[XnatScan]: One entry per scan, keyed by accession id (the experiment label).

        Raises:
            XnatError: If any underlying XNAT request fails.
        """
        experiments = self._get_result_rows(
            f"/data/projects/{project_id}/experiments",
            {"columns": "ID,label,subject_ID"},
        )

        scans: list[XnatScan] = []
        for experiment in experiments:
            experiment_id = str(experiment.get("ID", ""))
            for scan in self._get_result_rows(f"/data/experiments/{experiment_id}/scans"):
                scans.append(
                    XnatScan(
                        accession_id=str(experiment.get("label", "")),
                        subject_id=str(experiment.get("subject_ID", "")),
                        experiment_id=experiment_id,
                        scan_id=str(scan.get("ID", "")),
                    )
                )

        logger.info(f"Found {len(scans)} scan(s) across {len(experiments)} experiment(s) in project {project_id}")
        return scans

    def list_resource_files(self, scan: XnatScan, resource: str) -> list[str]:
        """List the filenames held in one of a scan's resources.

        A missing resource (404) is an expected, non-fatal state — the scan was never converted —
        and callers report it as a skip. Every other failure propagates: swallowing a 401 or a 500
        here would surface as "no image in resource", which reads as a benign
        conversion-not-run-yet skip and would hide an expired session or a broken server behind a
        clean-looking summary.

        Args:
            scan (XnatScan): The scan to inspect.
            resource (str): Resource name, e.g. ``NIFTI``.

        Returns:
            list[str]: Filenames, empty if the resource does not exist or holds nothing.

        Raises:
            XnatError: If the request fails for any reason other than the resource being absent.
        """
        rows = self._get_result_rows(
            f"/data/experiments/{scan.experiment_id}/scans/{scan.scan_id}/resources/{resource}/files",
            not_found_ok=True,
        )
        if not rows:
            logger.debug(f"No {resource} files for scan {scan.scan_id} of {scan.accession_id}")

        return [str(row.get("Name", "")) for row in rows if row.get("Name")]

    def upload_scan_resource_file(
        self,
        scan: XnatScan,
        project_id: str,
        resource: str,
        local_path: Path,
        target_filename: str,
        overwrite: bool = False,
    ) -> None:
        """Upload a file into a scan's resource.

        Args:
            scan (XnatScan): The destination scan.
            project_id (str): The XNAT project id.
            resource (str): Resource name, e.g. ``NIFTI``.
            local_path (Path): The file to upload.
            target_filename (str): Name the file should take in XNAT.
            overwrite (bool): Replace an existing file of that name. Off by default so a re-run
                never silently rewrites enrichment that is already in place.

        Raises:
            XnatError: If the upload fails, or if the file exists and ``overwrite`` is False.
        """
        url = (
            f"{self.server}/data/projects/{project_id}/subjects/{scan.subject_id}/"
            f"experiments/{scan.experiment_id}/scans/{scan.scan_id}/"
            f"resources/{resource}/files/{target_filename}?inbody=true"
        )

        try:
            with local_path.open("rb") as handle:
                response = self._session.put(url, data=handle, timeout=self.timeout)
        except OSError as err:
            raise XnatError(f"Could not read {local_path}: {err}") from err
        except requests.RequestException as err:
            raise XnatError(f"Upload of {local_path} to {url} failed: {err}") from err

        if response.status_code == 409 and not overwrite:
            raise XnatError(f"File already exists on XNAT: {target_filename}")
        if response.status_code not in (200, 201):
            raise XnatError(f"Upload of {local_path} returned {response.status_code}: {response.text}")

        logger.info(f"Uploaded {local_path.name} as {target_filename} to scan {scan.scan_id} ({scan.accession_id})")
