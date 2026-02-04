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

from flip_api.domain.schemas.status import BucketAction, BucketStatus

# TODO Review these as they are not used, they should probably be removed and replaced with pydantic models in schemas


class ScannedFileSns(ABC):
    """Python equivalent of IScannedFileSns interface"""

    @property
    @abstractmethod
    def message(self) -> str:
        """Get the SNS message"""
        ...


class ScannedFileMessage(ABC):
    """Python equivalent of IScannedFileMessage interface"""

    @property
    @abstractmethod
    def bucket(self) -> str:
        """Get the bucket name"""
        ...

    @property
    @abstractmethod
    def key(self) -> str:
        """Get the object key"""
        ...

    @property
    @abstractmethod
    def status(self) -> BucketStatus:
        """Get the bucket status"""
        ...

    @property
    @abstractmethod
    def action(self) -> BucketAction:
        """Get the bucket action"""
        ...

    @property
    @abstractmethod
    def finding(self) -> str:
        """Get the finding details"""
        ...


class ModelFiles(ABC):
    """Python equivalent of IModelFiles interface"""

    @property
    @abstractmethod
    def opener(self) -> str:
        """Get the opener file"""
        ...

    @property
    @abstractmethod
    def algo(self) -> str:
        """Get the algorithm file"""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Get the model file"""
        ...


class ModelFileDownload(ABC):
    """Python equivalent of IModelFileDownload interface"""

    @property
    @abstractmethod
    def modelId(self) -> str:
        """Get the model ID"""
        ...

    @property
    @abstractmethod
    def user_id(self) -> str:
        """Get the user ID"""
        ...

    @property
    @abstractmethod
    def file_name(self) -> str:
        """Get the file name"""
        ...


class ModelFileDelete(ModelFileDownload):
    """Python equivalent of IModelFileDelete interface, which extends IModelFileDownload"""

    # This class inherits all abstract methods from ModelFileDownload
    ...
