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

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

TrimStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

NonEmptyUUIDList = Annotated[list[UUID], Field(min_length=1)]


class FLBackend(StrEnum):
    """The set of supported federated-learning backends.

    Defined once here so the value flows consistently through the DB column
    (``FLNets.fl_backend``), the FL interfaces, and every helper that
    resolves/bundles/pulls per backend. Add a new backend in this one place.

    ``StrEnum`` (not a bare ``Enum``) so each member *is* its lowercase string
    value: ``str(FLBackend.FLOWER) == "flower"``. That keeps env (``FL_BACKEND``),
    JSON API fields, S3 path segments and the varchar DB column all using the
    plain backend string, exactly as the previous ``Literal`` did.
    """

    NVFLARE = "nvflare"
    FLOWER = "flower"
