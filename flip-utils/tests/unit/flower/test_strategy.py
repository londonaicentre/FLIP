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

"""Tests for flip.flower.strategy — FlipFedAvg's wiring over the real FedAvg.

The logic lives (and is tested) in the flwr-free helpers (``flip.flower.selection``,
``flip.flower.progress``); these tests cover the strategy's wiring against real flwr
objects — a stub Grid feeds FedAvg's dispatch, replies are real Messages, and the
hub-facing FLIP client is a MagicMock. What must hold: each round's arrays under
evaluation reach the selector with that round's aggregated metrics, a stale stash
never crosses rounds, the constructor threads the metric and direction knobs
through, the best-model properties mirror the selector, and the train hooks emit
the round events around FedAvg's aggregation.

Metric values are exact binary fractions (0.5, 0.75, …) so the weighted aggregation
reproduces them exactly and the assertions need no approx.
"""

import logging
from unittest.mock import MagicMock

import numpy as np
import pytest
from flwr.app import ArrayRecord, Message, MetricRecord, RecordDict
from flwr.common import ConfigRecord

from flip.flower.strategy import MIN_CLIENTS_KEY, FlipFedAvg, min_clients_from_run_config
from flip.schemas import FLLogEvent


class _StubGrid:
    """The only Grid surface FedAvg's configure hooks touch."""

    def __init__(self, node_ids: list[int]):
        self._node_ids = node_ids

    def get_node_ids(self) -> list[int]:
        return list(self._node_ids)


def _strategy(**kwargs) -> FlipFedAvg:
    return FlipFedAvg(MagicMock(), "model-1", **kwargs)


def _replies(messages: list[Message], client_metrics: list[dict[str, float]]) -> list[Message]:
    return [
        Message(content=RecordDict({"metrics": MetricRecord({**metrics, "num-examples": 5})}), reply_to=msg)
        for msg, metrics in zip(messages, client_metrics)
    ]


def _run_evaluate_round(strategy: FlipFedAvg, server_round: int, arrays: ArrayRecord, client_metrics: list[dict]):
    """Drive one configure_evaluate → aggregate_evaluate cycle with real Messages."""
    grid = _StubGrid([101, 202])
    messages = list(strategy.configure_evaluate(server_round, arrays, ConfigRecord(), grid))
    return strategy.aggregate_evaluate(server_round, _replies(messages, client_metrics))


class TestBestModelDisabled:
    def test_disabled_by_default_and_properties_inert(self):
        strategy = _strategy()

        result = _run_evaluate_round(strategy, 1, ArrayRecord(), [{"accuracy": 0.75}] * 2)

        assert result is not None
        assert result["accuracy"] == 0.75
        assert strategy.best_model_selection_enabled is False
        assert strategy.best_model_round is None
        assert strategy.best_model_metric_value is None
        assert strategy.best_model_arrays is None


class TestBestModelTracking:
    def test_first_evaluated_round_becomes_best(self):
        strategy = _strategy(best_model_metric="accuracy")
        arrays = ArrayRecord()

        result = _run_evaluate_round(strategy, 1, arrays, [{"accuracy": 0.5}] * 2)

        assert result is not None
        assert result["accuracy"] == 0.5
        assert strategy.best_model_selection_enabled is True
        assert strategy.best_model_round == 1
        assert strategy.best_model_metric_value == 0.5
        assert strategy.best_model_arrays is arrays

    def test_improved_round_replaces_best_and_worse_round_does_not(self):
        strategy = _strategy(best_model_metric="accuracy")
        arrays_r1, arrays_r2, arrays_r3 = ArrayRecord(), ArrayRecord(), ArrayRecord()

        _run_evaluate_round(strategy, 1, arrays_r1, [{"accuracy": 0.5}] * 2)
        _run_evaluate_round(strategy, 2, arrays_r2, [{"accuracy": 0.75}] * 2)
        _run_evaluate_round(strategy, 3, arrays_r3, [{"accuracy": 0.625}] * 2)

        assert strategy.best_model_round == 2
        assert strategy.best_model_metric_value == 0.75
        assert strategy.best_model_arrays is arrays_r2

    def test_minimize_threads_through_to_the_selector(self):
        strategy = _strategy(best_model_metric="loss", best_model_metric_minimize=True)
        arrays_r1, arrays_r2 = ArrayRecord(), ArrayRecord()

        _run_evaluate_round(strategy, 1, arrays_r1, [{"loss": 0.25}] * 2)
        _run_evaluate_round(strategy, 2, arrays_r2, [{"loss": 0.75}] * 2)

        assert strategy.best_model_round == 1
        assert strategy.best_model_metric_value == 0.25
        assert strategy.best_model_arrays is arrays_r1

    def test_new_best_is_logged_to_the_flwr_logger(self, caplog):
        strategy = _strategy(best_model_metric="accuracy")

        with caplog.at_level(logging.INFO, logger="flwr"):
            _run_evaluate_round(strategy, 1, ArrayRecord(), [{"accuracy": 0.5}] * 2)

        assert "New best global model at round 1: accuracy=0.5" in caplog.text


