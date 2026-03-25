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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserPermissionsResponse(BaseModel):
    """Response model for user permissions."""

    permissions: list[str] = Field(..., description="List of permissions assigned to the user.")


class GetUser(BaseModel):
    """Model for retrieving a user by ID or email."""

    userId: str = Field(..., description="User identifier (email or UUID)")


class GetUserByEmail(GetUser):
    """Model specifically for retrieving a user by email."""

    userId: EmailStr


class GetUserById(GetUser):
    """Model specifically for retrieving a user by UUID."""

    @field_validator("userId")
    def validate_uuid(cls, v):
        try:
            UUID(v)
        except ValueError:
            raise ValueError("'userId' must be a valid GUID")
        return v


class Disabled(BaseModel):
    """Model for user disabled status."""

    disabled: bool


class CognitoUser(BaseModel):
    """Model for Cognito user data."""

    id: UUID = Field(..., description="User ID from Cognito")
    email: EmailStr = Field(..., description="User's email address")
    is_disabled: bool = Field(default=False, description="Indicates if the user is disabled", alias="isDisabled")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class IRole(BaseModel):
    """Model for role data."""

    id: UUID
    rolename: str
    roledescription: str


class IUser(CognitoUser):
    """Model for user data."""

    roles: list[IRole]
