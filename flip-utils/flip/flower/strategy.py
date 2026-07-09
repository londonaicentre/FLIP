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
from flip.flower.metrics import _resolve_site_name, handle_client_exception, handle_client_metrics
from flip.flower.progress import (
    report_client_result,
    report_round_aggregated,
    report_round_started,
    resolve_absent_site,
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
        # How many clients the current round's train phase was dispatched to;
        # None means this round has no train phase (evaluation-only strategy).
        self._expected_train_replies: int | None = None
        # node_id -> site name, learned from healthy replies, and the node ids the
        # current round was dispatched to. A crashed reply carries neither content
        # nor its sender's real node id, so together these are the only way to name
        # the trust that fell out — without it the hub rejects the exception log and
        # the per-trust "dropped" state is unreachable.
        self._site_by_node: dict[int, str] = {}
        self._dispatched_nodes: set[int] = set()

    def _forward_replies(self, replies: list[Message], server_round: int) -> int:
        """Forward every reply's metrics, exception and round event to the hub.

        Learns each healthy reply's site, then names the one client that was
        dispatched a task and never answered (see ``resolve_absent_site``) so its
        error is attributed to the right trust. fl-clients never reach the Central
        Hub directly — every forward happens here, server-side.

        Args:
            replies: The round's reply Messages.
            server_round: Flower's 1-based round number.

        Returns:
            int: How many replies were healthy.
        """
        for msg in replies:
            site = _resolve_site_name(msg)
            if site is not None:
                self._site_by_node[msg.metadata.src_node_id] = site

        responded = {msg.metadata.src_node_id for msg in replies if not msg.has_error()}
        absent_site = resolve_absent_site(self._dispatched_nodes, responded, self._site_by_node)

        returned = 0
        for msg in replies:
            handle_client_metrics(msg, server_round, self.model_id, self.flip)
            handle_client_exception(msg, self.model_id, self.flip, site_name=absent_site)
            if report_client_result(msg, server_round, self.model_id, self.flip):
                returned += 1
        return returned

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
        self._expected_train_replies = len(messages) if messages else None
        self._dispatched_nodes = {msg.metadata.dst_node_id for msg in messages}
        if messages:
            report_round_started(self.flip, self.model_id, server_round, self.num_rounds)
        return messages

    def aggregate_train(self, server_round: int, replies: Iterable[Message]) -> ArrayRecord | None:
        """Forward per-client telemetry, aggregate, then report the round aggregated."""
        replies = list(replies)
        returned = self._forward_replies(replies, server_round)

        result = super().aggregate_train(server_round, replies)
        if self._expected_train_replies is not None:
            report_round_aggregated(self.flip, self.model_id, server_round, returned, self._expected_train_replies)
        return result

    def configure_evaluate(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> Iterable[Message]:
        """Dispatch the round's evaluate tasks; owns the round events when nothing trains."""
        messages = list(super().configure_evaluate(server_round, arrays, config, grid))
        if messages and self._expected_train_replies is None:
            self._dispatched_nodes = {msg.metadata.dst_node_id for msg in messages}
            report_round_started(self.flip, self.model_id, server_round, self.num_rounds)
        return messages

    def aggregate_evaluate(self, server_round: int, replies: Iterable[Message]) -> MetricRecord | None:
        """Forward per-client telemetry, aggregate, and close evaluation-only rounds."""
        replies = list(replies)
        returned = self._forward_replies(replies, server_round)

        result = super().aggregate_evaluate(server_round, replies)
        if replies and self._expected_train_replies is None:
            report_round_aggregated(self.flip, self.model_id, server_round, returned, len(replies))
        return result
