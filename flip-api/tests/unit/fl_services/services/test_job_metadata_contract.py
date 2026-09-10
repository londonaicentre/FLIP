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

import pytest
from pydantic import ValidationError

from flip_api.domain.interfaces.fl import IJobMetaData
from flip_api.domain.schemas.status import FLJobStatus


def test_fl_job_status_has_exactly_six_contract_values():
    assert {s.value for s in FLJobStatus} == {"PENDING", "RUNNING", "FINISHED", "FAILED", "STOPPED", "UNKNOWN"}


def test_job_metadata_has_exactly_the_contract_fields():
    # Pinned deliberately: the contract is implemented independently by two FL API adapters,
    # so a field added on one side and not the other is invisible until it matters. Adding a
    # field here means adding it to both adapters' JobMetadata in the same PR.
    assert set(IJobMetaData.model_fields) == {"job_id", "status", "status_details"}


def test_status_details_is_optional_and_defaults_to_none():
    # Optional so an FL API image predating the field still validates against a newer hub —
    # unlike the UNKNOWN status value, this half of the contract is deploy-order-safe in both
    # directions (the hub also ignores extra fields, so a newer FL API is safe too).
    job = IJobMetaData.model_validate({"job_id": "abc", "status": "FAILED"})
    assert job.status_details is None


def test_status_details_is_carried_through_when_present():
    job = IJobMetaData.model_validate(
        {"job_id": "abc", "status": "FAILED", "status_details": "ServerApp failed with exception: boom"}
    )
    assert job.status_details == "ServerApp failed with exception: boom"


@pytest.mark.parametrize("job_status", ["PENDING", "RUNNING", "FINISHED", "FAILED", "STOPPED", "UNKNOWN"])
def test_job_metadata_accepts_every_contract_status(job_status):
    job = IJobMetaData.model_validate({"job_id": "abc", "status": job_status})
    assert job.status == FLJobStatus(job_status)


def test_job_metadata_rejects_unknown_status():
    with pytest.raises(ValidationError):
        IJobMetaData.model_validate({"job_id": "abc", "status": "running"})


def test_job_metadata_ignores_extra_fields():
    job = IJobMetaData.model_validate({"job_id": "abc", "status": "RUNNING", "job_name": "legacy"})
    assert job.job_id == "abc"
    assert not hasattr(job, "job_name")
