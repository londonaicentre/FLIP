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

"""Delivery of a job's hub credential to the FL server.

The hub mints one credential per job and sends it with the app. The job's server-side code presents it on
every callback about its model (``flip.core.standard``), so the FL API writes it where the FL server reads
and no trust does: ``JOB_CREDENTIAL_DIR``, a directory on the volume the two share, outside the app that is
deployed to the trusts.

Byte-identical in the FLARE and Flower fl-api copies — ``scripts/check_fl_api_validation_sync.sh``.
"""

import os
from pathlib import Path

from fastapi import HTTPException, status

JOB_CREDENTIAL_DIR_ENV = "JOB_CREDENTIAL_DIR"


def deliver_job_credential(model_id: str, credential: str | None) -> None:
    """Make ``credential`` the one job credential the FL server can read.

    Args:
        model_id (str): The hub's model id (a UUID, checked by the route), which names the file.
        credential (str | None): The job's credential. None from a hub that mints none; it checks none either.

    Raises:
        HTTPException: 500 when a credential was sent and ``JOB_CREDENTIAL_DIR`` is not set, since the hub
            would refuse everything the job reported.
    """
    if credential is None:
        return
    root = os.getenv(JOB_CREDENTIAL_DIR_ENV)
    if not root:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{JOB_CREDENTIAL_DIR_ENV} is not set: the job could not report to the hub.",
        )
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    # A net runs one job at a time, so any other credential here belongs to a job that has ended.
    for other in directory.iterdir():
        other.unlink()
    (directory / model_id).write_text(credential)
