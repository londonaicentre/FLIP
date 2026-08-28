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
reports to the hub: the RUNNING status, per-client metric and
exception forwarding (``flip.flower.metrics``), and the typed round events
(``flip.flower.progress``). App templates subclass it and keep only their
app-specific behaviour; the telemetry evolves here, in flip-utils, without
touching uploaded apps.

Round events attach to the phase the strategy actually runs: training rounds
emit around configure_train/aggregate_train; an evaluation-only strategy
(``fraction_train=0.0``) emits around the evaluate phase instead, so every job
type yields a coherent round timeline without double-emission.

Like ``flip.flower.privacy``, importing this module requires the ``flwr`` package
(present in the Flower fl-server images) — it subclasses the real ``FedAvg``.
Keep logic in the helpers; this class is wiring.
"""

from collections.abc import Iterable, Mapping
from logging import INFO
from typing import cast

from flwr.app import ArrayRecord, Message, MetricRecord, UserConfigValue
from flwr.common import ConfigRecord, log
from flwr.serverapp import Grid
from flwr.serverapp.strategy import FedAvg, Result

from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flip.flower.progress import (
    RoundTelemetry,
    report_round_aggregated,
    report_round_started,
)
from flip.flower.selection import BestModelSelector

__all__ = ["MIN_CLIENTS_KEY", "FlipFedAvg", "min_clients_from_run_config"]

# Run-config key carrying the participating-trust count, injected by fl-api-flower at submit
# time (the Flower analogue of the NVFLARE adapter's ``config["min_clients"] = len(trusts)``).
# Declared under ``[tool.flwr.app.config]`` in each app's pyproject.toml, as the other
# ``flip-*`` keys are — flwr rejects a ``--run-config`` override it has not been declared.
MIN_CLIENTS_KEY = "flip-min-clients"


def min_clients_from_run_config(run_config: Mapping[str, UserConfigValue]) -> int | None:
    """Read the participating-trust count from a Flower run config.

    Kept here rather than in each app template for the same reason as
    :func:`~flip.flower.selection.parse_best_model_run_config`: the templates should carry no
    run-config parsing.

    Args:
        run_config (Mapping[str, UserConfigValue]): The app's run config (``Context.run_config``).

    Returns:
        int | None: The trust count, or ``None`` when the key is absent — meaning "leave
        ``FedAvg``'s own thresholds alone", never a stand-in for 1. No shipped FLIP app reaches
        that branch: each declares a ``flip-min-clients`` placeholder under
        ``[tool.flwr.app.config]``, and flwr fuses declared defaults into the run config, so the
        placeholder (not this branch) is what protects the simulator and ``submit_tutorial``
        paths. The branch exists so a caller without the declaration inherits flwr's defaults
        rather than a made-up quorum.

    Raises:
        ValueError: If the value is not a TOML integer. ``int()`` would otherwise coerce
            ``true`` to 1 and ``2.9`` to 2, setting the quorum to something other than the
            participating-trust count.
    """
    value = run_config.get(MIN_CLIENTS_KEY)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(
            f"{MIN_CLIENTS_KEY} must be a TOML integer, got {value!r} — a coerced value sets the "
            "node quorum to something other than the participating-trust count"
        )
    return int(value)


class FlipFedAvg(FedAvg):
    """FedAvg with FLIP hub telemetry: status, metrics/exceptions, round events.

    Optionally also tracks the best aggregated global model: when
    ``best_model_metric`` is set, each evaluate round's aggregated metrics are
    offered to a :class:`~flip.flower.selection.BestModelSelector` together with
    the arrays that round evaluated (Flower's round loop evaluates the freshly
    aggregated model, so the metric measures exactly the checkpoint being
    considered). Unset, nothing changes and no best model is retained.

    Args:
        flip: FLIP instance used by the fl-server to reach the Central Hub.
        model_id: FLIP model ID (UUID) for the current run.
        *args: Passed through to ``FedAvg``.
        best_model_metric: Aggregated evaluation-metric key to select the best
            global model on; ``None`` disables selection.
        best_model_metric_minimize: Whether lower values of that metric are
            better (e.g. a loss).
        min_clients: Number of participating trusts. Pins ``min_train_nodes``,
            ``min_evaluate_nodes`` and ``min_available_nodes`` unless the caller sets
            them explicitly by keyword. ``None`` leaves ``FedAvg``'s own defaults (2) in place.
        **kwargs: Passed through to ``FedAvg``.
    """

    def __init__(
        self,
        flip: FLIP,
        model_id: str,
        *args,
        best_model_metric: str | None = None,
        best_model_metric_minimize: bool = False,
        min_clients: int | None = None,
        **kwargs,
    ):
        # flwr defaults all three node thresholds to 2, so a single-trust run waits for a second
        # node that never arrives — and flwr's sample_nodes polls in an UNBOUNDED sleep(1) loop,
        # so the job hangs for good rather than failing. Deriving them from the trust count also
        # stops a multi-trust run starting before every trust has connected.
        if min_clients is not None:
            if min_clients < 1:
                # Reachable: slot_names is a DB lookup that returns [] when a participating trust
                # has no assigned FL kit slot, and validate_client_availability passes an empty
                # list. len([]) == 0 would zero every threshold, and flwr's sample_size is
                # max(n, 0) — so the round would start against no one.
                raise ValueError(
                    f"min_clients must be >= 1, got {min_clients} — a zero or negative quorum "
                    "lets a round start without waiting for any trust"
                )
            kwargs.setdefault("min_train_nodes", min_clients)
            kwargs.setdefault("min_evaluate_nodes", min_clients)
            kwargs.setdefault("min_available_nodes", min_clients)
        super().__init__(*args, **kwargs)
        self.flip = flip
        self.model_id = model_id
        self.num_rounds: int | None = None
        self._best_selector = (
            BestModelSelector(best_model_metric, minimize=best_model_metric_minimize) if best_model_metric else None
        )
        # The arrays configure_evaluate dispatched this round — what the replies measure.
        self._arrays_under_evaluation: tuple[int, ArrayRecord] | None = None
        # Reply bookkeeping for crashed-client attribution: a crashed reply carries
        # neither content nor its sender's real node id, so the tracker names the
        # trust that fell out by elimination — per phase, since the evaluate arm may
        # sample a different cohort than train. The tracker also owns the round's
        # dispatch counts (the "k of m" denominators, and the round-event ownership
        # test: no train dispatch this round means evaluate owns the round events).
        # All logic (and its tests) live in flip.flower.progress; this class only
        # wires the FedAvg hooks to it.
        self._telemetry = RoundTelemetry()

    def start(self, grid: Grid, initial_arrays: ArrayRecord, num_rounds: int = 3, *args, **kwargs) -> Result:
        """Capture the round total and mark the run as executing on the hub."""
        # *args keeps FedAvg's optional params (timeout, …) passable positionally — a
        # kwargs-only override silently breaks such callers.
        self.num_rounds = num_rounds
        self.flip.update_status(self.model_id, ModelStatus.RUNNING)
        return super().start(grid, initial_arrays, num_rounds, *args, **kwargs)

    def configure_train(
        self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
    ) -> Iterable[Message]:
        """Dispatch the round's train tasks, reporting the round start."""
        messages = list(super().configure_train(server_round, arrays, config, grid))
        self._telemetry.record_dispatch("train", {msg.metadata.dst_node_id for msg in messages})
        if messages:
            report_round_started(self.flip, self.model_id, server_round, self.num_rounds)
        return messages

    def aggregate_train(
        self, server_round: int, replies: Iterable[Message]
    ) -> tuple[ArrayRecord | None, MetricRecord | None]:
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
        if messages and self._best_selector is not None:
            self._arrays_under_evaluation = (server_round, arrays)
        if messages and not self._telemetry.dispatched_count("train"):
            report_round_started(self.flip, self.model_id, server_round, self.num_rounds)
        return messages

    def aggregate_evaluate(self, server_round: int, replies: Iterable[Message]) -> MetricRecord | None:
        """Forward per-client telemetry, aggregate, and close evaluation-only rounds."""
        replies = list(replies)
        returned = self._telemetry.forward_replies(replies, "evaluate", server_round, self.model_id, self.flip)

        result = super().aggregate_evaluate(server_round, replies)
        if self._best_selector is not None:
            stashed = self._arrays_under_evaluation
            evaluated_arrays = stashed[1] if stashed is not None and stashed[0] == server_round else None
            if self._best_selector.consider(server_round, result, evaluated_arrays):
                # flwr's own logger — a stdlib logger here never reaches the ServerApp output.
                log(
                    INFO,
                    "New best global model at round %d: %s=%s",
                    server_round,
                    self._best_selector.metric,
                    self._best_selector.best_metric,
                )
        if not self._telemetry.dispatched_count("train") and self._telemetry.dispatched_count("evaluate"):
            # The dispatch count is the honest denominator: a client that fell out
            # without even a synthesised error reply must still deflate "k of m" —
            # and a dispatched round with zero replies still closes as "0 of m",
            # mirroring the train arm, rather than looking hung.
            report_round_aggregated(
                self.flip, self.model_id, server_round, returned, self._telemetry.dispatched_count("evaluate")
            )
        return result

    @property
    def best_model_selection_enabled(self) -> bool:
        """Whether a best-model metric was configured for this run."""
        return self._best_selector is not None

    @property
    def best_model_round(self) -> int | None:
        """The round that produced the best global model so far, if any."""
        return self._best_selector.best_round if self._best_selector is not None else None

    @property
    def best_model_metric_value(self) -> float | None:
        """The best value of the selection metric so far, if any."""
        return self._best_selector.best_metric if self._best_selector is not None else None

    @property
    def best_model_arrays(self) -> ArrayRecord | None:
        """The arrays of the best global model so far, if a selection happened."""
        if self._best_selector is None:
            return None
        # The selector holds arrays as an opaque object; this class only ever feeds it ArrayRecords.
        return cast("ArrayRecord | None", self._best_selector.best_arrays)
