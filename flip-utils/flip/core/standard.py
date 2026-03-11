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

try:
    from typing import override
except ImportError:
    from typing_extensions import override
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Union
from urllib.parse import urlparse

import boto3
import pandas as pd
import requests
from requests import HTTPError

from flip.constants.flip_constants import FlipConstants, ModelStatus, ResourceType
from flip.core.base import FLIPBase
from flip.utils.utils import Utils


class FLIPStandardProd(FLIPBase):
    """Production implementation of FLIP for standard job types."""

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

        endpoint = f"{FlipConstants.DATA_ACCESS_API_URL}/cohort/dataframe"

        response = requests.post(
            endpoint,
            json=payload,
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
        resource_type: Union[ResourceType, List[ResourceType]] = ResourceType.NIFTI,
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

        endpoint = f"{FlipConstants.IMAGING_API_URL}/download/images/{FlipConstants.NET_ID}"

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
        files: List[str],
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

        if not isinstance(files, List):
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

        endpoint = f"{FlipConstants.IMAGING_API_URL}/upload/images/{FlipConstants.NET_ID}"

        response = requests.put(
            endpoint,
            json=payload,
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

        endpoint = f"{FlipConstants.CENTRAL_HUB_API_URL}/model/{model_id}/status/{new_model_status.value}"

        self.logger.info(f"Attempting to update model status to [{new_model_status}]")
        try:
            self.logger.info(
                f"Sending PUT request to {endpoint} with model ID: {model_id} and new status: {new_model_status}"
            )
            response = requests.put(
                endpoint,
                headers={FlipConstants.PRIVATE_API_KEY_HEADER: FlipConstants.PRIVATE_API_KEY},
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
        payload = {
            "trust": client_name,
            "globalRound": round,
            "label": label,
            "result": value,
        }

        endpoint = f"{FlipConstants.CENTRAL_HUB_API_URL}/model/{model_id}/metrics"

        self.logger.info(f"Attempting to send metrics raised by {client_name}...")

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={FlipConstants.PRIVATE_API_KEY_HEADER: FlipConstants.PRIVATE_API_KEY},
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
    def send_handled_exception(self, formatted_exception: str, client_name: str, model_id: str) -> None:
        """
        Sends a handled exception to the Central Hub.

        Args:
            formatted_exception (str): The formatted exception message.
            client_name (str): The name of the client that raised the exception.
            model_id (str): The ID of the model associated with the exception.
        """
        if not isinstance(formatted_exception, str):
            raise TypeError(f"formatted_exception must be type str but got {type(formatted_exception)}")

        if not isinstance(client_name, str):
            raise TypeError(f"client_name must be type str but got {type(client_name)}")

        if Utils.is_valid_uuid(model_id) is False:
            raise ValueError(f"Invalid model ID: {model_id}, unable to send exception")

        payload = {
            "trust": client_name,
            "log": formatted_exception,
        }

        endpoint = f"{FlipConstants.CENTRAL_HUB_API_URL}/model/{model_id}/logs"

        self.logger.info(f"Attempting to send the exception raised by {client_name} to the Central Hub...")

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={FlipConstants.PRIVATE_API_KEY_HEADER: FlipConstants.PRIVATE_API_KEY},
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
                prefix = parsed.path.lstrip("/")

                bucket_zip_path = f"{model_id}/{zip_name}.zip"

                self.logger.info(f"Uploading zip file {zip_file} to {bucket}/{prefix}/{bucket_zip_path}...")

                s3_client = boto3.client("s3")
                s3_client.upload_file(
                    zip_file,
                    bucket,
                    f"{prefix}/{bucket_zip_path}",
                )

                self.logger.info("Upload .zip to the S3 bucket successful")

        except Exception as e:
            # catch-all: ensures you still get a consistent exception type at the boundary
            self.logger.exception("Unexpected failure in upload_results_to_s3 for model_id=%s", model_id)
            raise Exception("Unexpected failure uploading results to S3") from e

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
        resource_type: Union[ResourceType, List[ResourceType]] = ResourceType.NIFTI,
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
        files: List[str],
    ) -> None:
        """Log only in dev mode - no actual upload."""
        self.logger.info("[DEV] add_resource is not supported in LOCAL_DEV mode.")

    @override
    def update_status(self, model_id: str, new_model_status: ModelStatus) -> None:
        """Log only in dev mode - no actual status update."""
        self.logger.info(
            "[DEV] update_status is not supported in LOCAL_DEV mode."
            f"Details of the function call: updating model status to {new_model_status}."
        )

    @override
    def send_metrics(self, client_name: str, model_id: str, label: str, value: float, round: int) -> None:
        """Log only in dev mode - no actual metrics sending."""
        self.logger.info(
            "[DEV] send_metrics is not supported in LOCAL_DEV mode."
            f"Details of the function call: sending metrics with label {label} and value {value} for {client_name}."
        )

    @override
    def send_handled_exception(self, formatted_exception: str, client_name: str, model_id: str) -> None:
        """Log only in dev mode - no actual exception sending."""
        self.logger.info(
            "[DEV] send_handled_exception is not supported in LOCAL_DEV mode."
            f"Details of the function call: sending {formatted_exception} for {client_name}."
        )

    @override
    def upload_results_to_s3(self, results_folder: Path, model_id: str) -> None:
        """Log only in dev mode - no actual upload."""
        # NOTE FlipConstants.UPLOADED_FEDERATED_DATA_BUCKET is not available in dev mode, so we can't log it here.
        self.logger.info(
            "[DEV] upload_results_to_s3 is not supported in LOCAL_DEV mode."
            f"Details of the function call: uploading results from {results_folder} for model {model_id}."
        )

    @override
    def cleanup(self, path: Path) -> None:
        """
        Cleans up local files in LOCAL_DEV mode. Logs the cleanup action but does not actually delete any files.
        """
        self.logger.info(f"[DEV] cleanup is not supported in LOCAL_DEV mode. Would have cleaned up path: {path}")
