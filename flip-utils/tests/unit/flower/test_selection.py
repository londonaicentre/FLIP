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

import pytest

from flip.flower.selection import BestModelSelector, parse_best_model_run_config

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

    def test_boolean_metric_value_is_ignored(self):
        # bool subclasses int, so isinstance(True, numbers.Real) is True: without
        # the explicit bool guard, True would score as 1.0 and beat every real
        # metric below it.
        selector = BestModelSelector("val_f1")

        improved = selector.consider(1, {"val_f1": True}, ARRAYS_R1)

        assert improved is False
        assert selector.best_round is None

    def test_missing_arrays_are_ignored_with_warning(self, caplog):
        selector = BestModelSelector("val_f1")

        with caplog.at_level(logging.WARNING, logger="flip.flower.selection"):
            improved = selector.consider(1, {"val_f1": 0.9}, None)

        assert improved is False
        assert selector.best_round is None
        assert "arrays" in caplog.text.lower()
        # The arrays check runs before any comparison against the current best, so the
        # warning must not claim the metric "improved" — nothing was compared yet.
        assert "improved" not in caplog.text.lower()

    def test_ignored_round_does_not_disturb_an_existing_best(self):
        selector = BestModelSelector("val_f1")
        selector.consider(1, {"val_f1": 0.5}, ARRAYS_R1)

        selector.consider(2, {"other": 1.0}, ARRAYS_R2)

        assert selector.best_round == 1
        assert selector.best_metric == 0.5
        assert selector.best_arrays is ARRAYS_R1


class TestBestModelSelectorNonFiniteValues:
    """Non-finite metrics must never win.

    NaN compares ``False`` against everything in both directions, so accepting one
    would pin the best model to that round for the rest of the run. NaN losses are
    a live Flower failure mode here — see FLIP#764, whose guard landed in this same
    xray tutorial — and the tutorial's own metric reduction yields ``nan`` for an
    empty metric list.
    """

    def test_nan_metric_value_is_ignored_with_warning(self, caplog):
        selector = BestModelSelector("val_loss", minimize=True)

        with caplog.at_level(logging.WARNING, logger="flip.flower.selection"):
            improved = selector.consider(1, {"val_loss": float("nan")}, ARRAYS_R1)

        assert improved is False
        assert selector.best_round is None
        assert selector.best_metric is None
        assert selector.best_arrays is None
        assert "val_loss" in caplog.text

    def test_nan_does_not_pin_the_best_model_against_later_rounds(self):
        selector = BestModelSelector("val_loss", minimize=True)
        selector.consider(1, {"val_loss": float("nan")}, ARRAYS_R1)

        improved = selector.consider(2, {"val_loss": 0.3}, ARRAYS_R2)

        assert improved is True
        assert selector.best_round == 2
        assert selector.best_metric == 0.3
        assert selector.best_arrays is ARRAYS_R2

    def test_infinite_metric_value_is_ignored(self):
        selector = BestModelSelector("val_f1")

        improved = selector.consider(1, {"val_f1": float("inf")}, ARRAYS_R1)

        assert improved is False
        assert selector.best_round is None
        assert selector.best_arrays is None


class TestParseBestModelRunConfig:
    """Run-config parsing shared by the server-app templates.

    The Flower platform path has no hub-side validation of these keys — fl-api-flower
    feeds the researcher's config.toml straight to ``flwr run`` — so this parse is the
    only gate between a typo and a mislabelled (or silently missing) best checkpoint.
    """

    def test_absent_metric_disables_selection(self):
        assert parse_best_model_run_config({}, num_rounds=5) == (None, False)

    def test_blank_metric_disables_selection(self):
        assert parse_best_model_run_config({"best-model-metric": "   "}, num_rounds=5) == (None, False)

    def test_metric_is_stripped(self):
        # Mirrors fl-api's NVFLARE-side fix (#673): a stray space in the config must not
        # survive parsing and silently never match the metric label the clients report.
        config = {"best-model-metric": " val_f1 "}

        assert parse_best_model_run_config(config, num_rounds=5) == ("val_f1", False)

    def test_minimize_defaults_to_false(self):
        assert parse_best_model_run_config({"best-model-metric": "val_f1"}, num_rounds=5) == ("val_f1", False)

    def test_minimize_true_is_honoured(self):
        config = {"best-model-metric": "val_loss", "best-model-metric-minimize": True}

        assert parse_best_model_run_config(config, num_rounds=5) == ("val_loss", True)

    def test_non_string_metric_is_rejected(self):
        # str() coercion would turn e.g. an int into a bogus metric key that enables
        # selection which then warns every round and produces no best artifact.
        with pytest.raises(ValueError, match="best-model-metric"):
            parse_best_model_run_config({"best-model-metric": 123}, num_rounds=5)

    def test_quoted_minimize_is_rejected_when_selection_enabled(self):
        # bool("false") is True: a quoted TOML value would silently invert the selection
        # direction and ship the worst-scoring checkpoint as the best one.
        config = {"best-model-metric": "val_loss", "best-model-metric-minimize": "true"}

        with pytest.raises(ValueError, match="best-model-metric-minimize"):
            parse_best_model_run_config(config, num_rounds=5)

    def test_invalid_minimize_is_ignored_when_selection_disabled(self):
        # With no metric set the minimize key is inert — failing the whole training run
        # over an inert key would be worse than ignoring it.
        config = {"best-model-metric-minimize": "true"}

        assert parse_best_model_run_config(config, num_rounds=5) == (None, False)

    def test_single_round_selection_warns_but_stays_enabled(self, caplog):
        # Unlike NVFLARE (whose IntimeModelSelector skips round 0, so a 1-round job can
        # never save a best model), Flower's round-1 evaluation still feeds the selector:
        # best == final is redundant, not broken, so this warns instead of rejecting.
        with caplog.at_level(logging.WARNING, logger="flip.flower.selection"):
            result = parse_best_model_run_config({"best-model-metric": "val_f1"}, num_rounds=1)

        assert result == ("val_f1", False)
        assert "num_rounds" in caplog.text

    def test_multi_round_selection_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING, logger="flip.flower.selection"):
            parse_best_model_run_config({"best-model-metric": "val_f1"}, num_rounds=2)

        assert caplog.text == ""
