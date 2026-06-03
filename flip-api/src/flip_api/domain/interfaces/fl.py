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

import json
from enum import Enum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from flip_api.domain.schemas.status import ClientStatus, FLJobStatus
from flip_api.domain.schemas.types import FLBackend
from flip_api.utils.constants import job_types_required_files_name
from flip_api.utils.logger import logger

# Default job type used when config.json declares none. The job-type vocabulary itself is data
# (the per-backend manifest keys); this is the one name that legitimately lives in code, as the
# fallback. Mirrors the UI's DEFAULT_JOB_TYPE.
DEFAULT_JOB_TYPE = "standard"

# Directory where the per-backend required-files manifests are downloaded at runtime.
# The manifests themselves are never committed to source control (gitignored) — S3 is the
# single source of truth, pulled per FL backend on model creation. See pull_required_files.py.
ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"
# Ensure the directory exists so the manifest pull and load have a target on a clean checkout /
# fresh Docker volume (the dir is not tracked in git — only its manifest contents are gitignored).
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def required_job_types_file(fl_backend: FLBackend) -> Path:
    """Local path of the per-backend job-types/required-files manifest.

    Args:
        fl_backend (FLBackend): The FL backend the manifest belongs to (``nvflare`` or ``flower``).

    Returns:
        Path: Absolute path to the per-backend manifest under the assets directory.
    """
    return ASSETS_DIR / job_types_required_files_name(fl_backend)


def _load_job_types_config(fl_backend: FLBackend) -> dict[str, list[str]]:
    """Loads the job types configuration for a backend from its on-disk manifest.

    The manifest is pulled from S3 at runtime; if it is missing (e.g. S3 was unreachable
    when the model was created) an empty mapping is returned so the API does not crash.

    Args:
        fl_backend (FLBackend): The FL backend whose manifest to load (``nvflare`` or ``flower``).

    Returns:
        dict[str, list[str]]: A dictionary mapping job type names to their required files,
        or an empty dict if the manifest is absent or malformed.
    """
    path = required_job_types_file(fl_backend)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Benign: the manifest is pulled from S3 on model creation, so it is legitimately
        # absent on first boot / a fresh volume before any model exists.
        logger.debug(f"Required-files manifest not present at {path}; returning empty job-type map.")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        # A present-but-unreadable or corrupt manifest is a real error worth a traceback.
        logger.warning(f"Could not load required-files manifest {path}: {e}", exc_info=True)
        return {}


class IStartTrainingBody(BaseModel):
    project_id: str
    cohort_query: str
    trusts: list[str]
    bundle_urls: list[str]


class ISchedulerResponse(BaseModel):
    id: UUID
    netId: UUID


class IJobResponse(BaseModel):
    """Internal handoff from `check_for_queued_jobs` to `prepare_and_start_training`.

    Carries the trust ids that were attached to the FL job via the `fl_job_trust` link table;
    `prepare_and_start_training` re-fetches the Trust rows from the DB before talking to the
    FL backend, so we don't try to ferry ORM objects through a Pydantic schema.
    """

    id: UUID  # FLJob table primary key
    model_id: UUID
    trust_ids: list[UUID]


class IJobMetaData(BaseModel):
    """Job metadata as returned by an FL-API adapter's ``GET /list_jobs``.

    The shared job-metadata contract (GitHub issue #490). flip-api correlates
    ``model_id`` <-> ``job_id`` in its own ``fl_job`` table, so the contract carries
    only ``job_id`` + ``status``.
    """

    model_config = ConfigDict(extra="ignore")

    job_id: str
    status: FLJobStatus


class IRequiredTrainingInformation(BaseModel):
    project_id: str
    cohort_query: str


class IInitiateTrainingInputPayload(BaseModel):
    """Request body for `POST /fl/initiate/{model_id}`.

    The selected trusts are the FL participants for this training run. Trusts are
    identified by their UUID `id` (not name): names are admin-chosen, non-unique,
    and may contain arbitrary characters, so they are unsafe to match on. The ids
    are looked up against the `trust` table at request time — see
    `initiate_training` for the existence check.
    """

    model_config = ConfigDict(extra="forbid")

    trust_ids: list[UUID] = Field(
        min_length=1,
        description="IDs of trusts to participate in training. Must be non-empty and unique.",
    )

    @field_validator("trust_ids")
    @classmethod
    def must_be_unique(cls, v: list[UUID]) -> list[UUID]:
        if len(set(v)) != len(v):
            raise ValueError("'trust_ids' must all be unique entries")
        return v