class TestBestModelRoundGuards:
    def test_aggregate_for_a_different_round_ignores_the_stash(self):
        strategy = _strategy(best_model_metric="accuracy")
        messages = list(strategy.configure_evaluate(1, ArrayRecord(), ConfigRecord(), _StubGrid([101, 202])))

        result = strategy.aggregate_evaluate(2, _replies(messages, [{"accuracy": 0.75}] * 2))

        assert result is not None
        assert strategy.best_model_round is None
        assert strategy.best_model_arrays is None

    def test_round_with_no_replies_keeps_no_best(self):
        strategy = _strategy(best_model_metric="accuracy")
        list(strategy.configure_evaluate(1, ArrayRecord(), ConfigRecord(), _StubGrid([101, 202])))

        result = strategy.aggregate_evaluate(1, [])

        assert result is None
        assert strategy.best_model_round is None
        assert strategy.best_model_arrays is None


class TestTrainPhaseWiring:
    def test_train_round_aggregates_and_reports_round_events(self):
        flip = MagicMock()
        strategy = FlipFedAvg(flip, "model-1")
        messages = list(
            strategy.configure_train(1, ArrayRecord([np.array([1.0, 2.0])]), ConfigRecord(), _StubGrid([101, 202]))
        )
        replies = [
            Message(
                content=RecordDict(
                    {"arrays": ArrayRecord([np.array([3.0, 4.0])]), "metrics": MetricRecord({"num-examples": 5})}
                ),
                reply_to=msg,
            )
            for msg in messages
        ]

        aggregated_arrays, _aggregated_metrics = strategy.aggregate_train(1, replies)

        assert len(messages) == 2
        assert aggregated_arrays is not None
        np.testing.assert_array_equal(aggregated_arrays.to_numpy_ndarrays()[0], np.array([3.0, 4.0]))
        events = [call.kwargs["event_type"] for call in flip.send_event.call_args_list]
        assert FLLogEvent.ROUND_STARTED in events
        assert FLLogEvent.ROUND_AGGREGATED in events
        aggregated_event = next(
            call for call in flip.send_event.call_args_list if call.kwargs["event_type"] == FLLogEvent.ROUND_AGGREGATED
        )
        assert aggregated_event.kwargs["details"] == {"returned": 2, "expected": 2}


class TestMinClients:
    """min_clients pins every node threshold to the participating-trust count.

    flwr's FedAvg defaults all three to 2, so a single-trust FLIP run would otherwise
    wait for a second node that never arrives — in an unbounded poll loop, so it hangs for
    good rather than timing out.
    Pinning them also stops a multi-trust run beginning before every expected trust has
    connected, which would train on part of the federation without saying so.
    """

    @pytest.mark.parametrize("min_clients", [1, 3])
    def test_min_clients_pins_all_three_node_thresholds(self, min_clients):
        strategy = _strategy(min_clients=min_clients)

        assert strategy.min_train_nodes == min_clients
        assert strategy.min_evaluate_nodes == min_clients
        assert strategy.min_available_nodes == min_clients

    def test_evaluation_shape_keeps_the_thresholds_flwr_does_not_zero(self):
        """FedAvg zeroes min_train_nodes when fraction_train == 0.0 — that is flwr's doing.

        The evaluation templates pass fraction_train=0.0, so only two of the three thresholds
        can carry the trust count there. Pinned so a change to the setdefault block (or to
        flwr's zeroing) cannot quietly drop the two that matter.
        """
        strategy = _strategy(min_clients=3, fraction_train=0.0, fraction_evaluate=1.0)

        assert strategy.min_train_nodes == 0
        assert strategy.min_evaluate_nodes == 3
        assert strategy.min_available_nodes == 3

    def test_explicit_thresholds_win_over_min_clients(self):
        strategy = _strategy(min_clients=2, min_available_nodes=5)

        assert strategy.min_available_nodes == 5
        assert strategy.min_train_nodes == 2

    def test_omitting_min_clients_leaves_flwr_defaults_untouched(self):
        strategy = _strategy()

        assert strategy.min_train_nodes == 2
        assert strategy.min_evaluate_nodes == 2
        assert strategy.min_available_nodes == 2


class TestMinClientsFromRunConfig:
    """The one place the ``flip-min-clients`` key is read, so app templates carry no parsing.

    Absence must yield ``None`` (leave flwr's own defaults alone) rather than a made-up
    number: the key is only injected on the deployed FLIP path, and inventing a low value
    on the paths where nothing injects would let a round start short-handed.
    """

    def test_reads_the_injected_trust_count(self):
        assert min_clients_from_run_config({MIN_CLIENTS_KEY: 3}) == 3

    def test_absent_key_yields_none_so_flwr_defaults_stand(self):
        assert min_clients_from_run_config({}) is None

    def test_coerces_a_quoted_toml_value(self):
        assert min_clients_from_run_config({MIN_CLIENTS_KEY: "2"}) == 2
