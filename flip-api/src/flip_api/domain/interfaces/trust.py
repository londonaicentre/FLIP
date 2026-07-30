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

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from flip_api.domain.schemas.users import CognitoUser

# Interfaces


class ITrustStatus(BaseModel):
    """Trust list item with connection status, readable by any authenticated user.

    Backs ``GET /trust`` — the single list-of-trusts endpoint — powering both the
    trust pickers (project staging, cohort query, charts) and the Connection
    Status page (trust table + topology). Every field is benign trust metadata —
    no secrets — so the endpoint is intentionally not admin-gated. Creating a
    trust (``POST /admin/trusts``) stays admin-only.
    """

    id: UUID
    name: str
    code: str | None = None
    region: str | None = None
    # last_heartbeat is surfaced as a string with an explicit UTC marker (Z) so
    # the browser doesn't reinterpret a naive datetime as local time and skew the
    # staleness calc. The DB column is `timestamp without time zone` but the
    # values are written via datetime.now(timezone.utc), so they're already UTC —
    # we just need to tag it on the wire.
    last_heartbeat: str | None = None
    project_count: int = 0


class ICreateTrust(BaseModel):
    name: str
    code: str = Field(min_length=1, description="Short trust code, e.g. GSTT. Required.")
    region: str | None = None


class ICreatedTrust(BaseModel):
    """Response for POST /admin/trusts.

    `trust_api_key` and `trust_internal_service_key` are plaintext and are returned
    exactly once. The hub only stores the SHA-256 of the api key; the internal
    service key is not persisted (only used by trust-internal services).

    `trust_aes_key` / `trust_aes_kid` are the trust's payload-encryption key and its
    key-id. Returned exactly once and stored nowhere hub-side — unlike the api key a
    symmetric key cannot be reduced to a hash, so an admin who does not record it here
    has to re-register the trust to get another. Carried for the same reason as the
    other credentials: this response is the source for the Kit-credentials block of
    ``trust/.env.<CODE>.<env>``, and the key is one of those credentials. It stays
    inert until an operator also adds it to the hub's ``AES_TRUST_KEYS`` map
    (``docs/aes-payload-keys.md``); until then payloads use the shared key.

    `fl_kit_slot` is the pre-provisioned FL participant identity assigned to this
    trust from the shared pool. The operator's containers mount the matching
    ``workspace/net-N/services/<fl_kit_slot>/`` provisioned kit dirs; this is the
    name the FL server sees on registration (independent of `name`).
    """

    id: UUID
    name: str
    code: str | None = None
    region: str | None = None
    created_at: datetime | None = None
    trust_api_key: str
    trust_internal_service_key: str
    trust_aes_key: str
    trust_aes_kid: str
    fl_kit_slot: str
    fl_kit_slot_number: int


class ITrustHealth(BaseModel):
    trust_id: UUID = Field(..., alias="trustId")
    trust_name: str = Field(..., alias="trustName")
    online: bool

    model_config = ConfigDict(
        populate_by_name=True,
    )


class ITrust(BaseModel):
    id: UUID
    name: str
    code: str | None = Field(default=None, description="Short trust code, e.g. GSTT")
    fl_client_endpoint: str | None = Field(default=None, description="FL Client Endpoint URL")


class ICreateImagingProject(BaseModel):
    """Represents a project on the central hub from which an imaging project is created on XNAT."""

    project_id: UUID  # This is the central hub project ID
    trust_id: UUID
    project_name: str  # This is the name of the project on the central hub
    query: str | None = None
    users: list[CognitoUser] = []
    dicom_to_nifti: bool = True


class ICreatedImagingUser(BaseModel):
    """Represents a user created on XNAT. Used to be called IImageUser in the old repo."""

    username: str
    encrypted_password: str
    email: EmailStr


class IAddedImagingUser(BaseModel):
    """Represents an existing XNAT user who was added to an imaging project (no new credentials)."""

    username: str
    email: EmailStr


class ICreatedImagingProject(BaseModel):
    """Represents a project created on XNAT. Used to be called IImageId in the old repo."""

    imaging_project_id: UUID
    name: str
    created_users: list[ICreatedImagingUser]
    added_users: list[IAddedImagingUser] = []


class ISesTemplateData(BaseModel):
    trust_name: str
    project_name: str
    project_id: UUID
    username: str
    password: str


class ISesProjectAccessTemplateData(BaseModel):
    """Template data for notifying existing users they've been added to a project (no password)."""

    trust_name: str
    project_name: str
    project_id: UUID
    username: str
