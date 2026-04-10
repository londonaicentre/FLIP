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

from typing import List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IRole(BaseModel):
    """Model for role."""

    id: UUID
    name: str = Field(..., description="Name of the role", alias="rolename")
    description: str = Field(..., description="Description of the role", alias="roledescription")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IRolesResponse(BaseModel):
    """Model for roles response."""

    roles: List[IRole] = Field(..., description="List of user roles")
