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

from abc import ABC, abstractmethod

try:
    # Python 3.12+
    from typing import override
except ImportError:  # typing-extensions fallback
    from typing_extensions import override
import json
import logging
import os
from pathlib import Path
from typing import List, Union

import pandas as pd
import requests
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import EventScope, FedEventHeader, FLContextKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from requests import HTTPError
from utils.flip_constants import FlipConstants, FlipEvents, ModelStatus, ResourceType
from utils.utils import Utils


class FLIP_Parent(ABC):
    def __init__(self):
        self._name = self.__class__.__name__
        self.logger = logging.getLogger(self._name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # so logs don’t get filtered by root
        if not self.logger.hasHandlers():
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - FLIP - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    @abstractmethod
    def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
        """Returns a dataframe for the project/query."""

    @abstractmethod
    def get_by_accession_number(
        self,
        project_id: str,
        accession_id: str,
        resource_type: Union[ResourceType, List[ResourceType]] = ResourceType.NIFTI,
    ) -> Path:
        """Returns the path to the data for the given accession number."""

    @abstractmethod
    def add_resource(
        self,
        project_id: str,
        accession_id: str,
        scan_id: str,
        resource_id: str,
        files: List[str],
    ) -> None:
        """Adds specific image to XNAT for an accession ID."""

    @abstractmethod
    def update_status(self, model_id: str, new_model_status: ModelStatus) -> None:
        """Updates training status in Central Hub."""

    @abstractmethod
    def send_metrics_value(self, label: str, value: float, round: int, fl_ctx: FLContext) -> None:
        """Sends metric value to the Central Hub."""

    @abstractmethod
    def handle_metrics_event(self, event_data: Shareable, global_round: int, model_id: str) -> None:
        """Handles FLIP metric firing event."""

    @abstractmethod
    def send_handled_exception(self, formatted_exception: str, client_name: str, model_id: str) -> None:
        """Sends a training-related exception to Central Hub."""

    def check_query(self, query: str) -> None:
        """Check whether the query is a string type"""
        if not isinstance(query, str):
            raise TypeError(f"expect query to be string, but got {type(query)}")

    def check_project_id(self, project_id: str) -> None:
        """Checks whether the project id is a string type."""
        if not isinstance(project_id, str):
            raise TypeError(f"expect project_id to be string, but got {type(project_id)}")

    def check_accession_id(self, accession_id: str) -> None:
        """Checks whether accession_id is a string type."""
        if not isinstance(accession_id, str):
            raise TypeError(f"expect accession_id to be string, but got {type(accession_id)}")

    def check_resource_type(self, resource_type: ResourceType) -> List[ResourceType]:
        """Check whether resource type is valid and returns them reformatted."""
        if isinstance(resource_type, ResourceType):
            resources = [resource_type]
        elif isinstance(resource_type, list):
            if not all(isinstance(r, ResourceType) for r in resource_type):
                raise TypeError("Each item in resource_type list must be a ResourceType")
            resources = resource_type
        else:
            raise TypeError(f"resource_type must be ResourceType or list of ResourceType, got {type(resource_type)}")
        return resources


# ======================================================
# Base Production Implementation
# ======================================================
class _FLIPProd(FLIP_Parent):
    def __init__(self):
        super().__init__()
        self._name = self.__class__.__name__
        self.logger = logging.getLogger(self._name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # so logs don’t get filtered by root

    @override
    def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
        """
        Retrieves the dataframe from the trust OMOP using the SQL query. Calls the FLIP data-access-api.

        Args:
            project_id (str): project identifier
            query (str): SQL query

        Returns:
            pd.DataFrame: dataframe containing the resulting accession ids and additional data.
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
        Calls the imaging-service to return a filepath that contains images downloaded from XNAT based
        on the accession number. Optional resource type parameter allows for downloading only the specified
        resource folders from XNAT.

        Args:
            project_id (str): The ID of the project.
            accession_id (str): The accession ID of the imaging study.
            resource_type (Union[ResourceType, List[ResourceType]]): The type of resource to download. Defaults to
            ResourceType.NIFTI.

        Raises:
            TypeError: if project_id is not a string
            TypeError: if accession_id is not a string
            TypeError: if resource_type is not a ResourceType or list of ResourceType

        Returns:
            Path: path to the downloaded data for that accession_id.
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
            # Determine assessor type based on resource type
            # Images (NIFTI, DICOM) are considered scans, while segmentations (SEG) are considered assessors in XNAT
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
        Calls the imaging-service to upload image(s) to XNAT based on the accession number, scan ID, and resource ID

        Args:
            project_id (str): unique project identifier
            accession_id (str): accession ID to upload the resource to
            scan_id (str): ID of the scan to upload
            resource_id (str): type of resource that is being uploaded (e.g. NIFTI)
            files (List[str]): list of files to upload

        Raises:
            TypeError: if project_id, accession_id, scan_id and resource_id are not strings, or if files is not a list.
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
        """Updates the model status on the Central Hub.

        Args:
            model_id (str): unique model identifier.
            new_model_status (ModelStatus): new model status value.

        Raises:
            ValueError: if model_id is not a valid UUID.
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
    def send_metrics_value(self, label: str, value: float, round: int, fl_ctx: FLContext) -> None:
        """
        Sends a metric value to the Central Hub.

        Args:
            label (str): The label of the metric.
            value (float): The value of the metric.
            round (int): The local round number.
            fl_ctx (FLContext): The federated learning context.

        Raises:
            TypeError: if label is not a string or fl_ctx is not an instance of FLContext.
        """
        if not isinstance(label, str):
            raise TypeError(f"expect label to be string, but got {type(label)}")

        if not isinstance(fl_ctx, FLContext):
            raise TypeError(f"expect fl_ctx to be FLContext, but got {type(fl_ctx)}")

        engine = fl_ctx.get_engine()
        if engine is None:
            self.logger.error("Error: no engine in fl_ctx, cannot fire metrics event")
            return

        self.logger.info("Attempting to fire metrics event...")

        dxo = DXO(data_kind=DataKind.METRICS, data={"label": label, "value": value, "round": round})
        event_data = dxo.to_shareable()

        fl_ctx.set_prop(FLContextKey.EVENT_DATA, event_data, private=True, sticky=False)
        fl_ctx.set_prop(
            FLContextKey.EVENT_SCOPE,
            value=EventScope.FEDERATION,
            private=True,
            sticky=False,
        )
        fl_ctx.set_prop(FLContextKey.EVENT_ORIGIN, "flip_client", private=True, sticky=False)

        engine.fire_event(FlipEvents.SEND_RESULT, fl_ctx)

        self.logger.info("Successfully fired metrics event")

    @override
    def handle_metrics_event(self, event_data: Shareable, global_round: int, model_id: str) -> None:
        """
        Use on the server to handle metrics data events raised by clients

        Args:
            event_data (Shareable): The event data containing the metrics.
            global_round (int): The global round number.
            model_id (str): The ID of the model.

        Raises:
            ValueError: if model_id is not a valid UUID.
            TypeError: if global_round is not an int.
            TypeError: if event_data is not a Shareable.
        """
        if Utils.is_valid_uuid(model_id) is False:
            raise ValueError(f"Invalid model ID: {model_id}, cant update model status")

        if not isinstance(global_round, int):
            raise TypeError(f"global_round must be type int but got {type(global_round)}")

        if not isinstance(event_data, Shareable):
            raise TypeError(f"event_data must be type Shareable but got {type(event_data)}")

        client_name = event_data.get_header(FedEventHeader.ORIGIN)
        metrics_data = from_shareable(event_data).data

        # Note the FL client 'site' name needs to match the trust name
        trust_name = client_name.replace("site-", "Trust_")

        # TODO Change when addressing https://github.com/londonaicentre/nhsflame/issues/428
        if "round" in metrics_data.keys():
            # New behaviour if client sends round in metrics
            payload = {
                "trust": trust_name,
                "globalRound": metrics_data["round"],
                "label": metrics_data["label"],
                "result": metrics_data["value"],
            }
        else:
            # Old behaviour if client doesn't send round in metrics
            payload = {
                "trust": trust_name,
                "globalRound": global_round,
                "label": metrics_data["label"],
                "result": metrics_data["value"],
            }

        endpoint = f"{FlipConstants.CENTRAL_HUB_API_URL}/model/{model_id}/metrics"

        self.logger.info(f"Attempting to handle metrics event raised by {client_name}...")

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={FlipConstants.PRIVATE_API_KEY_HEADER: FlipConstants.PRIVATE_API_KEY},
            )
            self.logger.info(f"Received response status code: {response.status_code}, response text: {response.text}")
            response.raise_for_status()

            self.logger.info(f"Successfully handled {client_name} metrics event")
        except HTTPError as http_err:
            self.logger.error(
                f"An http error occurred when handling a metrics event, see exception below | status code "
                f"{http_err.response.status_code}"
            )
            self.logger.exception(http_err)
        except Exception as e:
            self.logger.error("Something went wrong when handling metrics event, see exception below")
            self.logger.exception(e)

    @override
    def send_handled_exception(self, formatted_exception: str, client_name: str, model_id: str) -> None:
        """
        Sends a handled exception to the Central Hub.

        Args:
            formatted_exception (str): The formatted exception message.
            client_name (str): The name of the client that raised the exception.
            model_id (str): The ID of the model associated with the exception.

        Raises:
            TypeError: if client name, model or formatted_exception are not string.
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


# ======================================================
# Development Override Implementation
# ======================================================
class _FLIPDev(FLIP_Parent):
    def __init__(self):
        super(_FLIPDev, self).__init__()
        self._name = self.__class__.__name__
        self.logger = logging.getLogger(self._name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # so logs don’t get filtered by root

    @override
    def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
        """Retrieves the dataframe from the specified path.

        Args:
            project_id (str): project identifier
            query (str): SQL query

        Raises:
            ValueError: if accession_id is not a column in the CSV.

        Returns:
            pd.DataFrame: returns the resulting dataframe from the query.
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
        """Returns the path to the image directory for a specific accession ID. This will be looked for at
        DEV_IMAGES_DIR FlipConstant, and created if non-existent.

        Args:
            project_id (str): project identifier
            accession_id (str): accession_id to retrieve
            resource_type (Union[ResourceType, List[ResourceType]], optional): type of imaging resource. Defaults to
            ResourceType.NIFTI.

        Raises:
            TypeError: if project_id is not a string
            TypeError: if accession_id is not a string
            TypeError: if resource_type is not a ResourceType or list of ResourceType

        Returns:
            Path: path to the accession_id folder within the images folder.
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
        self.logger.info("[DEV] add_resource is not supported in LOCAL_DEV mode.")

    @override
    def update_status(self, model_id: str, new_model_status: ModelStatus) -> None:
        self.logger.info(
            "[DEV] update_status is not supported in LOCAL_DEV mode."
            f"Details of the function call: updating model status to {new_model_status}."
        )

    @override
    def send_metrics_value(self, label: str, value: float, round: int, fl_ctx: FLContext) -> None:

        if not isinstance(label, str):
            raise TypeError(f"expect label to be string, but got {type(label)}")

        if not isinstance(fl_ctx, FLContext):
            raise TypeError(f"expect fl_ctx to be FLContext, but got {type(fl_ctx)}")

        engine = fl_ctx.get_engine()
        if engine is None:
            self.logger.error("Error: no engine in fl_ctx, cannot fire metrics event")
            return

        self.logger.info("Attempting to fire metrics event...")

        dxo = DXO(data_kind=DataKind.METRICS, data={"label": label, "value": value, "round": round})
        event_data = dxo.to_shareable()

        fl_ctx.set_prop(FLContextKey.EVENT_DATA, event_data, private=True, sticky=False)
        fl_ctx.set_prop(
            FLContextKey.EVENT_SCOPE,
            value=EventScope.FEDERATION,
            private=True,
            sticky=False,
        )
        fl_ctx.set_prop(FLContextKey.EVENT_ORIGIN, "flip_client", private=True, sticky=False)

        engine.fire_event(FlipEvents.SEND_RESULT, fl_ctx)

        self.logger.info("Successfully fired metrics event")

    @override
    def handle_metrics_event(self, event_data: Shareable, global_round: int, model_id: str) -> None:
        client_name = event_data.get_header(FedEventHeader.ORIGIN)
        metrics_data = from_shareable(event_data).data

        # Note the FL client 'site' name needs to match the trust name
        trust_name = client_name.replace("site-", "Trust_")

        # TODO Change when addressing https://github.com/londonaicentre/nhsflame/issues/428
        if "round" in metrics_data.keys():
            # New behaviour if client sends round in metrics
            payload = {
                "trust": trust_name,
                "globalRound": metrics_data["round"],
                "label": metrics_data["label"],
                "result": metrics_data["value"],
            }
        else:
            # Old behaviour if client doesn't send round in metrics
            payload = {
                "trust": trust_name,
                "globalRound": global_round,
                "label": metrics_data["label"],
                "result": metrics_data["value"],
            }

        self.logger.info(f"[DEV] Trust: {trust_name} is sending payload={payload} to Central Hub.")

    @override
    def send_handled_exception(self, formatted_exception: str, client_name: str, model_id: str) -> None:
        self.logger.info(
            "[DEV] send_handled_exception is not supported in LOCAL_DEV mode."
            f"Details of the function call: sending {formatted_exception} for {client_name}."
        )


# ======================================================
# Environment-based Alias
# ======================================================
FLIP = _FLIPDev if FlipConstants.LOCAL_DEV else _FLIPProd
