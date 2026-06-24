# Copyright (c) 2026 Flower Labs GmbH
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

"""Custom Federated Learning strategy for evaluation."""

from collections.abc import Iterable
from logging import INFO

from flip import FLIP
from flip.flower.metrics import handle_client_exception, handle_client_metrics
from flwr.common import MetricRecord, log
from flwr.common.message import Message
from flwr.serverapp.strategy import FedAvg

# MetricRecord key that FedAvg uses as the aggregation weight; it is bookkeeping,
# not a reportable metric, so it is excluded from the per-client JSON breakdown.
_WEIGHT_KEY = "num-examples"


class EvaluationStrategy(FedAvg):
    """Evaluation-only strategy: native metric aggregation plus FLIP forwarding.

    The aggregation itself — a weighted average (by ``num-examples``) of every
    metric a client returns in its ``MetricRecord`` — is handled entirely by
    ``FedAvg``. Whatever metrics ``client_app`` chooses to compute and return
    flow through unchanged; there is no metric declaration or validation here.

    This subclass only adds the two things the base strategy cannot know about:

    * forwarding each client's metrics and exceptions to the FLIP Central Hub
      (``handle_client_metrics`` / ``handle_client_exception``). These need the
      full reply ``Message``, so they must run in an ``aggregate_*`` override
      rather than via the ``evaluate_metrics_aggr_fn`` callback (which only
      receives ``RecordDict``s). This mirrors the pattern documented in
      ``flip.flower.metrics``.
    * capturing a per-client metric breakdown for the ``evaluation_results.json``
      artifact the ServerApp uploads to S3.

    Args:
        flip: FLIP instance used by the fl-server to forward metrics to the Central Hub.
        model_id: FLIP model ID (UUID) for the current run.
    """

    def __init__(
        self,
        flip: FLIP,
        model_id: str,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.flip = flip
        self.model_id = model_id
        # Per-client results for the JSON artifact: {client_name: {metric_name: value}}
        self.per_client_results: dict[str, dict[str, float]] = {}

    def aggregate_evaluate(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> MetricRecord | None:
        """Forward client results to FLIP, then aggregate natively via FedAvg."""
        replies = list(replies)

        for msg in replies:
            # fl-clients never reach the Central Hub directly — forward server-side.
            handle_client_metrics(msg, server_round, self.model_id, self.flip)
            handle_client_exception(msg, self.model_id, self.flip)
            self._record_client_metrics(msg)

        # FedAvg weighted-averages every metric in the replies, weighting by
        # _WEIGHT_KEY and excluding it from the aggregated record.
        aggregated = super().aggregate_evaluate(server_round, replies)
        log(INFO, "Round %s - per-client evaluation metrics: %s", server_round, self.per_client_results)
        return aggregated

    def _record_client_metrics(self, msg: Message) -> None:
        """Capture one client's metrics for the evaluation_results.json artifact."""
        # Errored replies raise on .content access; nothing to record for them.
        if msg.has_error() or not msg.content.metric_records:
            return

        # The client sends a single MetricRecord under the "metrics" key.
        metrics = dict(next(iter(msg.content.metric_records.values())))

        config = dict(msg.content.get("config", {}))
        client_name = config.get("client_name", str(msg.metadata.src_node_id))

        self.per_client_results[client_name] = {
            name: float(value) for name, value in metrics.items() if name != _WEIGHT_KEY
        }
