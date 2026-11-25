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
from fastapi import HTTPException

from flip_api.domain.schemas.status import ModelStatus
from flip_api.model_services.get_all_models import _parse_statuses


class TestParseStatuses:
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

    def test_rejects_the_legacy_training_started_name(self):
        # No silent aliasing of the pre-rename status on the public filter (#803).
        with pytest.raises(HTTPException) as exc:
            _parse_statuses("TRAINING_STARTED")

        assert exc.value.status_code == 400
