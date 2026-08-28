# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Fixtures for the flip.xnat tests."""

import pytest

from tests.unit.xnat.helpers import FakeSession, project_routes


@pytest.fixture
def project_session() -> FakeSession:
    """FakeSession: A project with two single-scan experiments, each holding one image."""
    return FakeSession(project_routes(["input_spleen_2.nii.gz"]))
