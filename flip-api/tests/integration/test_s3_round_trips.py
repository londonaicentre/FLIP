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

"""Real-S3 round-trips for ``flip_api.utils.s3_client`` and ``file_services/*``.

Replaces the all-mocked unit tests for the file-services flow. moto intercepts
``boto3.client("s3", ...)`` at the botocore layer, so the production code path
through ``S3Client`` exercises the real boto3 -> botocore -> moto stack with
no test-only branches in source.

Coverage:

* ``S3Client`` direct calls — presigned URL, head/get/put/delete/list/copy.
* ``POST /files/preSignedUrl/model/{model_id}`` — endpoint returns a working
  upload URL; pre-signed PUT against moto succeeds and the object lands at the
  expected key.
* ``POST /files/process-scanned-file/{model_id}/{file_name}`` — pre-seeded
  S3 object's metadata makes its way into the ``uploaded_files`` row.
* ``GET /files/model/{model_id}/files/list`` — listing reflects the keys
  actually present in the scanned bucket.
* ``GET /files/model/{model_id}/{file_name}`` — download returns a presigned URL
  that itself serves the object's bytes byte-for-byte.
* ``DELETE /files/model/{model_id}/{file_name}`` — endpoint removes the S3
  object and the DB row.
* ``GET /files/model/{model_id}/fl/results`` — federated-results endpoint
  returns presigned URLs for every object under the FL prefix.
"""

from urllib.parse import urlparse
from uuid import UUID, uuid4

import boto3
import requests
from fastapi.testclient import TestClient
from sqlmodel import select

from flip_api.config import get_settings
from flip_api.db.models.main_models import Model, Projects, UploadedFiles
from flip_api.domain.schemas.status import FileUploadStatus, ModelStatus
from flip_api.utils.s3_client import S3Client
from tests.integration.conftest import admin_user, override_verify_token_as


def _seed_project_and_model(session, owner_id: UUID) -> tuple[UUID, UUID]:
    """Create a non-deleted Project + Model owned by ``owner_id``.

    file_services endpoints filter on ``Projects.deleted is False`` and follow
    the FK from Model.project_id, so both rows have to exist before any
    request lands. ``flush()`` between the inserts makes the project visible
    to the FK lookup without ending the transaction; one ``commit()`` at the
    end suffices.
    """
    project = Projects(id=uuid4(), name="b2-s3-proj", description="b2", owner_id=owner_id, deleted=False)
    session.add(project)
    session.flush()
    model = Model(
        id=uuid4(),
        name="b2-model",
        description="b2",
        status=ModelStatus.PENDING,
        project_id=project.id,
        owner_id=owner_id,
    )
    session.add(model)
    session.commit()
    return project.id, model.id


def _bucket_and_prefix(setting_value: str) -> tuple[str, str]:
    """Split an ``s3://bucket/prefix`` setting into ``(bucket, prefix)``."""
    parsed = urlparse(setting_value)
    return parsed.netloc, parsed.path.lstrip("/")


# ---------------------------------------------------------------------------
# S3Client direct round-trips
# ---------------------------------------------------------------------------


def test_s3_client_put_then_head_then_delete_round_trip(s3_buckets):
    """Verifies the boto3 -> moto wiring through ``S3Client`` itself."""
    settings = get_settings()
    s3 = S3Client()
    object_key = f"{settings.UPLOADED_MODEL_FILES_BUCKET}/round-trip/{uuid4()}.txt"
    bucket, key = urlparse(object_key).netloc, urlparse(object_key).path.lstrip("/")
    payload = b"hello-from-moto"

    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=payload, ContentType="text/plain")

    head = s3.head_object(object_key)
    assert head["ContentLength"] == len(payload)
    assert head["ContentType"] == "text/plain"

    body = s3.get_object(object_key)["Body"].read()
    assert body == payload

    assert s3.object_exists(object_key) is True
    s3.delete_object(object_key)
    assert s3.object_exists(object_key) is False


