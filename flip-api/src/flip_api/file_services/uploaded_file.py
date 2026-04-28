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

"""Process malware-scan results posted by AWS SNS.

Wiring (see deploy/providers/AWS/services.tf):

    Upload → UPLOADED_MODEL_FILES_BUCKET (staging)
    → GuardDuty Malware Protection for S3 emits an EventBridge event
    → EventBridge rule forwards the event to an SNS topic
    → SNS topic is HTTPS-subscribed to this endpoint

For each clean scan result the file is copied to ``SCANNED_MODEL_FILES_BUCKET``
(the canonical bucket downloads + FL clients read from), the staging copy is
deleted, and the matching ``UploadedFiles`` row is set to ``COMPLETED``.
Infected, oversized, or extension-disallowed files are deleted from staging
and the row is marked ``INFECTED``/``ERROR`` so the UI can surface the
verdict to the uploader.

Pickle-bearing extensions (``.pt``/``.pth``/``.pkl``/``.ckpt``) are
additionally scanned with ``picklescan`` because GuardDuty does not parse
pickle opcodes — the most realistic attack on an ML platform.
"""

import json
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import UploadedFiles
from flip_api.domain.schemas.file import SNSNotification
from flip_api.domain.schemas.status import FileUploadStatus
from flip_api.utils.logger import logger
from flip_api.utils.pickle_scanner import is_pickle_bearing, scan_pickle_bytes
from flip_api.utils.s3_client import S3Client
from flip_api.utils.sns import SNSVerificationError, confirm_subscription, verify_sns_signature

router = APIRouter(prefix="/files", tags=["file_services"])

# GuardDuty Malware Protection for S3 emits this status when no threats are
# detected. Anything else (THREATS_FOUND, UNSUPPORTED, ACCESS_DENIED, FAILED)
# is treated as "do not promote".
_GUARDDUTY_CLEAN_STATUS = "NO_THREATS_FOUND"
_GUARDDUTY_INFECTED_STATUS = "THREATS_FOUND"


class _ScanOutcome:
    """Verdict from parsing the EventBridge → SNS payload."""

    __slots__ = ("bucket", "key", "is_clean", "threats", "raw_status")

    def __init__(self, bucket: str, key: str, is_clean: bool, threats: list[str], raw_status: str) -> None:
        self.bucket = bucket
        self.key = key
        self.is_clean = is_clean
        self.threats = threats
        self.raw_status = raw_status


def _parse_eventbridge_payload(message: str) -> _ScanOutcome:
    """Extract bucket, key, and verdict from an EventBridge → SNS message body.

    The ``Message`` field is a JSON string of the EventBridge event AWS
    GuardDuty Malware Protection for S3 emits.

    Args:
        message: The raw ``Message`` string from the SNS envelope.

    Returns:
        A ``_ScanOutcome`` describing the file and the verdict.

    Raises:
        HTTPException: 400 if the payload shape is unrecognised.
    """
    try:
        event = json.loads(message)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON in SNS Message: {exc}",
        ) from exc

    detail = event.get("detail") or {}
    s3_object = detail.get("s3ObjectDetails") or {}
    scan = detail.get("scanResultDetails") or {}

    bucket = s3_object.get("bucketName")
    key = s3_object.get("objectKey")
    raw_status = scan.get("scanResultStatus", "")

    if not bucket or not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SNS Message missing s3ObjectDetails.bucketName or objectKey.",
        )

    threats: list[str] = []
    for threat in scan.get("threats") or []:
        name = threat.get("name") if isinstance(threat, dict) else None
        if name:
            threats.append(str(name))

    return _ScanOutcome(
        bucket=bucket,
        key=key,
        is_clean=raw_status == _GUARDDUTY_CLEAN_STATUS,
        threats=threats,
        raw_status=raw_status,
    )


def _parse_object_key(key: str) -> tuple[UUID, str]:
    """Parse ``<model_id>/<file_name>`` from the S3 object key.

    Args:
        key: The S3 object key as written by the presigned-URL endpoint.

    Returns:
        Tuple of ``(model_id, file_name)``.

    Raises:
        HTTPException: 400 if the key does not match the expected layout.
    """
    parts = key.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Object key '{key}' does not match expected '<model_id>/<file_name>' layout.",
        )
    try:
        model_id = UUID(parts[0])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Object key prefix '{parts[0]}' is not a valid UUID.",
        ) from exc
    return model_id, parts[1]


