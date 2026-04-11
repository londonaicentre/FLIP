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

from typing import TypedDict


class ICohortResponseAge(TypedDict):
    """Age statistics for the cohort response."""

    mean: float


class ICohortResponseGender(TypedDict):
    """Gender statistics for the cohort response."""

    male: int
    female: int
    missing_data: int


class ICohortResponseClientVisit(TypedDict):
    """Client visit statistics for the cohort response."""

    inpatient: int
    emergency: int
    missing_data: int


class IResultsStats(TypedDict):
    """Statistics for the results."""

    total_count: int
    age: ICohortResponseAge
    gender: ICohortResponseGender
    client_visit: ICohortResponseClientVisit


class ICohortResultsAge(TypedDict):
    """Age statistics for the cohort response."""

    mean: float


class ICohortResultsGender(TypedDict):
    """Gender statistics for the cohort response."""

    male: int
    female: int
    missing_data: int


class ICohortResultsClientVisit(TypedDict):
    """Client visit statistics for the cohort response."""

    inpatient: int
    emergency: int
    missing_data: int


class IIncomingResultsStats(TypedDict):
    """Statistics for incoming results."""

    total_count: int
    age: ICohortResultsAge
    gender: ICohortResultsGender
    client_visit: ICohortResultsClientVisit


class ICohortResultDataResponse(TypedDict):
    """Cohort result data response."""

    trust_name: list[str]
    trust_id: list[str]
    data: list[str]


class IDbData(TypedDict):
    """Database data."""

    trust_name: list[str]
    trust_id: list[str]
    data: list[IResultsStats]


class IUpdateRetrieveImageStatus(TypedDict):
    """Update retrieve image status."""

    trust_id: str
    xnat_project_id: str


class ITrainingMetrics(TypedDict):
    """Training metrics."""

    trust: str
    global_round: int
    label: str
    result: float
