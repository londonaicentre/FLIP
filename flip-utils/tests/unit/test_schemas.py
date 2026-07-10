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

from flip.schemas import TrainingLog, TrainingMetrics, split_x_label


class TestSplitXLabel:
    """Test the shared "<label>[@<x_label>]" key splitter."""

    def test_plain_key_has_no_x_label(self):
        assert split_x_label("train_loss") == ("train_loss", None)

    def test_at_segment_names_the_x_axis(self):
        assert split_x_label("train_loss@epoch") == ("train_loss", "epoch")

    def test_only_first_at_splits(self):
        """Anything after the first "@" belongs to the x_label."""
        assert split_x_label("loss@epoch@2") == ("loss", "epoch@2")


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
            "x_value": 3.0,
            "x_label": "Global Round",
        }

    def test_x_label_defaults_to_global_round(self):
        """x_label defaults to the historical axis label when the client doesn't set one (FLIP#148)."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=1, label="loss", result=1.0)
        assert metrics.x_label == "Global Round"

    def test_model_dump_emits_custom_x_label(self):
        """A client-supplied x_label is carried on the wire (FLIP#148)."""
        payload = TrainingMetrics(
            fl_client_name="site-1", global_round=1, label="loss", result=1.0, x_label="epoch"
        ).model_dump()
        assert payload["x_label"] == "epoch"

    def test_x_value_defaults_to_global_round(self):
        """A metric without an explicit coordinate is plotted at its global round (FLIP#148)."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=4, label="loss", result=1.0)
        assert metrics.x_value == 4.0

    def test_explicit_none_x_value_backfills_from_global_round(self):
        """Callers pass x_value=None straight through; the validator resolves it."""
        metrics = TrainingMetrics(fl_client_name="site-1", global_round=4, label="loss", result=1.0, x_value=None)
        assert metrics.x_value == 4.0

    def test_custom_x_value_is_carried_on_the_wire(self):
        """An arbitrary (float) coordinate is kept and global_round stays as provenance."""
        payload = TrainingMetrics(
            fl_client_name="site-1", global_round=2, label="loss", result=1.0, x_value=7.5, x_label="epoch"
        ).model_dump()
        assert payload["x_value"] == 7.5
        assert payload["global_round"] == 2

    @pytest.mark.parametrize("bad_x", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_x_value_rejected(self, bad_x):
        """nan/inf would break JSON-encoding the hub's metrics response, so reject at the edge."""
        with pytest.raises(ValidationError):
            TrainingMetrics(fl_client_name="site-1", global_round=1, label="loss", result=1.0, x_value=bad_x)

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
