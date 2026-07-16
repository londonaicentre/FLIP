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
FLIP Base Classes.

This module contains the abstract base class for all FLIP implementations.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pandas as pd

from flip.constants.flip_constants import ModelStatus, ResourceType
from flip.schemas import FLLogEvent


class FLIPBase(ABC):
    """
    Abstract base class for FLIP functionality across all job types.

    This class defines the interface that all FLIP implementations must follow.
    Concrete implementations handle the differences between development and
    production environments, as well as job-type-specific behavior.
    """

    def __init__(self):
        self._name = self.__class__.__name__
        self.logger = logging.getLogger(self._name)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # so logs don't get filtered by root
        if not self.logger.hasHandlers():
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - FLIP - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    # ======================================================
    # Abstract Methods - Must be implemented by subclasses
    # ======================================================

    @abstractmethod
    def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
        """
        Returns a dataframe for the project/query.

        Args:
            project_id (str): The project identifier
            query (str): SQL query string

        Returns:
            pd.DataFrame: Dataframe containing the query results
        """

    @abstractmethod
    def get_by_accession_number(
        self,
        project_id: str,
        accession_id: str,
        resource_type: ResourceType | list[ResourceType] = ResourceType.NIFTI,
    ) -> Path:
        """
        Returns the path to the data for the given accession number.

        Args:
            project_id (str): The project identifier
            accession_id (str): The accession ID of the imaging study
            resource_type (ResourceType | list[ResourceType]): Type(s) of resources to download

        Returns:
            Path: Path to the downloaded data
        """

    @abstractmethod
    def add_resource(
        self,
        project_id: str,
        accession_id: str,
        scan_id: str,
        resource_id: str,
        files: list[str],
    ) -> None:
        """
        Adds specific image to XNAT for an accession ID.

        Args:
            project_id (str): The project identifier
            accession_id (str): The accession ID
            scan_id (str): The scan ID
            resource_id (str): The resource type ID
            files (list[str]): List of file paths to upload
        """

    @abstractmethod
    def update_status(self, model_id: str, new_model_status: ModelStatus) -> None:
        """
        Updates training status in Central Hub.

        Args:
            model_id (str): The model UUID
            new_model_status (ModelStatus): The new status to set
        """

    @abstractmethod
    def send_metrics(
        self,
        client_name: str,
        model_id: str,
        label: str,
        value: float,
        global_round: int,
        x_value: float | None = None,
        x_label: str | None = None,
    ) -> None:
        """
        Sends a metric value to the Central Hub.

        Args:
            client_name (str): The client name sending the metric
            model_id (str): The model UUID
            label (str): The label of the metric
            value (float): The value of the metric
            global_round (int): Provenance — the FL global round the metric is reported in. Always the
                true round; it is NOT the plot coordinate (that's ``x_value``).
            x_value (float | None): The x-coordinate the metric is plotted at (e.g. an epoch counter).
                ``None`` plots it at ``global_round``.
            x_label (str | None): Label naming the x-axis the metric is plotted against. ``None`` lets the
                hub default it to "Global Round". A plot's identity is (label, x_label) — see FLIP#148.
        """

    @abstractmethod
    def send_handled_exception(self, formatted_exception: str, client_name: str | None, model_id: str) -> None:
        """
        Sends a training-related exception to Central Hub.

        Args:
            formatted_exception (str): The formatted exception message
            client_name (str | None): The client name that raised the exception; None when
                the client cannot be identified, so the hub records it model-level
            model_id (str): The model UUID
        """

    @abstractmethod
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

        The event carries facts only — the hub composes the display text at
        serve time, so wording never lives in FL images. Best-effort like every
        hub call: a failed post is logged and never breaks training.

        Args:
            model_id (str): The model UUID
            event_type (FLLogEvent): Which round event this is
            global_round (int): The 1-based federated round the event belongs to
            client_name (str | None): FL client identity for trust-attributed
                events (e.g. CLIENT_RESULT_RECEIVED); None for hub-attributed ones
            details (dict[str, Any] | None): Event-specific facts (total_rounds,
                size_bytes, returned/expected counts)
            success (bool): Whether the event marks a healthy step
        """

    @abstractmethod
    def upload_results_to_s3(self, results_folder: Path, model_id: str) -> None:
        """
        Uploads results to S3 bucket.

        Args:
            results_folder (Path): The folder containing results to upload
            model_id (str): The model UUID for which results are being uploaded
        """

    @abstractmethod
    def cleanup(self, path: Path) -> None:
        """
        Cleans up local files.

        Args:
            path (Path): The path to the file or directory to clean up
        """

    # ======================================================
    # Concrete Validation Methods - Shared across all implementations
    # ======================================================

    def check_query(self, query: str) -> None:
        """
        Check whether the query is a string type.

        Args:
            query (str): The query to validate

        Raises:
            TypeError: If query is not a string
        """
        if not isinstance(query, str):
            raise TypeError(f"expect query to be string, but got {type(query)}")

    def check_project_id(self, project_id: str) -> None:
        """
        Checks whether the project id is a string type.

        Args:
            project_id (str): The project ID to validate

        Raises:
            TypeError: If project_id is not a string
        """
        if not isinstance(project_id, str):
            raise TypeError(f"expect project_id to be string, but got {type(project_id)}")

    def check_accession_id(self, accession_id: str) -> None:
        """
        Checks whether accession_id is a string type.

        Args:
            accession_id (str): The accession ID to validate

        Raises:
            TypeError: If accession_id is not a string
        """
        if not isinstance(accession_id, str):
            raise TypeError(f"expect accession_id to be string, but got {type(accession_id)}")

    def check_resource_type(self, resource_type: ResourceType | list[ResourceType]) -> list[ResourceType]:
        """
        Check whether resource type is valid and returns them reformatted.

        Args:
            resource_type (ResourceType | list[ResourceType]): Single ResourceType or list of ResourceTypes

        Returns:
            list[ResourceType]: List of validated resource types

        Raises:
            TypeError: If resource_type is not valid
        """
        if isinstance(resource_type, ResourceType):
            resources = [resource_type]
        elif isinstance(resource_type, list):
            if not all(isinstance(r, ResourceType) for r in resource_type):
                raise TypeError("Each item in resource_type list must be a ResourceType")
            resources = resource_type
        else:
            raise TypeError(f"resource_type must be ResourceType or list of ResourceType, got {type(resource_type)}")
        return resources
