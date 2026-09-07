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

from uuid import uuid4

import pytest
from fastapi import HTTPException

from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.get_all_models import _parse_project_id, _parse_statuses


class TestParseProjectId:
    """The ``project`` query param must never fail open.

    ``get_paging_details`` ignores query keys it does not recognise, so anything this parser
    lets through as ``None`` becomes "show the whole estate" — under a UI that says otherwise.
    """

    def test_absent_or_blank_means_no_filter(self):
        assert _parse_project_id(None) is None
        assert _parse_project_id("") is None
        assert _parse_project_id("   ") is None

    def test_parses_a_uuid_and_tolerates_surrounding_whitespace(self):
        project_id = uuid4()

        assert _parse_project_id(str(project_id)) == project_id
        assert _parse_project_id(f"  {project_id}  ") == project_id

    def test_rejects_a_malformed_id_rather_than_ignoring_it(self):
        with pytest.raises(HTTPException) as exc:
            _parse_project_id("not-a-uuid")

        assert exc.value.status_code == 400

    def test_rejects_a_numeric_id(self):
        """A project id is a UUID; an integer is a caller bug, not a wildcard."""
        with pytest.raises(HTTPException) as exc:
            _parse_project_id("42")

        assert exc.value.status_code == 400


class TestParseStatuses:
    """Sibling parser, previously untested."""

    def test_absent_means_no_filter(self):
        assert _parse_statuses(None) is None
        assert _parse_statuses("") is None

    def test_parses_a_comma_separated_list(self):
        assert _parse_statuses("RUNNING,PENDING") == [ModelStatus.RUNNING, ModelStatus.PENDING]

    def test_skips_empty_segments(self):
        assert _parse_statuses("RUNNING,,") == [ModelStatus.RUNNING]

    def test_a_list_of_only_separators_means_no_filter(self):
        assert _parse_statuses(",,") is None

    def test_rejects_an_unknown_status(self):
        with pytest.raises(HTTPException) as exc:
            _parse_statuses("RUNNING,NOT_A_STATUS")

        assert exc.value.status_code == 400
