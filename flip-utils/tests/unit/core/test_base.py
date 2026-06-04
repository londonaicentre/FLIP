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

"""Tests for flip.core.base module."""

import pytest

from flip.constants.flip_constants import ResourceType
from flip.core.standard import FLIPStandardDev


class TestFLIPBaseValidation:
    """Test validation methods in FLIPBase class."""

    @pytest.fixture
    def flip_instance(self):
        """Create a FLIPStandardDev instance for testing inherited methods."""
        return FLIPStandardDev()

    def test_check_query_valid_string(self, flip_instance):
        """check_query should accept valid string queries."""
        # Should not raise
        flip_instance.check_query("SELECT * FROM table")
        flip_instance.check_query("")
        flip_instance.check_query("complex query with 'quotes'")

    def test_check_query_invalid_type(self, flip_instance):
        """check_query should raise TypeError for non-string inputs."""
        with pytest.raises(TypeError, match="expect query to be string"):
            flip_instance.check_query(123)

        with pytest.raises(TypeError, match="expect query to be string"):
            flip_instance.check_query(None)

        with pytest.raises(TypeError, match="expect query to be string"):
            flip_instance.check_query(["query"])

    def test_check_project_id_valid_string(self, flip_instance):
        """check_project_id should accept valid string project IDs."""
        flip_instance.check_project_id("project-123")
        flip_instance.check_project_id("abc-def-ghi")
        flip_instance.check_project_id("")

    def test_check_project_id_invalid_type(self, flip_instance):
        """check_project_id should raise TypeError for non-string inputs."""
        with pytest.raises(TypeError, match="expect project_id to be string"):
            flip_instance.check_project_id(123)

        with pytest.raises(TypeError, match="expect project_id to be string"):
            flip_instance.check_project_id(None)

    def test_check_accession_id_valid_string(self, flip_instance):
        """check_accession_id should accept valid string accession IDs."""
        flip_instance.check_accession_id("ACC123")
        flip_instance.check_accession_id("accession-456")

    def test_check_accession_id_invalid_type(self, flip_instance):
        """check_accession_id should raise TypeError for non-string inputs."""
        with pytest.raises(TypeError, match="expect accession_id to be string"):
            flip_instance.check_accession_id(123)

        with pytest.raises(TypeError, match="expect accession_id to be string"):
            flip_instance.check_accession_id(None)

    def test_check_resource_type_single_resource(self, flip_instance):
        """check_resource_type should convert single ResourceType to list."""
        result = flip_instance.check_resource_type(ResourceType.NIFTI)
        assert result == [ResourceType.NIFTI]

        result = flip_instance.check_resource_type(ResourceType.DICOM)
        assert result == [ResourceType.DICOM]

    def test_check_resource_type_list_of_resources(self, flip_instance):
        """check_resource_type should accept list of ResourceTypes."""
        resources = [ResourceType.NIFTI, ResourceType.DICOM]
        result = flip_instance.check_resource_type(resources)
        assert result == resources

    def test_check_resource_type_invalid_type(self, flip_instance):
        """check_resource_type should raise TypeError for invalid types."""
        with pytest.raises(TypeError, match="resource_type must be ResourceType"):
            flip_instance.check_resource_type("NIFTI")

        with pytest.raises(TypeError, match="resource_type must be ResourceType"):
            flip_instance.check_resource_type(123)

    def test_check_resource_type_invalid_list_item(self, flip_instance):
        """check_resource_type should raise TypeError if list contains non-ResourceType."""
        with pytest.raises(TypeError, match="Each item in resource_type list must be a ResourceType"):
            flip_instance.check_resource_type([ResourceType.NIFTI, "DICOM"])
