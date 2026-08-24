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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class UserPermissionsResponse(BaseModel):
    """Response model for user permissions."""

    permissions: list[str] = Field(..., description="List of permissions assigned to the user.")


class Disabled(BaseModel):
    """Model for user disabled status."""

    disabled: bool


class UpdateUser(BaseModel):
    """Editable user state."""

    disabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    organisation: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_has_update(self) -> "UpdateUser":
        if self.disabled is None and self.name is None and self.organisation is None:
            raise ValueError("At least one user field must be supplied")
        return self


class UpdateUserResponse(BaseModel):
    """Updated user fields."""

    disabled: bool | None = None
    name: str | None = None
    organisation: str | None = None


class CognitoUser(BaseModel):
    """Model for Cognito user data."""

    id: UUID = Field(..., description="User ID from Cognito")
    email: EmailStr = Field(..., description="User's email address")
    name: str = Field(default="", description="User's display name")
    organisation: str = Field(default="", description="User's organisation")
    is_disabled: bool = Field(default=False, description="Indicates if the user is disabled", alias="isDisabled")

    model_config = ConfigDict(
        populate_by_name=True,
    )


class ProjectMemberLookup(BaseModel):
    """Minimal identity a project editor needs in order to add someone as a project member.

    Omits ``name`` and ``organisation``: the add-member flow in ``ProjectUsers.vue`` only ever
    reads id / email / isDisabled, and its endpoint is reachable by every Researcher (FLIP#907).

    Stated as its own model rather than a subclass of :class:`CognitoUser` because a pydantic
    subclass can only add fields, never drop them — so the narrowing has to be expressed here.
    (Inverting the hierarchy, so ``CognitoUser`` extends this, would remove the repetition and
    keep the same guarantee; that is a larger change to a widely-used schema.)
    """

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