def test_s3_client_list_objects_returns_full_paths_with_scheme(s3_buckets):
    """``list_objects`` returns ``s3://bucket/key`` strings, not raw keys.

    Documents the API contract that file-services callers rely on: the listing
    output is fed back into ``S3Client.get_presigned_url`` etc, which expects
    the ``s3://`` scheme.
    """
    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.UPLOADED_FEDERATED_DATA_BUCKET)
    model_id = uuid4()
    boto3_client = boto3.client("s3")
    for name in ("metrics.json", "weights.bin", "report.txt"):
        boto3_client.put_object(Bucket=bucket, Key=f"{prefix}/{model_id}/{name}", Body=b"x")

    listing = S3Client().list_objects(f"{settings.UPLOADED_FEDERATED_DATA_BUCKET}/{model_id}")

    assert sorted(listing) == sorted(
        [
            f"s3://{bucket}/{prefix}/{model_id}/metrics.json",
            f"s3://{bucket}/{prefix}/{model_id}/report.txt",
            f"s3://{bucket}/{prefix}/{model_id}/weights.bin",
        ]
    )


# ---------------------------------------------------------------------------
# /files/preSignedUrl/model/{model_id}
# ---------------------------------------------------------------------------


def test_post_presigned_url_endpoint_returns_working_upload_policy(
    client: TestClient, session, s3_buckets
):
    """Endpoint returns a presigned POST policy; the policy actually accepts an upload.

    Build the policy via the endpoint, sign it with moto's signing path, send
    a real multipart POST through ``requests``, then verify the object lands
    at the expected key. Locks in the contract that the response carries
    ``url`` + ``fields`` + ``maxBytes`` and that the POSTed object materialises
    in the bucket.
    """
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    response = client.post(
        f"/api/files/preSignedUrl/model/{model_id}",
        json={"fileName": "weights.bin", "contentType": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    policy = response.json()
    assert policy["url"], "endpoint returned an empty URL"
    assert isinstance(policy["fields"], dict)
    assert policy["maxBytes"] > 0
    assert policy["fields"].get("Content-Type") == "application/octet-stream"

    payload = b"\x00\x01\x02moto-bytes"
    post = requests.post(
        policy["url"],
        data=policy["fields"],
        files={"file": ("weights.bin", payload, "application/octet-stream")},
        timeout=10,
    )
    assert post.status_code in (200, 204), post.text

    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.UPLOADED_MODEL_FILES_BUCKET)
    expected_key = f"{prefix}/{model_id}/weights.bin"
    obj = boto3.client("s3").get_object(Bucket=bucket, Key=expected_key)
    assert obj["Body"].read() == payload


def test_post_presigned_url_policy_carries_size_cap_and_content_type_lock(
    client: TestClient, session, s3_buckets, monkeypatch
):
    """The policy returned by the endpoint must bake the size cap and the
    Content-Type lock in directly.

    Real S3 enforces ``content-length-range`` and Content-Type conditions
    server-side — that's the actual storage-cost-DoS mitigation. moto's
    fake doesn't enforce policy conditions on POST uploads (oversized
    payloads still get a 204), so this test asserts the structural
    contract by base64-decoding the policy field instead.
    """
    import base64
    import json as _json

    from flip_api.utils.s3_client import _MULTIPART_OVERHEAD_BUFFER_BYTES

    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    monkeypatch.setattr(get_settings(), "MAX_MODEL_FILE_BYTES", 64)

    response = client.post(
        f"/api/files/preSignedUrl/model/{model_id}",
        json={"fileName": "huge.bin", "contentType": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    policy_response = response.json()
    # The endpoint surfaces the raw file-size cap so the UI guard mirrors it.
    assert policy_response["maxBytes"] == 64
    assert policy_response["fields"]["Content-Type"] == "application/octet-stream"

    decoded = _json.loads(base64.b64decode(policy_response["fields"]["policy"]))
    conditions = decoded["conditions"]
    # S3 sees ``max_bytes + buffer`` because content-length-range checks the
    # whole encoded request body (multipart framing), not just the file part.
    expected_cap = 64 + _MULTIPART_OVERHEAD_BUFFER_BYTES
    assert ["content-length-range", 0, expected_cap] in conditions
    assert any(
        isinstance(c, dict) and c.get("Content-Type") == "application/octet-stream"
        for c in conditions
    )


def test_post_presigned_url_endpoint_404_for_unknown_model(client: TestClient, session, s3_buckets):
    """Unknown / deleted models should 404 before any S3 call is attempted.

    Authz runs before the model lookup (``can_modify_model`` rejects an
    unprivileged caller with 403), so the test needs an admin user to reach
    the 404 path.
    """
    user_id = admin_user(session)
    override_verify_token_as(user_id)

    response = client.post(
        f"/api/files/preSignedUrl/model/{uuid4()}",
        json={"fileName": "weights.bin"},
    )
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# /files/process-scanned-file/{model_id}/{file_name}
# ---------------------------------------------------------------------------


def test_process_scanned_file_records_metadata_in_db(client: TestClient, session, s3_buckets):
    """Pre-seed an object in the upload bucket; assert the resulting DB row."""
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.UPLOADED_MODEL_FILES_BUCKET)
    file_name = "model.tar.gz"
    payload = b"\x00" * 4096
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=f"{prefix}/{model_id}/{file_name}",
        Body=payload,
        ContentType="application/gzip",
    )

    response = client.post(f"/api/files/process-scanned-file/{model_id}/{file_name}")
    assert response.status_code == 200, response.text

    row = session.exec(
        select(UploadedFiles).where(
            UploadedFiles.model_id == model_id,
            UploadedFiles.name == file_name,
        )
    ).first()
    assert row is not None
    assert row.size == len(payload)
    assert row.type == "application/gzip"
    assert row.status == FileUploadStatus.COMPLETED


# ---------------------------------------------------------------------------
# /files/model/{model_id}/files/list
# ---------------------------------------------------------------------------


def test_retrieve_model_files_list_categorises_monai_files(
    client: TestClient, session, s3_buckets
):
    """The endpoint categorises by the well-known monai suffixes the FL server expects.

    ``retrieve_model_files_list`` matches keys ending in ``monaialgo.py`` →
    ``algo``, ``monaiopener.py`` → ``opener``, and ``monai-test.pth.tar`` →
    ``model``. Other files in the prefix are ignored — that's a quirk of the
    endpoint, not the test, and the contract is what we want to lock in.
    """
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.SCANNED_MODEL_FILES_BUCKET)
    s3 = boto3.client("s3")
    keys_by_kind = {
        "algo": f"{prefix}/{model_id}/monaialgo.py",
        "opener": f"{prefix}/{model_id}/monaiopener.py",
        "model": f"{prefix}/{model_id}/monai-test.pth.tar",
    }
    for key in keys_by_kind.values():
        s3.put_object(Bucket=bucket, Key=key, Body=b"x")

    response = client.get(f"/api/files/model/{model_id}/files/list")

    assert response.status_code == 200, response.text
    files = response.json()["files"]
    for kind, key in keys_by_kind.items():
        assert files[kind] is not None, f"{kind} file missing from response"
        assert files[kind].endswith(key.rsplit("/", 1)[-1])


