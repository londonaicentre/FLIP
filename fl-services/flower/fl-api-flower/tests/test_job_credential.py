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

"""Delivery of a job's hub credential to the directory the FL server reads."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from fl_api.utils.job_credential import deliver_job_credential

CREDENTIAL = "job-credential-sentinel"  # pragma: allowlist secret (synthetic sentinel)


def test_credential_is_written_under_the_model_id(tmp_path, monkeypatch):
    directory = tmp_path / "shared" / ".job-credentials"
    monkeypatch.setenv("JOB_CREDENTIAL_DIR", str(directory))
    model_id = str(uuid4())

    deliver_job_credential(model_id, CREDENTIAL)

    assert (directory / model_id).read_text() == CREDENTIAL


def test_the_next_job_leaves_no_earlier_credential_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_CREDENTIAL_DIR", str(tmp_path))
    earlier, model_id = str(uuid4()), str(uuid4())
    deliver_job_credential(earlier, "earlier")

    deliver_job_credential(model_id, CREDENTIAL)

    assert [path.name for path in tmp_path.iterdir()] == [model_id]


def test_a_hub_that_sends_no_credential_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_CREDENTIAL_DIR", str(tmp_path))
    deliver_job_credential(str(uuid4()), "earlier")

    deliver_job_credential(str(uuid4()), None)

    assert len(list(tmp_path.iterdir())) == 1


def test_a_credential_with_nowhere_to_go_fails_the_upload(monkeypatch):
    monkeypatch.delenv("JOB_CREDENTIAL_DIR", raising=False)

    with pytest.raises(HTTPException) as failed:
        deliver_job_credential(str(uuid4()), CREDENTIAL)

    assert failed.value.status_code == 500
    assert CREDENTIAL not in failed.value.detail
