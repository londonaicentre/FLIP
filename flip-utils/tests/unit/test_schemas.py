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

"""Tests for the flip.schemas request schemas."""

import pytest
from pydantic import ValidationError

from flip.schemas import TrainingLog, TrainingMetrics


class TestTrainingMetrics:
    """Test the TrainingMetrics request schema."""

    def test_model_dump_uses_snake_case_contract(self):
        """model_dump should emit the snake_case field names expected by the hub."""
        payload = TrainingMetrics(
            fl_client_name="site-1",
            global_round=3,
            label="LOSS_FUNCTION",
            result=0.42,
        ).model_dump()

        assert payload == {
            "fl_client_name": "site-1",
            "global_round": 3,
            "label": "LOSS_FUNCTION",
            "result": 0.42,
        }

    def test_global_round_accepts_zero(self):
        """global_round of 0 is the first round and must be valid."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=0, label="loss", result=1.0)
        assert metrics.global_round == 0

    def test_global_round_rejects_negative(self):
        """global_round below 0 should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name="site-1", global_round=-1, label="loss", result=1.0)

    def test_result_coerces_int_to_float(self):
        """An integer result should be coerced to a float."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=1, label="loss", result=1)
        assert isinstance(metrics.result, float)
        assert metrics.result == 1.0

    def test_missing_field_raises(self):
        """Omitting a required field should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TrainingMetrics.model_validate({"fl_client_name": "site-1", "global_round": 1, "label": "loss"})


class TestTrainingLog:
    """Test the TrainingLog request schema."""

    def test_model_dump_uses_snake_case_contract(self):
        """model_dump should emit the snake_case field names expected by the hub."""
        payload = TrainingLog(fl_client_name="site-1", log="handled exception").model_dump()

        assert payload == {
            "fl_client_name": "site-1",
            "log": "handled exception",
        }

    def test_missing_field_raises(self):
        """Omitting a required field should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TrainingLog.model_validate({"fl_client_name": "site-1"})
