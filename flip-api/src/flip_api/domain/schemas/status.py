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

from enum import Enum

# ---------------------------
# Enums
# ---------------------------


class BucketStatus(Enum):
    """Status of the bucket."""

    CLEAN = "clean"
    INFECTED = "infected"
    NO = "no"


class BucketAction(Enum):
    """Action to be taken on the bucket."""

    DELETE = "delete"
    TAG = "tag"
    NO = "no"


class ClientDeployResponse(str, Enum):
    """Response for client deployment."""

    OK = "OK"


class ClientStatus(str, Enum):
    """Status of the client."""

    # TODO we might want to reconcile these with the FL API responses
    # FLARE FL API returns
    NO_REPLY = "no_reply"
    NO_JOBS = "no_jobs"
    # Flower FL API returns
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class JobStatus(Enum):
    """Status of the job."""

    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELETED = "DELETED"


class ModelStatus(Enum):
    """Status of the model."""

    ERROR = "ERROR"
    STOPPED = "STOPPED"
    PENDING = "PENDING"
    INITIATED = "INITIATED"
    PREPARED = "PREPARED"
    TRAINING_STARTED = "TRAINING_STARTED"
    RESULTS_UPLOADED = "RESULTS_UPLOADED"


class NetStatus(Enum):
    """Status of the net."""

    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"


class FLTargets(str, Enum):
    """Targets for FL backend."""

    SERVER = "server"
    CLIENT = "client"
    ALL = "all"


class FileUploadStatus(Enum):
    """Status of the file upload."""

    SCANNING = "SCANNING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class FileUploadTag(Enum):
    """Tag for the file upload."""

    MODEL = "MODEL"
    DATA_OPENER = "DATA_OPENER"
    OBJECTIVE_TARGET = "OBJECTIVE_TARGET"


class FLStatus(str, Enum):
    """Status of the FL."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    ERROR_RUNTIME = "ERROR_RUNTIME"
    ERROR_SYNTAX = "ERROR_SYNTAX"
    ERROR_AUTHENTICATION = "ERROR_AUTHENTICATION"


class ProjectStatus(str, Enum):
    """Status of the project."""

    UNSTAGED = "UNSTAGED"
    STAGED = "STAGED"
    APPROVED = "APPROVED"


class ServerEngineStatus(str, Enum):
    """Status of the server engine."""

    STARTED = "started"
    STOPPED = "stopped"
    STARTING = "starting"
    NOT_STARTED = "not started"
    SHUTDOWN = "shutdown"


class TrustIntersectStatus(str, Enum):
    """Status of the trust intersect."""

    PENDING = "PENDING"
    REQUEST_SENT = "REQUEST_SENT"
    INITIALISED = "INITIALISED"


class TaskStatus(str, Enum):
    """Status of a trust task in the task queue."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskType(str, Enum):
    """Type of task dispatched to a trust."""

    COHORT_QUERY = "cohort_query"
    CREATE_IMAGING = "create_imaging"
    DELETE_IMAGING = "delete_imaging"
    GET_IMAGING_STATUS = "get_imaging_status"
    REIMPORT_STUDIES = "reimport_studies"
    UPDATE_USER_PROFILE = "update_user_profile"


class XNATImageStatus(str, Enum):
    """Status of the XNAT imaging project."""

    RETRIEVE_STARTED = "RETRIEVE_STARTED"
    RETRIEVE_COMPLETED = "RETRIEVE_COMPLETED"
    RETRIEVE_IN_PROGRESS = "RETRIEVE_IN_PROGRESS"
    RETRIEVE_ERROR = "RETRIEVE_ERROR"
    CREATED = "CREATED"
    DELETED = "DELETED"
