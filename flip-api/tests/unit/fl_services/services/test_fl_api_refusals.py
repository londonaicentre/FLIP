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

"""Exercise real FL HTTP/service/feed paths with only external dependencies mocked."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from flip_api.db.models.main_models import FLJob, FLLogs
from flip_api.domain.interfaces.fl import INetDetails, IRequiredTrainingInformation
from flip_api.domain.schemas.status import ModelStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.fl_services.services import fl_scheduler_service, fl_service
from flip_api.fl_services.stop_training import stop_training
from flip_api.model_services.retrieve_logs_for_model import retrieve_logs_for_model_endpoint


@pytest.fixture(params=[FLBackend.NVFLARE, FLBackend.FLOWER])
def net(request):
    return INetDetails(name="net-1", endpoint=f"http://fl-api-{request.param}", fl_backend=request.param)


def assert_failure_in_feed(session, model_id, message):
    # Keep add_log real: assert on the stored FLLogs row and on the feed the researcher receives.
    session.add.assert_called_once()
    row = session.add.call_args.args[0]
    assert isinstance(row, FLLogs)
    assert row.model_id == model_id
    assert row.success is False
    session.commit.assert_called_once()
    logs_result, trusts_result = MagicMock(), MagicMock()
    logs_result.all.return_value = [row]
    trusts_result.all.return_value = []
    session.exec.side_effect = [MagicMock(), logs_result, trusts_result]
    with (
        patch("flip_api.model_services.retrieve_logs_for_model.can_access_model", return_value=True),
        patch(
            "flip_api.model_services.retrieve_logs_for_model.get_model_status", return_value=MagicMock(deleted=False)
        ),
    ):
        feed = retrieve_logs_for_model_endpoint(model_id, session, uuid4())
    assert len(feed) == 1
    assert feed[0].log == message
    assert "fl-api" not in feed[0].log
    assert "private" not in feed[0].log
    assert "X-Amz" not in feed[0].log
    assert feed[0].success is False


@pytest.mark.parametrize("failed_path", ["/upload_app", "/submit_job"])
@pytest.mark.parametrize(
    "body",
    [
        b'{"detail": "private path /app/src", "refusal": "No participating trusts in the upload request"}',
        b"",
        b"not JSON",
        b'{"refusal":',
        b'{"refusal": " "}',
        b'{"refusal": null}',
        b'{"refusal": 12}',
        b"[]",
        b'{"detail": "https://s3.internal/?X-Amz-Credential=private&X-Amz-Signature=private"}',
    ],
)
def test_training_refusal_reaches_activity_feed(net, failed_path, body):
    model_id, job_id = uuid4(), uuid4()
    session = MagicMock()
    session.get.return_value = FLJob(id=job_id, model_id=model_id)
    session.exec.return_value.all.return_value = ["Trust_1"]
    refused_response = None

    def respond(method, url, **kwargs):
        nonlocal refused_response
        request = httpx.Request(method, url)
        if request.url.path.startswith(failed_path):
            refused_response = httpx.Response(400, content=body, request=request)
            return refused_response
        if request.url.path == "/check_client_status":
            return httpx.Response(200, json=[{"name": "Trust_1", "status": "CONNECTED"}], request=request)
        return httpx.Response(200, json="job-1", request=request)

    with (
        patch.object(httpx.Client, "request", side_effect=respond),
        patch.object(fl_scheduler_service, "_raise_if_job_aborted"),
        patch.object(fl_service, "_raise_if_job_aborted"),
        patch.object(fl_scheduler_service, "get_net_by_model_id", return_value=net),
        patch.object(fl_scheduler_service, "bundle_nvflare_application", return_value="s3://bundle"),
        patch.object(fl_scheduler_service, "bundle_flower_application", return_value="s3://bundle"),
        patch.object(fl_scheduler_service, "get_bundle_urls", return_value=["http://bundle/app.zip"]),
        patch.object(
            fl_scheduler_service,
            "get_required_training_details",
            return_value=IRequiredTrainingInformation(project_id=str(uuid4()), cohort_query="SELECT 1"),
        ),
        patch.object(fl_scheduler_service, "remove_job") as remove_job,
        patch.object(fl_scheduler_service, "update_model_status") as update_status,
        patch.object(fl_scheduler_service, "release_scheduler_for_model") as release,
        patch.object(fl_scheduler_service.logger, "exception") as logged,
        pytest.raises(httpx.HTTPStatusError) as caught,
    ):
        fl_scheduler_service.prepare_and_start_training(model_id, job_id, [uuid4()], session)

    assert caught.value.response is refused_response
    assert caught.value.request.url.path.startswith(failed_path)
    message = (
        "The FL server refused the job: No participating trusts in the upload request"
        if b"No participating trusts" in body
        else "The FL server refused the job (HTTP 400)."
    )
    assert "400 Bad Request" in str(caught.value)
    assert "No participating trusts" not in str(caught.value)
    assert "private" not in str(caught.value)
    assert_failure_in_feed(session, model_id, message)
    logged.assert_called_once_with("Failed to start training for model %s (job %s)", model_id, job_id)
    assert "private" not in logged.call_args.args[0]
    remove_job.assert_called_once_with(job_id, session)
    update_status.assert_called_once_with(model_id, ModelStatus.ERROR, session)
    release.assert_called_once_with(model_id, session)


@pytest.mark.parametrize(
    ("failure", "safe_message"),
    [
        (RuntimeError("http://private-fl-host:8000/upload_app"), None),
        (
            ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "private-bucket/path arn:aws:iam::123:role/private"}},
                "ListObjectsV2",
            ),
            None,
        ),
        (SQLAlchemyError("SELECT * FROM private_table WHERE token = 'private-value'"), None),
        (ValueError("Clients unavailable: private-bucket"), None),
        (fl_service.ClientAvailabilityError("Clients unavailable: Trust_1"), "Clients unavailable: Trust_1"),
        (fl_service.UnknownJobTypeError("Unknown job_type: example"), "Unknown job_type: example"),
    ],
    ids=["internal-host", "s3", "sql", "unmarked-value-error", "client-availability", "job-type"],
)
def test_scheduler_failure_feed_allowlist(net, caplog, failure, safe_message):
    model_id, job_id = uuid4(), uuid4()
    session = MagicMock()
    with (
        patch.object(fl_scheduler_service, "_raise_if_job_aborted"),
        patch.object(fl_scheduler_service, "get_net_by_model_id", return_value=net),
        patch.object(fl_scheduler_service, "bundle_nvflare_application", side_effect=failure),
        patch.object(fl_scheduler_service, "bundle_flower_application", side_effect=failure),
        patch.object(fl_scheduler_service, "remove_job") as remove_job,
        patch.object(fl_scheduler_service, "update_model_status") as update_status,
        patch.object(fl_scheduler_service, "release_scheduler_for_model") as release,
        pytest.raises(type(failure)) as caught,
    ):
        fl_scheduler_service.prepare_and_start_training(model_id, job_id, [uuid4()], session)

    assert caught.value is failure
    message = safe_message or f"Training could not be started; the platform team has the details (job {job_id})."
    assert_failure_in_feed(session, model_id, message)
    record = next(r for r in caplog.records if r.msg == "Failed to start training for model %s (job %s)")
    assert record.exc_info[1] is failure
    assert str(model_id) in record.getMessage()
    assert str(job_id) in record.getMessage()
    remove_job.assert_called_once_with(job_id, session)
    update_status.assert_called_once_with(model_id, ModelStatus.ERROR, session)
    release.assert_called_once_with(model_id, session)


@pytest.mark.parametrize("failed_path", ["/list_jobs", "/abort_job"])
@pytest.mark.parametrize("status_code", [400, 500])
def test_abort_refusal_reaches_feed_and_router_stays_sanitized(net, failed_path, status_code):
    model_id = uuid4()
    session = MagicMock()
    refusal = "Job cannot be aborted in this state" if status_code == 400 else "private server exception"
    refused_response = None

    def respond(method, url, **kwargs):
        nonlocal refused_response
        upstream_request = httpx.Request(method, url)
        if upstream_request.url.path.startswith(failed_path):
            refused_response = httpx.Response(
                status_code, json={"detail": "private server exception", "refusal": refusal}, request=upstream_request
            )
            return refused_response
        body = (
            [{"job_id": "job-1", "status": "RUNNING"}]
            if upstream_request.url.path == "/list_jobs"
            else {"status": "started"}
        )
        return httpx.Response(200, json=body, request=upstream_request)

    with (
        patch.object(httpx.Client, "request", side_effect=respond),
        patch.object(fl_scheduler_service, "get_net_by_model_id", return_value=net),
        patch.object(fl_scheduler_service, "remove_job_from_queue") as dequeue,
        patch.object(fl_scheduler_service, "release_scheduler_for_model") as release,
        patch.object(fl_service, "get_fl_backend_job_id_by_model_id", return_value="job-1"),
        patch("flip_api.fl_services.stop_training.can_modify_model", return_value=True),
        patch("flip_api.fl_services.stop_training.update_model_status") as update_status,
        patch.object(fl_service.logger, "exception") as logged,
        pytest.raises(HTTPException) as caught,
    ):
        stop_training(model_id, session, uuid4())

    assert caught.value.status_code == 500
    assert caught.value.detail == "Internal server error"
    upstream_error = caught.value.__cause__
    assert isinstance(upstream_error, httpx.HTTPStatusError)
    assert upstream_error.response is refused_response
    assert upstream_error.request.url.path.startswith(failed_path)
    assert refusal not in str(upstream_error)
    message = (
        f"The FL server refused the job: {refusal}"
        if status_code == 400
        else "The FL server refused the job (HTTP 500)."
    )
    assert_failure_in_feed(session, model_id, message)
    logged.assert_any_call("FL API request failed: %s", upstream_error)
    assert all("private" not in str(call) for call in logged.call_args_list)
    dequeue.assert_called_once_with(model_id, session)
    update_status.assert_not_called()
    release.assert_not_called()
