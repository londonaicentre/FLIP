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

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class IRoles(BaseModel):
    """Model for user roles."""

    roles: list[UUID] = Field(..., description="List of role GUIDs assigned to the user")


class IRegisterUser(IRoles):
    """Model for user data."""

    email: EmailStr = Field(..., description="User's email address")


class IUserResponse(IRegisterUser):
    """Model for user creation response."""

    user_id: UUID = Field(..., description="User ID from Cognito", alias="userId")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IRegisterUserDto(BaseModel):
    """Data transfer object for registering a user."""

    user_id: UUID = Field(..., description="User ID from Cognito")
    email: EmailStr = Field(..., description="User's email address")
    roles: list[UUID] = Field(..., description="List of role GUIDs assigned to the user")
