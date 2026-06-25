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

import uuid
from dataclasses import dataclass
from uuid import UUID

import factory

from flip_api.db.models.main_models import (
    Model,
    ModelStatus,
    Projects,
    ProjectStatus,
    ProjectTrustIntersect,
    Trust,
    TrustTask,
)
from flip_api.db.models.user_models import Role, UserRole
from flip_api.domain.interfaces.user import IRoles
from flip_api.domain.schemas.status import TaskStatus, TaskType


@dataclass
class _Identity:
    """Stand-in for a Cognito user identity in tests.

    Cognito is the source of truth for user identity — there is no local
    users table — so the only thing test fixtures need is a
    Cognito-sub-shaped ``id`` (and optionally an ``email``) to thread
    through ``UserRole.user_id``, ``Projects.owner_id``, etc. This
    lightweight type lets ``UserFactory`` keep producing objects with the
    ``.id`` / ``.email`` / ``.is_disabled`` attributes test code already
    expects, mirroring the real :class:`~flip_api.domain.schemas.users.CognitoUser`
    shape closely enough for UID-threading without pulling Pydantic in.
    """

    id: UUID
    email: str
    is_disabled: bool = False


class ProjectFactory(factory.Factory):
    """Factory for creating Project instances."""

    class Meta:
        model = Projects

    id = factory.Faker("uuid4")
    name = factory.Faker("word")
    description = factory.Faker("sentence")
    owner_id = factory.Faker("uuid4")
    deleted = factory.Faker("boolean")
    creation_timestamp = factory.Faker("date_time")
    status = ProjectStatus.APPROVED


class ModelFactory(factory.Factory):
    """Factory for creating Model instances."""

    class Meta:
        model = Model

    id = factory.LazyFunction(uuid.uuid4)
    name = factory.Faker("word")
    description = factory.Faker("sentence")
    status = ModelStatus.PREPARED
    deleted = factory.Faker("boolean")
    project_id = factory.LazyFunction(uuid.uuid4)
    owner_id = factory.LazyFunction(uuid.uuid4)
    creation_timestamp = factory.Faker("date_time")


class UserFactory(factory.Factory):
    """Factory for a Cognito-shaped identity (id + email + is_disabled).

    No DB row is produced — the result is a plain dataclass; tests use
    ``user_factory().id`` as a stand-in for a Cognito ``sub`` UUID. The
    factory survives the removal of the local users table so existing
    tests that read ``.id`` / ``.email`` keep working without touching
    every callsite.
    """

    class Meta:
        model = _Identity

    id = factory.LazyFunction(uuid.uuid4)
    email = factory.Faker("email")
    is_disabled = False


class RoleFactory(factory.Factory):
    """Factory for creating Role instances."""

    class Meta:
        model = Role

    id = factory.LazyFunction(uuid.uuid4)
    name = factory.Faker("word")
    description = factory.Faker("sentence")
    created_at = factory.Faker("date_time")
    updated_at = factory.Faker("date_time")


class RolesFactory(factory.Factory):
    """Factory for creating Roles instances."""

    class Meta:
        model = IRoles

    roles = [uuid.uuid4() for _ in range(2)]


class UserRoleFactory(factory.Factory):
    """Factory for creating UserRole instances."""

    class Meta:
        model = UserRole

    user_id = factory.LazyFunction(uuid.uuid4)
    role_id = factory.LazyFunction(uuid.uuid4)


class TrustFactory(factory.Factory):
    """Factory for creating Trust instances."""

    class Meta:
        model = Trust

    id = factory.LazyFunction(uuid.uuid4)
    name = factory.Faker("word")
    last_heartbeat = None


class ProjectTrustIntersectFactory(factory.Factory):
    """Factory for creating ProjectTrustIntersect instances."""

    class Meta:
        model = ProjectTrustIntersect

    id = factory.LazyFunction(uuid.uuid4)
    project_id = factory.LazyFunction(uuid.uuid4)
    trust_id = factory.LazyFunction(uuid.uuid4)
    approved = True


class TrustTaskFactory(factory.Factory):
    """Factory for creating TrustTask instances."""

    class Meta:
        model = TrustTask

    id = factory.LazyFunction(uuid.uuid4)
    trust_id = factory.LazyFunction(uuid.uuid4)
    task_type = TaskType.COHORT_QUERY
    payload = '{"query": "SELECT 1"}'
    status = TaskStatus.PENDING
    created_at = factory.Faker("date_time")
