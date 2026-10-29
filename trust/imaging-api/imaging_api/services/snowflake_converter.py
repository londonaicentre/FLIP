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
"""DICOM to NIfTI conversion through Snowpark Container Services (FLIP#1583).

The Snowflake half of the converter seam. On Compose and Kubernetes nothing here is
imported: ``Settings.CONVERTER_BACKEND`` defaults to ``container-service`` and XNAT's
Container Service plugin launches dcm2niix exactly as before. On SPCS there is no Docker
socket, no swarm and no Kubernetes backend for that plugin to drive, so conversion goes
the only way it can:

1. ``CALL APP.CONVERT_SCAN(project, session, scan)``. An owner's-rights procedure owned
   by ``FLIP_<CODE>_CONVERTER`` (Terraform, FLIP#1581), because launching a job needs
   CREATE SERVICE and a service's OAuth token carries the role that OWNS it: granting the
   services role CREATE SERVICE would let the fl-client, which runs researcher code, start
   arbitrary containers. The procedure takes three identifiers, refuses anything outside
   ``[A-Za-z0-9_-]``, and launches a specification fixed in Terraform. Image, pool,
   command and mounts are not parameters.
2. Poll ``APP.CONVERT_STATUS(project, session, scan)``, which returns the job's
   ``SYSTEM$GET_SERVICE_STATUS`` JSON. ``'[]'`` means the job is not scheduled yet, which
   is PENDING and not "nothing happened" (FLIP#1578).
3. Read the output from this container's own mount of the CONVERTER_IO stage, at
   ``<CONVERTER_IO_DIR>/<project>/<session>/<scan>/``, and register it in XNAT over REST
   as a NIFTI resource, which is what Container Service's upload step did before.

The credential is the OAuth session token SPCS mounts at ``SNOWFLAKE_TOKEN_FILE``. There
is no password on this path, and nothing here ever logs the token.

The ``session`` identifier is the XNAT session LABEL, not the experiment id: XNAT's
archive tree is ``<project>/arc001/<label>/SCANS/<scan>/DICOM`` and that is the path the
procedure mounts. The experiment id is what the REST registration in step 3 addresses.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from imaging_api.config import Settings, get_settings
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import XNAT_TRANSFER_TIMEOUT, xnat_url

#: The resource label the NIfTI is registered under, and the one reconvert_nifti looks for
#: when it decides a scan has been converted. Must stay in step with
#: ``services.projects.DCM2NIIX_OUTPUT_RESOURCE``.
NIFTI_RESOURCE = "NIFTI"

#: The same shape the procedures enforce. Checked here too so a bad identifier fails with a
#: message naming the value, rather than as a bare 'rejected' from several layers down.
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCAN_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

#: What dcm2niix leaves behind. Everything else in the output directory (a stray log, a
#: partial write) is ignored rather than uploaded into the archive.
NIFTI_SUFFIXES = (".nii.gz", ".nii", ".json", ".bval", ".bvec")

#: Conversion states, as this module reports them.
PENDING, RUNNING, DONE, FAILED = "PENDING", "RUNNING", "DONE", "FAILED"


class ConversionError(Exception):
    """The Snowflake conversion path could not do what was asked of it."""


@dataclass(frozen=True)
class ScanRef:
    """One scan, in the three forms the conversion path needs it in.

    Attributes:
        project (str): The XNAT project ID, which is the archive's first path segment.
        session (str): The XNAT session LABEL, which is the archive directory name.
        scan (str): The scan ID.
        experiment (str): The XNAT experiment ID, which REST addresses the resource by.
    """

    project: str
    session: str
    scan: str
    experiment: str

    def validate(self) -> None:
        """Refuse an identifier the procedures would reject, naming which one.

        Raises:
            ConversionError: Any of the three identifiers is not a bare identifier.
        """
        for name, value, pattern in (
            ("project", self.project, IDENTIFIER),
            ("session", self.session, IDENTIFIER),
            ("scan", self.scan, SCAN_IDENTIFIER),
        ):
            if not pattern.match(value or ""):
                raise ConversionError(
                    f"{name} {value!r} is not usable as a conversion identifier: "
                    f"APP.CONVERT_SCAN accepts {pattern.pattern}"
                )

    def __str__(self) -> str:
        return f"{self.project}/{self.session}/{self.scan}"


def _connect(settings: Settings) -> Any:
    """Open a Snowflake session with the SPCS OAuth token.

    Imported here rather than at module scope so that a Compose or Kubernetes trust, which
    never takes this path, does not pay for (or fail on) the connector at start-up.

    Raises:
        ConversionError: The token file is missing, which is what an imaging-api deployed
            without ``mountSessionToken`` looks like.
    """
    import snowflake.connector  # noqa: PLC0415

    token_file = Path(settings.SNOWFLAKE_TOKEN_FILE)
    if not token_file.exists():
        raise ConversionError(
            f"no Snowflake session token at {token_file}. The service spec must set "
            "mountSessionToken: true; see trust/deploy/snowflake/README.md."
        )
    return snowflake.connector.connect(
        host=settings.SNOWFLAKE_HOST or None,
        account=settings.SNOWFLAKE_ACCOUNT or None,
        token=token_file.read_text().strip(),
        # The token is an OAuth token, not a password. Any other authenticator here would
        # send it as one and be refused.
        authenticator="oauth",
        database=settings.SNOWFLAKE_DATABASE or None,
        schema=settings.SNOWFLAKE_SCHEMA or None,
        client_session_keep_alive=True,
    )


def interpret_status(raw: str) -> str:
    """Turn ``APP.CONVERT_STATUS``'s answer into one of PENDING/RUNNING/DONE/FAILED.

    Args:
        raw (str): The procedure's return value: SPCS status JSON, ``'[]'`` when the job
            is not scheduled yet, or ``'rejected'`` for a bad identifier.

    Returns:
        str: The conversion's state. An empty or unparseable status is PENDING, never
        FAILED: treating "I cannot see it yet" as a failure is how a healthy conversion
        gets relaunched in a loop.

    Raises:
        ConversionError: The procedure rejected the identifiers.
    """
    text = (raw or "").strip()
    if text == "rejected":
        raise ConversionError("APP.CONVERT_STATUS rejected the identifiers")
    if not text or text == "[]":
        return PENDING
    try:
        entries = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("CONVERT_STATUS returned something that is not JSON; treating as pending")
        return PENDING
    if isinstance(entries, dict):
        entries = [entries]
    states = [str(entry.get("status") or "").upper() for entry in entries if isinstance(entry, dict)]
    if not states:
        return PENDING
    # One container per job, but read defensively: any FAILED fails the scan, and the
    # conversion is only DONE once nothing is still running.
    if FAILED in states:
        return FAILED
    if all(state == DONE for state in states):
        return DONE
    return RUNNING


class SnowflakeConverter:
    """Launches, follows and registers one trust's DICOM to NIfTI conversions."""

    def __init__(
        self,
        settings: Settings | None = None,
        connect: Callable[[Settings], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a converter.

        Args:
            settings (Settings | None): Overrides the process settings (tests).
            connect (Callable[[Settings], Any] | None): Opens the Snowflake session.
            sleep (Callable[[float], None]): Waits between status polls.
            clock (Callable[[], float]): Monotonic seconds, for the per-scan timeout.
        """
        self.settings = settings or get_settings()
        self._connect = connect or _connect
        self._sleep = sleep
        self._clock = clock
        self._connection: Any | None = None

    # ── Snowflake ────────────────────────────────────────────────────────────
    @property
    def connection(self) -> Any:
        """The Snowflake session, opened on first use and reused afterwards."""
        if self._connection is None:
            self._connection = self._connect(self.settings)
        return self._connection

    def close(self) -> None:
        """Close the Snowflake session, if one was ever opened."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _call(self, procedure: str, scan: ScanRef) -> str:
        """CALL one converter procedure for one scan and return its single value.

        Bound parameters, never interpolation: the procedures validate their arguments,
        but a value spliced into the CALL text would never reach that validation.

        Raises:
            ConversionError: The procedure returned no row.
        """
        qualified = f"{self.settings.SNOWFLAKE_DATABASE}.{self.settings.SNOWFLAKE_SCHEMA}.{procedure}"
        with self.connection.cursor() as cursor:
            cursor.execute(f"CALL {qualified}(%s, %s, %s)", (scan.project, scan.session, scan.scan))
            row = cursor.fetchone()
        if not row:
            raise ConversionError(f"{qualified} returned no row for {scan}")
        return str(row[0])

    def launch(self, scan: ScanRef) -> str:
        """Launch dcm2niix for one scan.

        Returns:
            str: ``'launched'``.

        Raises:
            ConversionError: The identifiers were refused, so nothing was launched.
        """
        scan.validate()
        answer = self._call("CONVERT_SCAN", scan).strip()
        if answer != "launched":
            raise ConversionError(f"APP.CONVERT_SCAN answered {answer!r} for {scan}")
        logger.info("Launched dcm2niix for %s", scan)
        return answer

    def status(self, scan: ScanRef) -> str:
        """The conversion's state: PENDING, RUNNING, DONE or FAILED."""
        return interpret_status(self._call("CONVERT_STATUS", scan))

    def wait(self, scan: ScanRef, timeout_seconds: int | None = None) -> str:
        """Poll until the conversion is terminal or the budget runs out.

        Returns:
            str: DONE, FAILED, or the last non-terminal state when the budget expired.
        """
        budget = self.settings.CONVERT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        deadline = self._clock() + budget
        state = PENDING
        while self._clock() < deadline:
            state = self.status(scan)
            if state in (DONE, FAILED):
                return state
            self._sleep(self.settings.CONVERT_POLL_SECONDS)
        logger.warning("Conversion of %s is still %s after %ss", scan, state, budget)
        return state

    # ── The converter's output ───────────────────────────────────────────────
    def output_dir(self, scan: ScanRef) -> Path:
        """Where the procedure told the job to write this scan's NIfTI."""
        return Path(self.settings.CONVERTER_IO_DIR) / scan.project / scan.session / scan.scan

    def outputs(self, scan: ScanRef) -> list[Path]:
        """The converter's files for one scan, name-ordered.

        Only the suffixes dcm2niix produces, and only regular files directly under the
        scan's own directory: the output tree is shared storage, and uploading whatever
        happens to be there would put unreviewed bytes into the archive.
        """
        directory = self.output_dir(scan)
        if not directory.is_dir():
            return []
        return sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.name.endswith(NIFTI_SUFFIXES) and not path.name.startswith(".")
        )

    # ── XNAT ─────────────────────────────────────────────────────────────────
    def register(self, scan: ScanRef, files: Iterable[Path], headers: dict[str, str]) -> int:
        """Create the NIFTI resource on the scan and upload the converter's files into it.

        This is the step XNAT's Container Service used to perform from its own upload
        mount. Two calls per scan plus one per file, exactly as the container service's
        output handler issued them, so the archive ends up in the same shape:

        * ``PUT /data/experiments/{eid}/scans/{scan}/resources/NIFTI?format=NIFTI&content=NIFTI_RAW``
        * ``PUT .../resources/NIFTI/files/{name}?inbody=true``

        Args:
            scan (ScanRef): The scan whose resource this is.
            files (Iterable[Path]): The converter's output files.
            headers (dict[str, str]): XNAT authentication headers.

        Returns:
            int: How many files were uploaded.

        Raises:
            ConversionError: XNAT refused the resource or one of the files. The resource
                is left in place: a partial upload that is visible is better than one that
                is silently rolled back, and re-running overwrites it.
        """
        uploads = list(files)
        if not uploads:
            raise ConversionError(f"{scan}: the converter produced no NIfTI to register")

        # 200 or 201 both mean "the resource is there"; XNAT answers 409 when it already
        # is, which is the re-run case and not an error.
        resource_url = xnat_url(
            "data",
            "experiments",
            scan.experiment,
            "scans",
            scan.scan,
            "resources",
            NIFTI_RESOURCE,
            query={"format": NIFTI_RESOURCE, "content": "NIFTI_RAW"},
        )
        response = requests.put(resource_url, headers=headers, timeout=XNAT_TRANSFER_TIMEOUT)
        if response.status_code not in (200, 201, 409):
            raise ConversionError(
                f"{scan}: creating the {NIFTI_RESOURCE} resource failed: {response.status_code} - {response.text[:500]}"
            )

        # The header carries a JSON content type for every other XNAT call this service
        # makes; these bodies are raw bytes, so it has to go or XNAT stores a file it then
        # cannot serve.
        file_headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
        for path in uploads:
            file_url = xnat_url(
                "data",
                "experiments",
                scan.experiment,
                "scans",
                scan.scan,
                "resources",
                NIFTI_RESOURCE,
                "files",
                path.name,
                query={"inbody": "true", "overwrite": "true"},
            )
            with path.open("rb") as handle:
                uploaded = requests.put(file_url, headers=file_headers, data=handle, timeout=XNAT_TRANSFER_TIMEOUT)
            if uploaded.status_code not in (200, 201):
                raise ConversionError(
                    f"{scan}: uploading {path.name} failed: {uploaded.status_code} - {uploaded.text[:500]}"
                )
        logger.info("Registered %d NIfTI file(s) on %s", len(uploads), scan)
        return len(uploads)

    def collect(self, scan: ScanRef, headers: dict[str, str]) -> int:
        """Register a finished conversion's output in XNAT.

        Returns:
            int: How many files were uploaded.

        Raises:
            ConversionError: The job finished without producing anything, or XNAT refused
                the upload.
        """
        return self.register(scan, self.outputs(scan), headers)


def enabled(settings: Settings | None = None) -> bool:
    """Whether this trust converts through Snowflake rather than Container Service."""
    return (settings or get_settings()).CONVERTER_BACKEND == "snowflake"
