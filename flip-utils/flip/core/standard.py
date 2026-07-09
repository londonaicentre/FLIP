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

"""
Standard FLIP Implementation.

This module contains the production and development implementations of FLIP
for the standard, evaluation, and fed_opt job types.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, override
from urllib.parse import urlparse

import boto3
import pandas as pd
import requests
from requests import HTTPError

from flip.constants.flip_constants import FlipConstants, ModelStatus, ResourceType
from flip.core.base import FLIPBase
from flip.exceptions import ResultsUploadError
from flip.schemas import FLLogEvent, TrainingLog, TrainingMetrics
from flip.utils.utils import Utils


def _trust_internal_headers() -> dict[str, str]:
    """Return the auth header sent on every trust-internal call.

    Used on outbound calls to imaging-api and data-access-api. The receiver
    (in the FLIP repo) compares the value with a constant-time compare against
    its own copy of the same per-trust key.

    Returns:
        dict[str, str]: Single-entry dict mapping the configured header name to
        the trust-internal service key.
    """
    return {FlipConstants.TRUST_INTERNAL_SERVICE_KEY_HEADER: FlipConstants.TRUST_INTERNAL_SERVICE_KEY}


def _join_url(base: object, path: str) -> str:
    """Join a base URL with a path, tolerating a trailing slash on the base.

    Pydantic v2 serializes a host-only ``HttpUrl`` with a trailing slash
    (e.g. ``http://data-access-api:8000/``), so naive f-string concatenation
    with ``/cohort/dataframe`` yields a double slash (``//cohort/dataframe``)
    that Starlette does not route — producing a spurious ``404 Not Found``.
    Normalising the base before joining keeps every trust-internal and
    hub-internal call working regardless of whether the configured URL carries
    a trailing slash. See FLIP#652.

    Args:
        base: The base URL (``str`` or pydantic ``HttpUrl``).
        path: The path to append (a leading slash is optional).

    Returns:
        str: ``<base-without-trailing-slash>/<path-without-leading-slash>``.
    """
    return f"{str(base).rstrip('/')}/{path.lstrip('/')}"


def _hub_internal_headers() -> dict[str, str]:
    """Return the auth header sent on every hub-internal call.

    Used by fl-server on the Central Hub for outbound calls to flip-api
    (update_status, send_metrics, send_handled_exception). The receiver
    (flip-api) compares the value with a constant-time compare against its
    own copy. Distinct boundary from the trust-internal key — a leak in one
    trust never affects this hub-side path and vice versa.

    Returns:
        dict[str, str]: Single-entry dict mapping the configured header name to
        the hub internal-service key.
    """
    return {FlipConstants.INTERNAL_SERVICE_KEY_HEADER: FlipConstants.INTERNAL_SERVICE_KEY}


class FLIPStandardProd(FLIPBase):
    """Production implementation of FLIP for standard job types.

    Method usage by FL role:

    **Server-only** (fl-server on Central Hub → calls flip-api):
        - ``update_status()`` — update model training status
        - ``send_metrics()`` — forward per-client training/evaluation metrics
        - ``send_handled_exception()`` — forward client exception logs
        - ``upload_results_to_s3()`` — upload trained model to S3

    **Client-only** (fl-client on trust side → calls local trust APIs):
        - ``get_dataframe()`` — fetch cohort data from data-access-api
        - ``get_images()`` — download images from imaging-api
        - ``download_data_from_s3()`` — download federated data from S3
    """

    def __init__(self):
        super().__init__()
        self._name = self.__class__.__name__
        self.logger = logging.getLogger(self._name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    @override
    def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
        """
        Retrieves the dataframe from the trust OMOP using the SQL query.
        Calls the FLIP data-access-api.

        Args:
            project_id (str): Project identifier
            query (str): SQL query

        Returns:
            pd.DataFrame: Dataframe containing the resulting accession ids and additional data.
        """
        self.check_query(query)
        self.check_project_id(project_id)

        self.logger.info("Attempting to fetch dataframe for imaging project...")

        payload = {
            "encrypted_project_id": project_id,
            "query": query,
        }

        endpoint = _join_url(FlipConstants.DATA_ACCESS_API_URL, "cohort/dataframe")

        response = requests.post(
            endpoint,
            json=payload,
            headers=_trust_internal_headers(),
        )

        self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")

        response.raise_for_status()

        content = json.loads(response.text)

        df = pd.DataFrame(data=content)

        self.logger.info("Successfully fetched dataframe")

        return df

    @override
    def get_by_accession_number(
        self,
        project_id: str,
        accession_id: str,
        resource_type: ResourceType | list[ResourceType] = ResourceType.NIFTI,
    ) -> Path:
        """
        Calls the imaging-service to return a filepath that contains images downloaded from XNAT
        based on the accession number.

        Args:
            project_id (str): The ID of the project.
            accession_id (str): The accession ID of the imaging study.
            resource_type (Union[ResourceType, List[ResourceType]]): The type of resource to download. Defaults to
            ResourceType.NIFTI.

        Returns:
            Path: Path to the downloaded data for that accession_id.
        """
        self.check_project_id(project_id)
        self.check_accession_id(accession_id)
        resources = self.check_resource_type(resource_type)

        self.logger.info(f"Attempting to download {resources} images for {accession_id}")

        payload = {
            "encrypted_central_hub_project_id": project_id,
            "accession_id": accession_id,
        }

        endpoint = _join_url(FlipConstants.IMAGING_API_URL, f"download/images/{FlipConstants.NET_ID}")

        for resource in resources:
            if resource != ResourceType.SEGMENTATION:
                assessor_type = "scan"
            else:
                assessor_type = "assessor"

            response = requests.post(
                endpoint,
                json=payload,
                params={
                    "assessor_type": assessor_type,
                    "resource_type": resource.value,
                },
                headers=_trust_internal_headers(),
            )
            self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")

            response.raise_for_status()

            self.logger.info(f"Successfully downloaded {resource} images for {accession_id}")

            imaging_service_response_json = response.json()

        return Path(imaging_service_response_json["path"])

    @override
    def add_resource(
        self,
        project_id: str,
        accession_id: str,
        scan_id: str,
        resource_id: str,
        files: list[str],
    ) -> None:
        """
        Calls the imaging-service to upload image(s) to XNAT based on the accession number,
        scan ID, and resource ID.

        Args:
            project_id (str): Unique project identifier
            accession_id (str): Accession ID to upload the resource to
            scan_id (str): ID of the scan to upload
            resource_id (str): Type of resource that is being uploaded (e.g. NIFTI)
            files (List[str]): List of files to upload
        """
        if not isinstance(project_id, str):
            raise TypeError(f"expect project id to be string, but got {type(project_id)}")

        if not isinstance(accession_id, str):
            raise TypeError(f"expect accession_id to be string, but got {type(accession_id)}")

        if not isinstance(scan_id, str):
            raise TypeError(f"expect scan_id to be string, but got {type(scan_id)}")

        if not isinstance(resource_id, str):
            raise TypeError(f"expect resource_id to be string, but got {type(resource_id)}")

        if not isinstance(files, list):
            raise TypeError(f"expect files to be List, but got {type(files)}")

        self.logger.info(
            f"Attempting to add resources for experiments/{accession_id}/scans/{scan_id}/resources/{resource_id}"
        )

        payload = {
            "encrypted_central_hub_project_id": project_id,
            "accession_id": accession_id,
            "scan_id": scan_id,
            "resource_id": resource_id,
            "files": files,
        }

        endpoint = _join_url(FlipConstants.IMAGING_API_URL, f"upload/images/{FlipConstants.NET_ID}")

        response = requests.put(
            endpoint,
            json=payload,
            headers=_trust_internal_headers(),
        )

        response.raise_for_status()

        self.logger.info(
            f"Successfully uploaded resources for experiments/{accession_id}/scans/{scan_id}/resources/{resource_id}"
        )

    @override
    def update_status(self, model_id: str, new_model_status: ModelStatus) -> None:
        """
        Updates the model status on the Central Hub.

        Args:
            model_id (str): Unique model identifier.
            new_model_status (ModelStatus): New model status value.
        """
        if Utils.is_valid_uuid(model_id) is False:
            raise ValueError(f"Invalid model ID: {model_id}, cant update model status")

        endpoint = _join_url(FlipConstants.FLIP_API_INTERNAL_URL, f"model/{model_id}/status/{new_model_status.value}")

        self.logger.info(f"Attempting to update model status to [{new_model_status}]")
        try:
            self.logger.info(
                f"Sending PUT request to {endpoint} with model ID: {model_id} and new status: {new_model_status}"
            )
            response = requests.put(
                endpoint,
                headers=_hub_internal_headers(),
            )
            self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")
            response.raise_for_status()

            self.logger.info(f"Successfully updated model status to [{new_model_status}]")
        except HTTPError as http_err:
            self.logger.error(
                f"An http error occurred when updating the model status, see exception below | status code "
                f"{http_err.response.status_code}"
            )
            self.logger.exception(http_err)
        except Exception as e:
            self.logger.error("Something went wrong when updating the model status, see exception below")
            self.logger.exception(e)

    @override
    def send_metrics(self, client_name: str, model_id: str, label: str, value: float, round: int) -> None:
        """
        Sends a metric value to the Central Hub.

        Args:
            client_name (str): The name of the client.
            model_id (str): The ID of the model.
            label (str): The label of the metric.
            value (float): The value of the metric.
            round (int): The round number.
        """
        payload = TrainingMetrics(
            fl_client_name=client_name,
            global_round=round,
            label=label,
            result=value,
        ).model_dump()

        endpoint = _join_url(FlipConstants.FLIP_API_INTERNAL_URL, f"model/{model_id}/metrics")

        self.logger.info(f"Attempting to send metrics raised by {client_name}...")

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=_hub_internal_headers(),
            )
            self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")
            response.raise_for_status()

            self.logger.info(f"Successfully sent metrics for {client_name}")
        except HTTPError as http_err:
            self.logger.error(
                f"An http error occurred when sending metrics, see exception below | status code "
                f"{http_err.response.status_code}"
            )
            self.logger.exception(http_err)
        except Exception as e:
            self.logger.error("Something went wrong when sending metrics, see exception below")
            self.logger.exception(e)

    @override
    def send_handled_exception(self, formatted_exception: str, client_name: str | None, model_id: str) -> None:
        """
        Sends a handled exception to the Central Hub.

        Args:
            formatted_exception (str): The formatted exception message.
            client_name (str | None): The name of the client that raised the exception.
                None when the client cannot be identified (e.g. a Flower reply that
                crashed before its first healthy response), in which case the hub
                records the exception model-level rather than rejecting it.
            model_id (str): The ID of the model associated with the exception.
        """
        if not isinstance(formatted_exception, str):
            raise TypeError(f"formatted_exception must be type str but got {type(formatted_exception)}")

        if client_name is not None and not isinstance(client_name, str):
            raise TypeError(f"client_name must be type str or None but got {type(client_name)}")

        if Utils.is_valid_uuid(model_id) is False:
            raise ValueError(f"Invalid model ID: {model_id}, unable to send exception")

        # success=False so the hub persists (and the UI shows) a failure row —
        # the ingest default is success=True.
        payload = TrainingLog(
            fl_client_name=client_name,
            log=formatted_exception,
            success=False,
        ).model_dump()

        endpoint = _join_url(FlipConstants.FLIP_API_INTERNAL_URL, f"model/{model_id}/logs")

        self.logger.info(f"Attempting to send the exception raised by {client_name} to the Central Hub...")

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=_hub_internal_headers(),
            )
            self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")
            response.raise_for_status()

            self.logger.info(f"Successfully sent the exception raised by {client_name}")
        except HTTPError as http_err:
            self.logger.error(
                f"An http error occurred when sending the exception to the Central Hub, "
                f"see exception below | status code {http_err.response.status_code}"
            )
            self.logger.exception(http_err)
        except Exception as e:
            self.logger.error("Something went wrong when sending the exception to the Central Hub, see exception below")
            self.logger.exception(e)

    @override
    def send_event(
        self,
        model_id: str,
        event_type: FLLogEvent,
        global_round: int,
        client_name: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> None:
        """
        Sends a typed round-progress event to the Central Hub.

        Facts only — the hub composes display text at serve time. Best-effort:
        a failed post is logged and never breaks training.

        Args:
            model_id (str): The ID of the model the event belongs to.
            event_type (FLLogEvent): Which round event this is.
            global_round (int): The 1-based federated round.
            client_name (str | None): FL client identity for trust-attributed
                events; None for hub-attributed ones.
            details (dict[str, Any] | None): Event-specific facts.
            success (bool): Whether the event marks a healthy step.
        """
        if Utils.is_valid_uuid(model_id) is False:
            raise ValueError(f"Invalid model ID: {model_id}, unable to send event")

        payload = TrainingLog(
            fl_client_name=client_name,
            event_type=event_type,
            global_round=global_round,
            details=details,
            success=success,
        ).model_dump(mode="json")

        endpoint = _join_url(FlipConstants.FLIP_API_INTERNAL_URL, f"model/{model_id}/logs")

        self.logger.info(f"Attempting to send {event_type} (round {global_round}) to the Central Hub...")

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=_hub_internal_headers(),
            )
            self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")
            response.raise_for_status()

            self.logger.info(f"Successfully sent {event_type} for round {global_round}")
        except HTTPError as http_err:
            self.logger.error(
                f"An http error occurred when sending a round event to the Central Hub, "
                f"see exception below | status code {http_err.response.status_code}"
            )
            self.logger.exception(http_err)
        except Exception as e:
            self.logger.error("Something went wrong when sending a round event to the Central Hub, see exception below")
            self.logger.exception(e)

    @override
    def upload_results_to_s3(self, results_folder: Path, model_id: str) -> None:
        """
        Uploads results to S3 bucket in standard mode.

        Args:
            results_folder (Path): The folder containing results to upload
            model_id (str): The model UUID for which results are being uploaded
        """
        s3_bucket = FlipConstants.UPLOADED_FEDERATED_DATA_BUCKET
        self.logger.info(f"Attempting to upload results folder for model {model_id} to S3 bucket {s3_bucket} ...")

        zip_name = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.logger.info(f"Results folder to be zipped: {results_folder}")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_base = Path(tmpdir) / zip_name

                # Create archive
                shutil.make_archive(str(zip_base), "zip", results_folder)

                zip_file = f"{zip_base}.zip"
                self.logger.info(f"Zip file created at: {zip_file}")

                # Parse bucket
                parsed = urlparse(s3_bucket)
                bucket = parsed.netloc
                prefix = parsed.path.lstrip("/").rstrip("/")

                bucket_zip_path = f"{model_id}/{zip_name}.zip"

                # Filter empty parts before joining so a bare bucket URI
                # (`s3://<bucket>` — `parsed.path` empty, `prefix` empty)
                # produces `<model_id>/<file>.zip`, NOT `/<model_id>/<file>.zip`.
                # A leading-slash key is silently accepted by S3 but downstream
                # listers (e.g. `list_objects_v2(Prefix=<model_id>)`) won't
                # match it, and the FLIP UI then reports "no result files".
                # See FLIP#465 for the incident this guards against.
                key = "/".join(part for part in (prefix, bucket_zip_path) if part)

                self.logger.info(f"Uploading zip file {zip_file} to {bucket}/{key}...")

                s3_client = boto3.client("s3")
                s3_client.upload_file(
                    zip_file,
                    bucket,
                    key,
                )

                self.logger.info("Upload .zip to the S3 bucket successful")

        except Exception as e:
            # catch-all: ensures you still get a consistent exception type at the boundary
            self.logger.exception("Unexpected failure in upload_results_to_s3 for model_id=%s", model_id)
            raise ResultsUploadError("Unexpected failure uploading results to S3") from e

    @override
    def cleanup(self, path: Path) -> None:
        """Cleans up local files by deleting the specified path."""
        self.logger.info(f"Cleaning up path: {path}")
        try:
            shutil.rmtree(path)
        except Exception as e:
            self.logger.error(f"Failed to clean up path: {path}, see exception below")
            self.logger.exception(e)
            raise Exception(f"Failed to clean up path: {path}") from e


class FLIPStandardDev(FLIPBase):
    """Development implementation of FLIP for standard job types."""

    def __init__(self):
        super().__init__()
        self._name = self.__class__.__name__
        self.logger = logging.getLogger(self._name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    @override
    def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
        """
        Retrieves the dataframe from the specified CSV path.

        Args:
            project_id (str): Project identifier (validated but not used in dev)
            query (str): SQL query (validated but not used in dev)

        Returns:
            pd.DataFrame: Dataframe from the DEV_DATAFRAME CSV file.
        """
        self.check_project_id(project_id)
        self.check_query(query)

        df = pd.read_csv(FlipConstants.DEV_DATAFRAME)

        if "accession_id" not in df.columns:
            raise ValueError("The provided dataframe does not contain an 'accession_id' column.")

        self.logger.info("Successfully fetched dataframe")

        return df

    @override
    def get_by_accession_number(
        self,
        project_id: str,
        accession_id: str,
        resource_type: ResourceType | list[ResourceType] = ResourceType.NIFTI,
    ) -> Path:
        """
        Returns the path to the image directory for a specific accession ID.

        Args:
            project_id (str): Project identifier
            accession_id (str): Accession ID to retrieve
            resource_type (Union[ResourceType, List[ResourceType]]): Type of imaging resource (not used in dev)

        Returns:
            Path: Path to the accession_id folder within the images folder.
        """
        accession_id_path = Path(FlipConstants.DEV_IMAGES_DIR) / accession_id
        if not os.path.isdir(accession_id_path):
            os.makedirs(accession_id_path, exist_ok=True)
            self.logger.info(
                f"[DEV] Accession ID {accession_id} directory {accession_id_path} does not exist. Created a blank one."
            )

        return accession_id_path

    @override
    def add_resource(
        self,
        project_id: str,
        accession_id: str,
        scan_id: str,
        resource_id: str,
        files: list[str],
    ) -> None:
        """Log only in dev mode - no actual upload."""
        self.logger.info(
            "[DEV] Resource → add %s file(s) to accession=%s scan=%s resource=%s",
            len(files),
            accession_id,
            scan_id,
            resource_id,
        )

    @override
    def update_status(self, model_id: str, new_model_status: ModelStatus) -> None:
        """Log only in dev mode - no actual status update."""
        self.logger.info("[DEV] Status → %s", new_model_status)

    @override
    def send_metrics(self, client_name: str, model_id: str, label: str, value: float, round: int) -> None:
        """Log only in dev mode - no actual metrics sending."""
        self.logger.info(
            "[DEV] Metric → %s=%0.4f (%s, round=%s)",
            label,
            value,
            client_name,
            round,
        )

    @override
    def send_handled_exception(self, formatted_exception: str, client_name: str | None, model_id: str) -> None:
        """Log only in dev mode - no actual exception sending."""
        self.logger.info("[DEV] Exception → reported from %s", client_name)

    @override
    def send_event(
        self,
        model_id: str,
        event_type: FLLogEvent,
        global_round: int,
        client_name: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> None:
        """Log only in dev mode - no actual event sending."""
        self.logger.info("[DEV] Event → %s (round %d) from %s", event_type, global_round, client_name or "hub")

    @override
    def upload_results_to_s3(self, results_folder: Path, model_id: str) -> None:
        """Log only in dev mode - no actual upload."""
        # NOTE FlipConstants.UPLOADED_FEDERATED_DATA_BUCKET is not available in dev mode, so we can't log it here.
        self.logger.info("[DEV] Upload → results from %s", results_folder)

    @override
    def cleanup(self, path: Path) -> None:
        """
        Log only in dev mode - no actual deletion of any files.
        """
        self.logger.info("[DEV] Cleanup → %s", path)