def _set_file_status(
    db: Session,
    model_id: UUID,
    file_name: str,
    new_status: FileUploadStatus,
    size: float | None = None,
    type_: str | None = None,
) -> None:
    """Upsert the ``UploadedFiles`` row for this model+file with the given status."""
    existing = db.exec(
        select(UploadedFiles).where(UploadedFiles.model_id == model_id, UploadedFiles.name == file_name)
    ).first()
    if existing:
        existing.status = new_status
        if size is not None:
            existing.size = size
        if type_ is not None:
            existing.type = type_
    else:
        db.add(
            UploadedFiles(
                name=file_name,
                status=new_status,
                size=float(size or 0),
                type=type_ or "",
                model_id=model_id,
            )
        )
    db.commit()


def _delete_quietly(s3: S3Client, s3_path: str) -> None:
    """Best-effort delete of an S3 object — log but never raise.

    The handler must always return a 2xx to SNS once it has processed a
    notification; otherwise SNS retries and the same scan result is
    re-applied. Failed cleanups are logged for the on-call to clear up
    rather than crashing the request.
    """
    try:
        s3.delete_object(s3_path)
    except Exception as exc:
        logger.warning(f"Failed to delete {s3_path}: {exc}. Manual cleanup required.")


def _handle_scan_result(outcome: _ScanOutcome, db: Session) -> dict[str, str]:
    """Apply a parsed scan verdict to S3 + the database.

    Args:
        outcome: The verdict and S3 location.
        db: Database session.

    Returns:
        A short status dict for the SNS caller (purely informational; SNS
        ignores the body but the response is useful in logs and tests).
    """
    settings = get_settings()
    model_id, file_name = _parse_object_key(outcome.key)
    staging_path = f"s3://{outcome.bucket}/{outcome.key}"

    if not outcome.is_clean:
        logger.warning(
            f"Malware scan rejected {staging_path}: status={outcome.raw_status}, threats={outcome.threats}"
        )
        s3 = S3Client()
        _delete_quietly(s3, staging_path)
        new_status = (
            FileUploadStatus.INFECTED
            if outcome.raw_status == _GUARDDUTY_INFECTED_STATUS
            else FileUploadStatus.ERROR
        )
        _set_file_status(db, model_id, file_name, new_status)
        return {"status": "rejected", "scan_status": outcome.raw_status}

    s3 = S3Client()

    # Authoritative server-side size + extension re-check. The presigned-URL
    # endpoint validates these too, but a malicious caller could lie about
    # the file size or substitute a different filename in the URL path, so
    # the post-upload check is the boundary that actually matters.
    try:
        head = s3.head_object(staging_path)
    except Exception as exc:
        logger.error(f"head_object failed for {staging_path}: {exc}")
        _set_file_status(db, model_id, file_name, FileUploadStatus.ERROR)
        return {"status": "error", "detail": "head_object failed"}

    content_length = int(head.get("ContentLength") or 0)
    content_type = str(head.get("ContentType") or "application/octet-stream")
    extension = os.path.splitext(file_name)[1].lstrip(".").lower()
    allowed = {ext.lower() for ext in settings.ALLOWED_MODEL_FILE_EXTENSIONS}

    if extension not in allowed:
        logger.warning(f"Rejecting {staging_path}: extension '{extension}' not in allowed list.")
        _delete_quietly(s3, staging_path)
        _set_file_status(db, model_id, file_name, FileUploadStatus.ERROR, size=float(content_length))
        return {"status": "rejected", "detail": f"extension {extension} not allowed"}

    if content_length > settings.MAX_MODEL_FILE_SIZE_BYTES:
        logger.warning(
            f"Rejecting {staging_path}: size {content_length} exceeds max "
            f"{settings.MAX_MODEL_FILE_SIZE_BYTES}."
        )
        _delete_quietly(s3, staging_path)
        _set_file_status(db, model_id, file_name, FileUploadStatus.ERROR, size=float(content_length))
        return {"status": "rejected", "detail": "file too large"}

    # Pickle scan for ML artifacts. GuardDuty cannot detect a malicious
    # pickle payload, so this runs as a second-line check before promotion.
    if is_pickle_bearing(extension):
        try:
            obj = s3.get_object(staging_path)
            body = obj["Body"].read()
        except Exception as exc:
            logger.error(f"Failed to fetch {staging_path} for pickle scan: {exc}")
            _set_file_status(db, model_id, file_name, FileUploadStatus.ERROR, size=float(content_length))
            return {"status": "error", "detail": "pickle scan fetch failed"}

        result = scan_pickle_bytes(body, file_name)
        if not result.is_safe:
            logger.warning(
                f"picklescan rejected {staging_path}: threats={list(result.threats)}, "
                f"scanner_error={result.scanner_error}"
            )
            _delete_quietly(s3, staging_path)
            _set_file_status(
                db, model_id, file_name, FileUploadStatus.INFECTED, size=float(content_length)
            )
            return {"status": "rejected", "detail": "pickle scan failed"}

    # Promote to the canonical bucket.
    scanned_path = f"{settings.SCANNED_MODEL_FILES_BUCKET}/{model_id}/{file_name}"
    try:
        s3.copy_object(staging_path, scanned_path)
    except Exception as exc:
        logger.error(f"Failed to copy {staging_path} → {scanned_path}: {exc}")
        _set_file_status(db, model_id, file_name, FileUploadStatus.ERROR, size=float(content_length))
        return {"status": "error", "detail": "copy failed"}

    _delete_quietly(s3, staging_path)
    _set_file_status(
        db,
        model_id,
        file_name,
        FileUploadStatus.COMPLETED,
        size=float(content_length),
        type_=content_type,
    )
    return {"status": "promoted"}


