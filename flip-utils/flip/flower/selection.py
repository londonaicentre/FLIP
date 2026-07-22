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

"""Best-global-model tracking for FLIP's Flower server apps.

In Flower's round flow the evaluate phase scores the freshly aggregated global
model, so the metrics aggregated in ``aggregate_evaluate`` measure exactly the
checkpoint under consideration. ``BestModelSelector`` turns that into
best-model selection: fed the aggregated evaluation metrics and the evaluated
arrays each round, it retains the arrays of the best-scoring round.

Like the rest of ``flip.flower``'s helpers, this module is flwr-free — the
aggregated ``MetricRecord`` is consumed as a plain mapping and the model arrays
are held as an opaque object — so it stays unit-testable outside the FL images.
``FlipFedAvg`` (``flip.flower.strategy``) owns the wiring.
"""

from __future__ import annotations

import logging
import numbers
from collections.abc import Mapping

logger = logging.getLogger(__name__)

__all__ = ["BestModelSelector"]


class BestModelSelector:
    """Tracks the best aggregated global model across federated rounds.

    A round only replaces the current best on strict improvement of the watched
    metric — higher wins by default, lower wins when ``minimize`` is set. Rounds
    whose metrics do not carry a usable value for the metric (or arrive without
    the evaluated arrays) are ignored with a warning, so a selection metric that
    the clients never report shows up loudly in the server logs rather than as a
    silently missing best model.

    Args:
        metric (str): Key of the aggregated evaluation metric to select on.
        minimize (bool): Whether lower metric values are better (e.g. a loss).
    """

    def __init__(self, metric: str, minimize: bool = False):
        self.metric = metric
        self.minimize = minimize
        self.best_round: int | None = None
        self.best_metric: float | None = None
        self.best_arrays: object | None = None

    def consider(self, server_round: int, metrics: Mapping[str, object] | None, arrays: object | None) -> bool:
        """Offer one round's aggregated evaluation metrics and evaluated arrays for selection.

        Args:
            server_round (int): The federated round the metrics belong to.
            metrics (Mapping[str, object] | None): Aggregated evaluation metrics for the round, or
                ``None`` when the round produced no aggregate.
            arrays (object | None): The global model arrays those metrics were computed on.

        Returns:
            bool: ``True`` when this round's model became the new best.
        """
        if metrics is None:
            return False

        value = metrics.get(self.metric)
        if value is None:
            logger.warning(
                "Best-model metric %r not found in round %d aggregated evaluation metrics %s — "
                "the client apps must report it for selection to work",
                self.metric,
                server_round,
                sorted(metrics),
            )
            return False
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            logger.warning(
                "Best-model metric %r in round %d is not a number (got %r) — ignoring this round",
                self.metric,
                server_round,
                value,
            )
            return False
        if arrays is None:
            logger.warning(
                "Best-model metric %r improved in round %d but no evaluated arrays were provided — "
                "ignoring this round",
                self.metric,
                server_round,
            )
            return False

        candidate = float(value)
        if self.best_metric is not None:
            improved = candidate < self.best_metric if self.minimize else candidate > self.best_metric
            if not improved:
                return False

        self.best_round = server_round
        self.best_metric = candidate
        self.best_arrays = arrays
        return True
