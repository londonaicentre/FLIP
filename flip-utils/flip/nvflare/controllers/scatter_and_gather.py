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

import os

import torch
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
from nvflare.app_common.workflows.scatter_and_gather import ScatterAndGather as NVFlareScatterAndGather
from nvflare.app_opt.pt.fedopt import PTFedOptModelShareableGenerator

from flip import FLIP
from flip.constants import FlipEvents, FlipProps, PTConstants
from flip.nvflare.metrics import handle_metrics_event
from flip.nvflare.runtime import get_flip_model_id
from flip.schemas import FLLogEvent


class ScatterAndGather(NVFlareScatterAndGather):
    """FLIP's FedAvg controller — a thin subclass of NVFLARE's stock ``ScatterAndGather``.

    FLIP previously vendored a full copy of the stock controller, which drifted out of date. This
    subclass inherits stock's round loop verbatim — and with it ``memory_gc_rounds`` allocator-aware
    cleanup (which bounds server RSS on large-model jobs), the per-round ``aggregator.reset``,
    ``allow_empty_global_weights``, and any future upstream fixes. Only the FLIP-specific hooks
    are overridden:

      * :meth:`__init__` — carries the FLIP ``model_id`` and a :class:`FLIP` client, the best-model
        selection knobs (``best_model_metric``, ``best_model_metric_minimize`` — FLIP#673), and disables
        stock's per-round component snapshot by default (``snapshot_every_n_rounds=0``). Snapshotting
        re-serialises the full global model every round; for a ~759 MiB model that re-introduces the
        very per-round memory churn ``memory_gc_rounds`` exists to bound. FLIP never snapshotted, so
        ``0`` also preserves prior behaviour. It stays configurable for callers that want resilience.
      * :meth:`_accept_train_result` — reports a client-side execution exception to the hub,
        converts a (possibly partial, frozen-backbone) ``WEIGHT_DIFF`` head update into full
        ``WEIGHTS`` before aggregation, since FLIP's aggregator expects ``WEIGHTS`` (FLIP#684),
        and relays each genuinely accepted result to the hub as a ``CLIENT_RESULT_RECEIVED`` fact.
      * :meth:`handle_event` — relays FLIP metrics on ``FlipEvents.SEND_RESULT`` (also tracking
        validation metrics for best-model selection) and, on ``AppEventType.ROUND_DONE`` — which
        stock fires after aggregation and persist, with ``_current_round`` still on the finished
        round — saves the aggregated model as the best checkpoint when its tracked metric beats the
        best seen so far (FLIP#673).
      * :meth:`_check_abort_signal` — fires ``FlipEvents.ABORTED`` so downstream components (e.g.
        ``PersistToS3AndCleanup``) can persist results on an aborted run.
      * :meth:`get_persist_state` / :meth:`restore` — carry the best-model tracking state through
        stock's component snapshots (only exercised when ``snapshot_every_n_rounds`` is enabled).
    """

    def __init__(
        self,
        *args,
        model_id: str = "",
        best_model_metric: str | None = None,
        best_model_metric_minimize: bool = False,
        **kwargs,
    ) -> None:
        # ``*args`` is load-bearing, not dead: NVFLARE's FedJob/recipe serialisation
        # (``get_component_init_parameters``) only walks a thin subclass's base class to capture
        # stock's __init__ args (persistor_id, aggregator_id, num_rounds, …) when the subclass
        # declares BOTH ``*args`` and ``**kwargs``. With ``**kwargs`` alone, only ``model_id`` is
        # serialised, so a recipe's ScatterAndGather loses its persistor and broadcasts an empty
        # round-0 model. Do not remove ``*args`` (guarded by TestFedJobSerialisation).
        #
        # FLIP has never snapshotted components each round; keep that off by default so a large-model
        # job does not re-serialise the full global model every round (see the class docstring). Left
        # in kwargs so an explicit config value still wins.
        kwargs.setdefault("snapshot_every_n_rounds", 0)
        super().__init__(*args, **kwargs)
        self._model_id_fallback = model_id
        self._model_id: str | None = None
        self.flip = FLIP()
        # Accepted client names per 0-based round, feeding the "k of m returned"
        # counts relayed on ROUND_DONE (see _report_client_result).
        self._round_acceptances: dict[int, set[str]] = {}

        # Best-model tracking (FLIP#673): validation metrics recorded per round drive a
        # best-checkpoint save on ROUND_DONE. ``best_model_metric`` selects the tracked label
        # (e.g. "VAL_DICE", "VAL_LOSS"); when None, the first VAL_*/TEST_* metric received is
        # tracked (warned once — not recommended for multi-metric jobs).
        # ``best_model_metric_minimize`` flips the comparison for loss-like metrics.
        self.best_model_metric = best_model_metric
        self.best_model_metric_minimize = best_model_metric_minimize
        self._metric_warning_logged = False
        self._best_metric: float | None = None
        self._best_round: int | None = None
        self._round_metrics: dict[int, float] = {}

    def _resolve_model_id(self, fl_ctx: FLContext) -> str:
        if self._model_id is None:
            self._model_id = get_flip_model_id(fl_ctx, fallback=self._model_id_fallback)
        return self._model_id

    def _diff_to_weights(self, result: Shareable, fl_ctx: FLContext) -> Shareable:
        """Convert a client ``WEIGHT_DIFF`` update into full ``WEIGHTS`` for the WEIGHTS aggregator.

        FedAvg here aggregates ``WEIGHTS`` while clients return a ``WEIGHT_DIFF``; rebuild the full
        weights by adding the diff onto the current global model. Partial-safe (FLIP#684): a
        frozen-backbone fine-tune sends only its trainable head, and keys absent from the diff keep
        their global value, so a head-only update reconstructs correctly. FedOpt (which aggregates
        ``WEIGHT_DIFF`` directly) is left untouched. On any error the original result is returned so
        the base class can apply its own handling.
        """
        try:
            dxo = from_shareable(result)
            if isinstance(self.aggregator, PTFedOptModelShareableGenerator):
                return result
            if dxo.data_kind == DataKind.WEIGHT_DIFF:
                global_weights = self._global_weights["weights"]
                diff = dxo.data
                new_weights = {
                    key: (global_weights[key] + diff[key] if key in diff else global_weights[key])
                    for key in global_weights
                }
                new_dxo = DXO(data_kind=DataKind.WEIGHTS, data=new_weights, meta=dxo.meta)
                return new_dxo.update_shareable(result)
            self.log_error(
                fl_ctx, f"The returned weights are not of type WEIGHT_DIFF. Received data kind: {dxo.data_kind}"
            )
        except Exception as e:
            self.log_error(fl_ctx, f"Error while adding client WEIGHT_DIFF to global weights at server: {e}")
        return result

    def _accept_train_result(
        self, client_name: str, result: Shareable, fl_ctx: FLContext, is_unknown_task: bool = False
    ) -> bool:
        rc = result.get_return_code()
        if rc and rc != ReturnCode.OK:
            # FLIP: surface a client-side execution exception to the hub before the base class applies
            # its ignore/panic policy for the non-OK result.
            if rc == ReturnCode.EXECUTION_EXCEPTION:
                formatted_exception = result.get_header("exception")
                if formatted_exception is not None:
                    self.log_error(fl_ctx, formatted_exception)
                    self.flip.send_handled_exception(
                        formatted_exception=formatted_exception,
                        client_name=client_name,
                        model_id=self._resolve_model_id(fl_ctx),
                    )
            return bool(super()._accept_train_result(client_name, result, fl_ctx, is_unknown_task))

        # Size the client's actual (possibly partial, head-only) update now — reconstruction
        # below rewrites the shareable into the full model.
        size_bytes = self._client_update_size(result, fl_ctx)

        # OK result: reconstruct full WEIGHTS from the (partial) WEIGHT_DIFF before the base aggregates.
        result = self._diff_to_weights(result, fl_ctx)
        accepted = bool(super()._accept_train_result(client_name, result, fl_ctx, is_unknown_task))

        # Relay only what the base class genuinely accepted: an aggregator-rejected result
        # (e.g. a stale contribution_round cookie) or a late unknown-task reply (whose weights
        # belong to an earlier round) must not surface as an accepted upload of the current
        # round or inflate the "k of m" in ROUND_AGGREGATED.
        if accepted and not is_unknown_task:
            self._report_client_result(client_name, size_bytes, fl_ctx)
        return accepted

    def _client_update_size(self, result: Shareable, fl_ctx: FLContext) -> int | None:
        """Best-effort byte size of the client's update; ``None`` when it cannot be sized."""
        try:
            dxo = from_shareable(result)
            return int(sum(getattr(arr, "nbytes", 0) for arr in dxo.data.values()))
        except Exception as e:
            # Distinguishes "my probe broke" (e.g. an NVFLARE shareable-shape
            # change) from a genuinely sizeless result — otherwise sizes
            # vanish silently forever after an upgrade.
            self.log_debug(fl_ctx, f"Could not size the client update: {e}")
            return None

    def _report_client_result(self, client_name: str, size_bytes: int | None, fl_ctx: FLContext) -> None:
        """Emit a CLIENT_RESULT_RECEIVED fact and refresh the per-round acceptance counts.

        Called only after the base class (and its aggregator) accepted the result — the
        event's contract is "per accepted result". Best-effort telemetry: any failure here
        is logged and must never block result acceptance. The counts are shared as sticky
        fl_ctx props so ServerEventHandler can attach them to the ROUND_AGGREGATED event on
        ROUND_DONE. NVFLARE's ``_current_round`` is 0-based; the wire contract is 1-based.
        """
        try:
            if self._current_round is None:
                return

            accepted = self._round_acceptances.setdefault(self._current_round, set())
            accepted.add(client_name)
            fl_ctx.set_prop(FlipProps.ROUND_RETURNED, len(accepted), private=True, sticky=True)
            fl_ctx.set_prop(
                FlipProps.ROUND_EXPECTED, getattr(self, "_current_num_targets", None), private=True, sticky=True
            )

            self.flip.send_event(
                model_id=self._resolve_model_id(fl_ctx),
                event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
                global_round=self._current_round + 1,
                client_name=client_name,
                details={"size_bytes": size_bytes} if size_bytes is not None else None,
            )
        except Exception as e:
            self.log_error(fl_ctx, f"Failed to relay accepted result from {client_name} to the hub: {e}")

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        super().handle_event(event_type, fl_ctx)
        if event_type == FlipEvents.SEND_RESULT:
            event_data = fl_ctx.get_prop(FLContextKey.EVENT_DATA, None)
            if event_data is None:
                self.log_error(fl_ctx, "Metrics Error: metrics result event was fired but no data found")
                return
            if self._current_round is None:
                # SEND_RESULT is broadcast to every controller. In a multi-phase job (e.g. the
                # diffusion AE/DM split) a controller whose control_flow has not started yet still
                # has _current_round is None (as stock initialises it); it must not relay another
                # controller's metric with a None round. The active controller relays it.
                return
            handle_metrics_event(event_data, self._current_round, self._resolve_model_id(fl_ctx), flip=self.flip)
            self._track_validation_metric(event_data, fl_ctx)
        elif event_type == AppEventType.ROUND_DONE:
            # Stock fires ROUND_DONE after aggregation and persist, with _current_round still on the
            # finished round — exactly when _round_metrics holds that round's metric and
            # _global_weights the freshly aggregated model. ROUND_DONE is broadcast to every
            # component, so gate on the training phase: a controller that has finished (or not yet
            # started) its control_flow must not react to another workflow's rounds.
            if self._phase == AppConstants.PHASE_TRAIN:
                self._update_best_model(fl_ctx)

    def _track_validation_metric(self, event_data, fl_ctx: FLContext) -> None:
        """Track validation metrics for best-model selection.

        Extracts the validation metric from event data and stores it for the current round.
        If best_model_metric is set, only tracks that metric; otherwise tracks the first
        validation metric received (VAL_* or TEST_*).
        """
        try:
            # event_data is a Shareable, convert to DXO to extract metrics
            dxo = from_shareable(event_data)
            metrics_data = dxo.data

            if "label" in metrics_data and "value" in metrics_data:
                label = metrics_data.get("label")
                value = metrics_data.get("value")

                # Check if this is a metric we should track
                should_track = False
                if self.best_model_metric:
                    # Track only the specified metric
                    should_track = label == self.best_model_metric
                else:
                    # Track any validation metric (VAL_* or TEST_*)
                    should_track = label.startswith(("VAL_", "TEST_"))

                if should_track and isinstance(value, (int, float)):
                    if self._current_round is not None:
                        # Warn once if best_model_metric not specified
                        if self.best_model_metric is None and not self._metric_warning_logged:
                            self.log_warning(
                                fl_ctx,
                                f"best_model_metric not specified. Auto-detected first validation metric: "
                                f"{label}. Set best_model_metric explicitly in config to control which metric "
                                f"drives best-model selection.",
                            )
                            self._metric_warning_logged = True

                        self._round_metrics[self._current_round] = float(value)
        except Exception as e:
            self.log_error(fl_ctx, f"Error tracking validation metric: {e}")

    def _update_best_model(self, fl_ctx: FLContext) -> None:
        """Save the current model as best model if it has the best validation metric.

        This is called after each round completes. Compares the current round's
        validation metric against the best seen so far and saves the model if it's better.
        Uses best_model_metric_minimize to determine optimization direction.
        """
        try:
            if self._current_round is None or self.persistor is None:
                return

            # Get current round's validation metric
            current_metric = self._round_metrics.get(self._current_round)
            if current_metric is None:
                return

            # Check if this is the best round so far
            is_better = False
            if self._best_metric is None:
                is_better = True
            elif self.best_model_metric_minimize:
                is_better = current_metric < self._best_metric
            else:
                is_better = current_metric > self._best_metric

            if is_better:
                self._best_metric = current_metric
                self._best_round = self._current_round

                # Get model location from persistor's model inventory (production-safe)
                model_inventory = self.persistor.get_model_inventory(fl_ctx)
                if PTConstants.PTFileModelName not in model_inventory:
                    self.log_error(fl_ctx, "No model in inventory, cannot save best model")
                    return

                final_model_location = model_inventory[PTConstants.PTFileModelName].location
                model_dir = os.path.dirname(final_model_location)
                best_model_path = os.path.join(model_dir, PTConstants.BestModelFilename)

                torch.save(self._global_weights, best_model_path)
        except Exception as e:
            self.log_error(fl_ctx, f"Error updating best model: {e}")

    def _check_abort_signal(self, fl_ctx: FLContext, abort_signal: Signal) -> bool:
        aborted = bool(super()._check_abort_signal(fl_ctx, abort_signal))
        if aborted:
            self.fire_event(FlipEvents.ABORTED, fl_ctx)
        return aborted

    def get_persist_state(self, fl_ctx: FLContext) -> dict:
        state: dict = super().get_persist_state(fl_ctx)
        state.update(
            best_metric=self._best_metric,
            best_round=self._best_round,
            round_metrics=self._round_metrics,
        )
        return state

    def restore(self, state_data: dict, fl_ctx: FLContext) -> None:
        super().restore(state_data, fl_ctx)
        self._best_metric = state_data.get("best_metric")
        self._best_round = state_data.get("best_round")
        self._round_metrics = state_data.get("round_metrics", {})
