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


def test_fl_job_status_has_exactly_five_contract_values():
    assert {s.value for s in FLJobStatus} == {"PENDING", "RUNNING", "FINISHED", "FAILED", "STOPPED"}


def test_job_metadata_has_exactly_job_id_and_status():
    assert set(IJobMetaData.model_fields) == {"job_id", "status"}


@pytest.mark.parametrize("job_status", ["PENDING", "RUNNING", "FINISHED", "FAILED", "STOPPED"])
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
