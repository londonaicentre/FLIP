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

"""FLIP's Flower base strategy — FedAvg plus Central Hub telemetry.

``FlipFedAvg`` is the durable home for everything a FLIP Flower server app
reports to the hub: the TRAINING_STARTED status, per-client metric and
exception forwarding (``flip.flower.metrics``), and the typed round events
(``flip.flower.progress``). App templates subclass it and keep only their
app-specific behaviour; the telemetry evolves here, in flip-utils, without
touching uploaded apps.

Round events attach to the phase the strategy actually runs: training rounds
emit around configure_train/aggregate_train; an evaluation-only strategy
(``fraction_train=0.0``) emits around the evaluate phase instead, so every job
type yields a coherent round timeline without double-emission.

Unlike the rest of ``flip.flower``, importing this module requires the ``flwr``
package (present in the Flower fl-server images) — it subclasses the real
``FedAvg``. Keep logic in the helpers; this class is wiring.
"""

from collections.abc import Iterable

from flwr.app import ArrayRecord, Message, MetricRecord
from flwr.common import ConfigRecord
from flwr.serverapp import Grid
from flwr.serverapp.strategy import FedAvg

from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flip.flower.progress import (
    RoundTelemetry,
    report_round_aggregated,
    report_round_started,
)

__all__ = ["FlipFedAvg"]


class FlipFedAvg(FedAvg):
    """FedAvg with FLIP hub telemetry: status, metrics/exceptions, round events.

    Args:
        flip: FLIP instance used by the fl-server to reach the Central Hub.
        model_id: FLIP model ID (UUID) for the current run.
        *args: Passed through to ``FedAvg``.
        **kwargs: Passed through to ``FedAvg``.
    """

    def __init__(self, flip: FLIP, model_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flip = flip
        self.model_id = model_id
        self.num_rounds: int | None = None
        # Reply bookkeeping for crashed-client attribution: a crashed reply carries
        # neither content nor its sender's real node id, so the tracker names the
        # trust that fell out by elimination — per phase, since the evaluate arm may
        # sample a different cohort than train. The tracker also owns the round's
        # dispatch counts (the "k of m" denominators, and the round-event ownership
        # test: no train dispatch this round means evaluate owns the round events).
        # All logic (and its tests) live in flip.flower.progress; this class only
        # wires the FedAvg hooks to it.
        self._telemetry = RoundTelemetry()

    def start(self, grid: Grid, initial_arrays: ArrayRecord, num_rounds: int = 3, **kwargs):
        """Capture the round total and mark the run as training on the hub."""
        self.num_rounds = num_rounds
        self.flip.update_status(self.model_id, ModelStatus.TRAINING_STARTED)
        return super().start(grid, initial_arrays, num_rounds, **kwargs)

    def configure_train(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> Iterable[Message]:
        """Dispatch the round's train tasks, reporting the round start."""
        messages = list(super().configure_train(server_round, arrays, config, grid))
        self._telemetry.record_dispatch("train", {msg.metadata.dst_node_id for msg in messages})
        if messages:
            report_round_started(self.flip, self.model_id, server_round, self.num_rounds)
        return messages

    def aggregate_train(self, server_round: int, replies: Iterable[Message]) -> ArrayRecord | None:
        """Forward per-client telemetry, aggregate, then report the round aggregated."""
        replies = list(replies)
        returned = self._telemetry.forward_replies(replies, "train", server_round, self.model_id, self.flip)

        result = super().aggregate_train(server_round, replies)
        if self._telemetry.dispatched_count("train"):
            report_round_aggregated(
                self.flip, self.model_id, server_round, returned, self._telemetry.dispatched_count("train")
            )
        return result

    def configure_evaluate(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> Iterable[Message]:
        """Dispatch the round's evaluate tasks; owns the round events when nothing trains."""
        messages = list(super().configure_evaluate(server_round, arrays, config, grid))
        # Always recorded — evaluate absences must resolve against the evaluate
        # roster even when the train phase owns the round events.
        self._telemetry.record_dispatch("evaluate", {msg.metadata.dst_node_id for msg in messages})
        if messages and not self._telemetry.dispatched_count("train"):
            report_round_started(self.flip, self.model_id, server_round, self.num_rounds)
        return messages

    def aggregate_evaluate(self, server_round: int, replies: Iterable[Message]) -> MetricRecord | None:
        """Forward per-client telemetry, aggregate, and close evaluation-only rounds."""
        replies = list(replies)
        returned = self._telemetry.forward_replies(replies, "evaluate", server_round, self.model_id, self.flip)

        result = super().aggregate_evaluate(server_round, replies)
        if not self._telemetry.dispatched_count("train") and self._telemetry.dispatched_count("evaluate"):
            # The dispatch count is the honest denominator: a client that fell out
            # without even a synthesised error reply must still deflate "k of m" —
            # and a dispatched round with zero replies still closes as "0 of m",
            # mirroring the train arm, rather than looking hung.
            report_round_aggregated(
                self.flip, self.model_id, server_round, returned, self._telemetry.dispatched_count("evaluate")
            )
        return result
