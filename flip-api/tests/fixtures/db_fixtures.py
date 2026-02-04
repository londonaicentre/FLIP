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

import factory

from flip_api.db.models.main_models import Model, ModelStatus, Projects, ProjectStatus, ProjectTrustIntersect, Trust
from flip_api.db.models.user_models import Role, User, UserRole
from flip_api.domain.interfaces.user import IRoles


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
    """Factory for creating User instances."""

    class Meta:
        model = User

    id = factory.LazyFunction(uuid.uuid4)
    email = factory.Faker("email")
    enabled = True
    created_at = factory.Faker("date_time")
    updated_at = factory.Faker("date_time")


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
    endpoint = factory.Faker("url")


class ProjectTrustIntersectFactory(factory.Factory):
    """Factory for creating ProjectTrustIntersect instances."""

    class Meta:
        model = ProjectTrustIntersect

    id = factory.LazyFunction(uuid.uuid4)
    project_id = factory.LazyFunction(uuid.uuid4)
    trust_id = factory.LazyFunction(uuid.uuid4)
    approved = True