def test_retrieve_model_files_list_404_when_no_objects(client: TestClient, session, s3_buckets):
    """Empty scanned-bucket prefix should bubble a 404, not a 500."""
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    response = client.get(f"/api/files/model/{model_id}/files/list")

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# /files/model/{model_id}/{file_name}  (download)
# ---------------------------------------------------------------------------


def test_download_file_returns_working_presigned_url(client: TestClient, session, s3_buckets):
    """Endpoint returns a presigned GET URL, and that URL actually serves the exact bytes.

    FLIP#784: the endpoint used to stream the object through flip-api (bounded by a
    client-side timeout for large files); it now hands back a short-lived presigned
    URL and the caller fetches bytes directly from S3.
    """
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.SCANNED_MODEL_FILES_BUCKET)
    file_name = "weights.bin"
    payload = bytes(range(256)) * 4
    boto3.client("s3").put_object(
        Bucket=bucket, Key=f"{prefix}/{model_id}/{file_name}", Body=payload
    )
    session.add(
        UploadedFiles(
            id=uuid4(),
            name=file_name,
            status=FileUploadStatus.COMPLETED,
            size=len(payload),
            type="application/octet-stream",
            model_id=model_id,
        )
    )
    session.commit()

    response = client.get(f"/api/files/model/{model_id}/{file_name}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fileName"] == file_name
    # Presigned URLs carry a ``Signature=`` query param (SigV2) or an
    # ``X-Amz-Signature=`` query param (SigV4); either confirms it was signed.
    assert "Signature=" in body["url"] or "X-Amz-Signature=" in body["url"], f"not a presigned URL: {body['url']}"

    # Note: the Content-Disposition override (see test_s3_client.py's
    # get_presigned_url tests for that contract) isn't asserted here — moto's
    # GetObject simulation doesn't echo back ResponseContentDisposition,
    # unlike real S3.
    download = requests.get(body["url"], timeout=10)
    assert download.status_code == 200, download.text
    assert download.content == payload


