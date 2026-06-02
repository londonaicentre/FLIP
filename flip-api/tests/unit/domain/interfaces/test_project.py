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

import pytest
from pydantic import ValidationError

from flip_api.domain.interfaces.project import IEditProject, IProjectDetails


class TestProjectNameXmlControlCharRejection:
    """
    Project ``name`` fields on hub-side schemas must reject XML control
    characters so they cannot reach the imaging-api XNAT projectData payload
    as un-escaped content.
    """

    @pytest.mark.parametrize("bad_name", ["evil<tag>", "name>injected", "amp&injection"])
    def test_iproject_details_rejects_xml_control_chars(self, bad_name: str):
        with pytest.raises(ValidationError, match="XML control characters"):
            IProjectDetails(name=bad_name, description="d", users=[])

    @pytest.mark.parametrize("bad_name", ["evil<tag>", "name>injected", "amp&injection"])
    def test_iedit_project_rejects_xml_control_chars(self, bad_name: str):
        with pytest.raises(ValidationError, match="XML control characters"):
            IEditProject(name=bad_name, description="d", users=[])

    def test_iproject_details_accepts_normal_name(self):
        details = IProjectDetails(name="Cardiology Q1", description="d", users=[])
        assert details.name == "Cardiology Q1"
