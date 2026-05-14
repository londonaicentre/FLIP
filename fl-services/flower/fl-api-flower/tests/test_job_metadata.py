# Copyright (c) 2026 Flower Labs GmbH
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

import pytest

from fl_api.schemas import JobMetadata, JobStatus, normalize_status


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("pending", JobStatus.PENDING),
        ("starting", JobStatus.PENDING),
        ("running", JobStatus.RUNNING),
        ("finished:completed", JobStatus.FINISHED),
        ("finished:failed", JobStatus.FAILED),
        ("finished:stopped", JobStatus.STOPPED),
        ("stopped", JobStatus.STOPPED),
        ("RUNNING", JobStatus.RUNNING),
        ("  running  ", JobStatus.RUNNING),
        ("", JobStatus.FAILED),
    ],
)
def test_normalize_status_maps_flower_statuses(native, expected):
    assert normalize_status(native) == expected


def test_normalize_status_unknown_is_failed():
    assert normalize_status("some-future-status") == JobStatus.FAILED


def test_job_metadata_has_exactly_job_id_and_status():
    assert set(JobMetadata.model_fields) == {"job_id", "status"}


def test_job_status_has_exactly_five_contract_values():
    assert {s.value for s in JobStatus} == {"PENDING", "RUNNING", "FINISHED", "FAILED", "STOPPED"}