def test_download_file_404_when_db_row_missing(client: TestClient, session, s3_buckets):
    """Endpoint must check the DB before reaching for S3 — missing row → 404."""
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    response = client.get(f"/api/files/model/{model_id}/missing.bin")

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# /files/model/{model_id}/{file_name}  (delete)
# ---------------------------------------------------------------------------


def test_delete_model_file_removes_object_and_db_row(client: TestClient, session, s3_buckets):
    """Both the S3 object and the ``uploaded_files`` row must be gone."""
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.SCANNED_MODEL_FILES_BUCKET)
    file_name = "to-delete.txt"
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=f"{prefix}/{model_id}/{file_name}", Body=b"goodbye")
    session.add(
        UploadedFiles(
            id=uuid4(),
            name=file_name,
            status=FileUploadStatus.COMPLETED,
            size=7,
            type="text/plain",
            model_id=model_id,
        )
    )
    session.commit()

    response = client.delete(f"/api/files/model/{model_id}/{file_name}")
    assert response.status_code == 200, response.text

    # S3: object gone.
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/{model_id}/")
    keys = [o["Key"] for o in listing.get("Contents", [])]
    assert f"{prefix}/{model_id}/{file_name}" not in keys

    # DB: row gone.
    remaining = session.exec(
        select(UploadedFiles).where(
            UploadedFiles.model_id == model_id,
            UploadedFiles.name == file_name,
        )
    ).first()
    assert remaining is None


# ---------------------------------------------------------------------------
# /files/model/{model_id}/fl/results
# ---------------------------------------------------------------------------


def test_retrieve_federated_results_returns_presigned_urls(client: TestClient, session, s3_buckets):
    """Each FL artefact under the model's prefix gets a presigned URL back."""
    user_id = admin_user(session)
    _, model_id = _seed_project_and_model(session, user_id)
    override_verify_token_as(user_id)

    settings = get_settings()
    bucket, prefix = _bucket_and_prefix(settings.UPLOADED_FEDERATED_DATA_BUCKET)
    s3 = boto3.client("s3")
    for name in ("metrics.json", "weights.bin"):
        s3.put_object(Bucket=bucket, Key=f"{prefix}/{model_id}/{name}", Body=b"x")

    response = client.get(f"/api/files/model/{model_id}/fl/results")
    assert response.status_code == 200, response.text
    urls = response.json()
    assert len(urls) == 2
    # Presigned URLs carry a ``Signature=`` query param (SigV2) or an
    # ``X-Amz-Signature=`` query param (SigV4). Either is fine — the
    # assertion only cares that the URL was actually signed, not which
    # version of the SDK signing path it went through.
    for url in urls:
        assert "Signature=" in url or "X-Amz-Signature=" in url, f"not a presigned URL: {url}"
        assert "uploaded_federated_data" in url


def test_retrieve_federated_results_404_for_unknown_model(client: TestClient, session, s3_buckets):
    """Federated-results endpoint should 404 a missing model before S3."""
    user_id = admin_user(session)
    override_verify_token_as(user_id)

    response = client.get(f"/api/files/model/{uuid4()}/fl/results")

    assert response.status_code == 404, response.text