class INetDetails(BaseModel):
    name: str
    endpoint: str
    # The FL backend this net runs. Derived from the non-null FLNets.fl_backend column, seeded
    # from FL_BACKEND and canonical (no runtime reconciliation), so it is always set.
    fl_backend: FLBackend


class IServerStatus(BaseModel):
    """Defines the status of the server."""

    model_config = ConfigDict(extra="ignore")

    status: str


class IClientStatus(BaseModel):
    """Defines the status of a client."""

    model_config = ConfigDict(extra="ignore")

    name: str
    # The trust's short code (e.g. "GSTT"). None for raw FL-server client
    # statuses; set when the client is resolved to a registered trust.
    code: str | None = None
    status: str
    # The FL kit slot backing this client (the pre-provisioned participant
    # identity, e.g. "Trust_1"). The trust's display name (`name`) can differ
    # from the slot once a trust is registered under an arbitrary name, so the
    # slot is surfaced for the connection-status detailed view. None for raw
    # FL-server client statuses (which are already keyed by slot).
    fl_kit_slot: str | None = None

    # set the online status based on the client status
    # This property will be included in dumps / JSON schemas
    @computed_field  # type: ignore[prop-decorator]
    @property
    def online(self) -> bool:
        offline_statuses = {
            ClientStatus.NO_REPLY.value,
            ClientStatus.DISCONNECTED.value,
        }
        return self.status not in offline_statuses


class INetStatus(BaseModel):
    name: str
    # Always set: derived from the non-null FLNets.fl_backend column (seeded + reconciled).
    fl_backend: FLBackend
    online: bool | None = None
    registered_clients: int | None = None
    net_in_use: bool | None = None
    clients: list[IClientStatus]


class IOverridableConfig(BaseModel):
    LOCAL_ROUNDS: int | None = None
    GLOBAL_ROUNDS: int | None = None
    IGNORE_RESULT_ERROR: bool | None = None
    AGGREGATOR: str | None = None
    AGGREGATION_WEIGHTS: dict[str, float] | None = None


class FLAggregators(Enum):
    """Enumeration for different FL aggregators"""

    InTimeAccumulateWeightedAggregator = "InTimeAccumulateWeightedAggregator"
    AccumulateWeightedAggregator = "AccumulateWeightedAggregator"


class AggregationWeights:
    MinimumAggregationWeight = 0
    MaximumAggregationWeight = 1


class JobRequiredFiles(BaseModel):
    @classmethod
    def get_required_files(cls, job_type: str, fl_backend: FLBackend) -> list[str]:
        """Returns the list of required files for a job type on a given backend.

        Always reloads from the per-backend on-disk manifest.

        Args:
            job_type (str): The job type to look up.
            fl_backend (FLBackend): The FL backend whose manifest to read (``nvflare`` or ``flower``).

        Returns:
            list[str]: Required file names for ``job_type``, or an empty list if the job type
            (or the manifest) is not present on disk.
        """
        config = _load_job_types_config(fl_backend)
        return config.get(job_type, [])

    @classmethod
    def is_valid_job_type(cls, job_type: str, fl_backend: FLBackend) -> bool:
        """Whether ``job_type`` is defined in the backend's on-disk manifest.

        The set of valid job types is data (the manifest keys), not a hard-coded enum, so adding
        a job type is purely an S3 manifest + base-application change.

        Args:
            job_type (str): The job type to validate.
            fl_backend (FLBackend): The FL backend whose manifest to read (``nvflare`` or ``flower``).

        Returns:
            bool: True if ``job_type`` is a key in the backend's manifest.
        """
        return job_type in _load_job_types_config(fl_backend)

    @classmethod
    def get_all_job_types_with_files(cls, fl_backend: FLBackend) -> dict[str, list[str]]:
        """Returns all job types with their required files for a given backend.

        Always reloads from the per-backend on-disk manifest.

        Args:
            fl_backend (FLBackend): The FL backend whose manifest to read (``nvflare`` or ``flower``).

        Returns:
            dict[str, list[str]]: A copy of the on-disk mapping of job type names to their
            required files.
        """
        return _load_job_types_config(fl_backend).copy()
