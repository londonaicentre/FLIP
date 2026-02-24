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
from typing import List, Union

import pandas as pd

from flip.constants.flip_constants import ModelStatus, ResourceType


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
        resource_type: Union[ResourceType, List[ResourceType]] = ResourceType.NIFTI,
    ) -> Path:
        """
        Returns the path to the data for the given accession number.

        Args:
            project_id (str): The project identifier
            accession_id (str): The accession ID of the imaging study
            resource_type (Union[ResourceType, List[ResourceType]]): Type(s) of resources to download

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
        files: List[str],
    ) -> None:
        """
        Adds specific image to XNAT for an accession ID.

        Args:
            project_id (str): The project identifier
            accession_id (str): The accession ID
            scan_id (str): The scan ID
            resource_id (str): The resource type ID
            files (List[str]): List of file paths to upload
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
    def send_metrics(self, client_name: str, model_id: str, label: str, value: float, round: int) -> None:
        """
        Sends a metric value to the Central Hub.

        Args:
            client_name (str): The client name sending the metric
            model_id (str): The model UUID
            label (str): The label of the metric
            value (float): The value of the metric
            round (int): The local round number
        """

    @abstractmethod
    def send_handled_exception(self, formatted_exception: str, client_name: str, model_id: str) -> None:
        """
        Sends a training-related exception to Central Hub.

        Args:
            formatted_exception (str): The formatted exception message
            client_name (str): The client name that raised the exception
            model_id (str): The model UUID
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

    def check_resource_type(self, resource_type: Union[ResourceType, List[ResourceType]]) -> List[ResourceType]:
        """
        Check whether resource type is valid and returns them reformatted.

        Args:
            resource_type (Union[ResourceType, List[ResourceType]]): Single ResourceType or list of ResourceTypes

        Returns:
            List[ResourceType]: List of validated resource types

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
