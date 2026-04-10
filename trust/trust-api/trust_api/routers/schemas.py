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

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# #########################
# Cohort
# #########################


class CohortQueryInput(BaseModel):
    """Represents the input for a cohort query."""

    encrypted_project_id: str = Field(..., description="The unique identifier for the project")
    query_id: str = Field(..., description="The unique identifier for the query")
    query_name: str = Field(..., description="A human-readable name for the query")
    query: str = Field(..., description="The raw SQL query to execute")
    trust_id: str = Field(..., description="The unique identifier for the trust")


# #########################
# Imaging
# #########################


class CentralHubUser(BaseModel):
    """Represents a user on the central hub."""

    id: UUID
    email: EmailStr
    is_disabled: bool = False


class CentralHubProject(BaseModel):
    """Represents a project on the central hub from which an imaging project is created on XNAT."""

    project_id: UUID  # This is the central hub project ID
    trust_id: UUID
    project_name: str
    query: Optional[str] = None
    users: List[CentralHubUser] = []


class UpdateProfileRequest(BaseModel):
    """Request body for updating a user's profile."""

    email: EmailStr
    enabled: bool