async def _read_sns_envelope(request: Request) -> SNSNotification:
    """Parse the SNS HTTPS envelope from a raw request body.

    SNS posts with content-type ``text/plain`` (despite the body being JSON),
    which trips FastAPI's automatic body parsing. Reading the bytes manually
    keeps the parsing robust regardless of the Content-Type header sent.
    """
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request body is not valid JSON: {exc}",
        ) from exc
    try:
        return SNSNotification.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request body is not a valid SNS envelope: {exc}",
        ) from exc


@router.post("/process-scanned-file", response_model=dict[str, str])
async def process_scanned_file(
    request: Request,
    db: Session = Depends(get_session),
) -> dict[str, str]:
    """Receive an SNS HTTPS notification carrying a malware-scan verdict.

    This endpoint is intentionally not behind Cognito auth — SNS does not
    carry user JWTs. Instead it verifies the message's RSA signature against
    the SigningCert published by AWS, and only acts on payloads from the
    expected ``SNS_TOPIC_ARN`` (when configured). Subscription confirmations
    are handled inline by GETing the ``SubscribeURL``.

    Args:
        request: The raw HTTPS request from SNS.
        db: Database session.

    Returns:
        A small status dict describing the action taken. SNS ignores the
        body; the response is useful in logs and tests.

    Raises:
        HTTPException: 400 on malformed payloads, 401 on signature/topic
            failures, 500 for internal errors during processing.
    """
    settings = get_settings()
    payload = await _read_sns_envelope(request)

    if settings.SNS_TOPIC_ARN and payload.TopicArn and payload.TopicArn != settings.SNS_TOPIC_ARN:
        logger.warning(f"Rejecting SNS payload from unexpected topic {payload.TopicArn}.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unexpected SNS topic.",
        )

    if settings.SNS_VERIFY_SIGNATURE:
        try:
            verify_sns_signature(payload)
        except SNSVerificationError as exc:
            logger.warning(f"Rejecting SNS payload — signature verification failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="SNS signature verification failed.",
            ) from exc

    if payload.Type == "SubscriptionConfirmation":
        try:
            confirm_subscription(payload)
        except SNSVerificationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subscription confirmation failed: {exc}",
            ) from exc
        return {"status": "subscription_confirmed"}

    if payload.Type == "UnsubscribeConfirmation":
        # Nothing to do — log and acknowledge so SNS stops retrying.
        logger.info(f"Received UnsubscribeConfirmation for topic {payload.TopicArn}.")
        return {"status": "unsubscribe_acknowledged"}

    if payload.Type != "Notification":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported SNS message Type: {payload.Type}",
        )

    outcome = _parse_eventbridge_payload(payload.Message)
    try:
        return _handle_scan_result(outcome, db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error processing scan result")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {exc}",
        ) from exc
