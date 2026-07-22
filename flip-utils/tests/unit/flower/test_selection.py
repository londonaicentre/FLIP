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

"""Tests for flip.flower.selection — best-global-model tracking across rounds.

The selector is deliberately flwr-free: aggregated metrics arrive as a plain
mapping (the aggregated ``MetricRecord`` behaves like one) and the model arrays
are an opaque object the selector only holds on to, so plain sentinels stand in
for both.
"""

import logging

from flip.flower.selection import BestModelSelector

ARRAYS_R1 = object()
ARRAYS_R2 = object()


class TestBestModelSelectorInitialState:
    def test_initial_state_has_no_best(self):
        selector = BestModelSelector("val_f1")

        assert selector.best_round is None
        assert selector.best_metric is None
        assert selector.best_arrays is None

    def test_records_metric_key_and_direction(self):
        selector = BestModelSelector("val_loss", minimize=True)

        assert selector.metric == "val_loss"
        assert selector.minimize is True


class TestBestModelSelectorMaximize:
    def test_first_metric_becomes_best(self):
        selector = BestModelSelector("val_f1")

        improved = selector.consider(1, {"val_f1": 0.4, "num-examples": 10}, ARRAYS_R1)

        assert improved is True
        assert selector.best_round == 1
        assert selector.best_metric == 0.4
        assert selector.best_arrays is ARRAYS_R1

    def test_higher_value_replaces_best(self):
        selector = BestModelSelector("val_f1")
        selector.consider(1, {"val_f1": 0.4}, ARRAYS_R1)

        improved = selector.consider(2, {"val_f1": 0.6}, ARRAYS_R2)

        assert improved is True
        assert selector.best_round == 2
        assert selector.best_metric == 0.6
        assert selector.best_arrays is ARRAYS_R2

    def test_lower_value_keeps_previous_best(self):
        selector = BestModelSelector("val_f1")
        selector.consider(1, {"val_f1": 0.6}, ARRAYS_R1)

        improved = selector.consider(2, {"val_f1": 0.4}, ARRAYS_R2)

        assert improved is False
        assert selector.best_round == 1
        assert selector.best_metric == 0.6
        assert selector.best_arrays is ARRAYS_R1

    def test_equal_value_is_not_an_improvement(self):
        selector = BestModelSelector("val_f1")
        selector.consider(1, {"val_f1": 0.5}, ARRAYS_R1)

        improved = selector.consider(2, {"val_f1": 0.5}, ARRAYS_R2)

        assert improved is False
        assert selector.best_round == 1
        assert selector.best_arrays is ARRAYS_R1


class TestBestModelSelectorMinimize:
    def test_lower_value_replaces_best(self):
        selector = BestModelSelector("val_loss", minimize=True)
        selector.consider(1, {"val_loss": 0.9}, ARRAYS_R1)

        improved = selector.consider(2, {"val_loss": 0.3}, ARRAYS_R2)

        assert improved is True
        assert selector.best_round == 2
        assert selector.best_metric == 0.3
        assert selector.best_arrays is ARRAYS_R2

    def test_higher_value_keeps_previous_best(self):
        selector = BestModelSelector("val_loss", minimize=True)
        selector.consider(1, {"val_loss": 0.3}, ARRAYS_R1)

        improved = selector.consider(2, {"val_loss": 0.9}, ARRAYS_R2)

        assert improved is False
        assert selector.best_round == 1
        assert selector.best_metric == 0.3
        assert selector.best_arrays is ARRAYS_R1


class TestBestModelSelectorIgnoredInputs:
    def test_none_metrics_are_ignored(self):
        selector = BestModelSelector("val_f1")

        improved = selector.consider(1, None, ARRAYS_R1)

        assert improved is False
        assert selector.best_round is None

    def test_missing_metric_key_is_ignored_with_warning(self, caplog):
        selector = BestModelSelector("val_f1")

        with caplog.at_level(logging.WARNING, logger="flip.flower.selection"):
            improved = selector.consider(1, {"val_loss": 0.2}, ARRAYS_R1)

        assert improved is False
        assert selector.best_round is None
        assert selector.best_arrays is None
        assert "val_f1" in caplog.text

    def test_non_numeric_metric_value_is_ignored(self):
        selector = BestModelSelector("val_f1")

        improved = selector.consider(1, {"val_f1": [0.1, 0.2]}, ARRAYS_R1)

        assert improved is False
        assert selector.best_round is None

    def test_missing_arrays_are_ignored_with_warning(self, caplog):
        selector = BestModelSelector("val_f1")

        with caplog.at_level(logging.WARNING, logger="flip.flower.selection"):
            improved = selector.consider(1, {"val_f1": 0.9}, None)

        assert improved is False
        assert selector.best_round is None
        assert "arrays" in caplog.text.lower()

    def test_ignored_round_does_not_disturb_an_existing_best(self):
        selector = BestModelSelector("val_f1")
        selector.consider(1, {"val_f1": 0.5}, ARRAYS_R1)

        selector.consider(2, {"other": 1.0}, ARRAYS_R2)

        assert selector.best_round == 1
        assert selector.best_metric == 0.5
        assert selector.best_arrays is ARRAYS_R1
