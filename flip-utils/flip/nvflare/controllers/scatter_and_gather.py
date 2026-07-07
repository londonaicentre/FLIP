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

from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.workflows.scatter_and_gather import ScatterAndGather as NVFlareScatterAndGather
from nvflare.app_opt.pt.fedopt import PTFedOptModelShareableGenerator

from flip import FLIP
from flip.constants import FlipEvents
from flip.nvflare.metrics import handle_metrics_event
from flip.nvflare.runtime import get_flip_model_id


class ScatterAndGather(NVFlareScatterAndGather):
    """FLIP's FedAvg controller — a thin subclass of NVFLARE's stock ``ScatterAndGather``.

    FLIP previously vendored a full copy of the stock controller, which drifted out of date. This
    subclass inherits stock's round loop verbatim — and with it ``memory_gc_rounds`` allocator-aware
    cleanup (which bounds server RSS on large-model jobs), the per-round ``aggregator.reset``,
    ``allow_empty_global_weights``, and any future upstream fixes. Only the four FLIP-specific hooks
    are overridden:

      * :meth:`__init__` — carries the FLIP ``model_id`` and a :class:`FLIP` client, and disables
        stock's per-round component snapshot by default (``snapshot_every_n_rounds=0``). Snapshotting
        re-serialises the full global model every round; for a ~759 MiB model that re-introduces the
        very per-round memory churn ``memory_gc_rounds`` exists to bound. FLIP never snapshotted, so
        ``0`` also preserves prior behaviour. It stays configurable for callers that want resilience.
      * :meth:`_accept_train_result` — reports a client-side execution exception to the hub, and
        converts a (possibly partial, frozen-backbone) ``WEIGHT_DIFF`` head update into full
        ``WEIGHTS`` before aggregation, since FLIP's aggregator expects ``WEIGHTS`` (FLIP#684).
      * :meth:`handle_event` — relays FLIP metrics on ``FlipEvents.SEND_RESULT``.
      * :meth:`_check_abort_signal` — fires ``FlipEvents.ABORTED`` so downstream components (e.g.
        ``PersistToS3AndCleanup``) can persist results on an aborted run.
    """

    def __init__(self, model_id: str = "", **kwargs) -> None:
        # FLIP has never snapshotted components each round; keep that off by default so a large-model
        # job does not re-serialise the full global model every round (see the class docstring). Left
        # in kwargs so an explicit config value still wins.
        kwargs.setdefault("snapshot_every_n_rounds", 0)
        super().__init__(**kwargs)
        self._model_id_fallback = model_id
        self._model_id: str | None = None
        self.flip = FLIP()

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

        # OK result: reconstruct full WEIGHTS from the (partial) WEIGHT_DIFF before the base aggregates.
        result = self._diff_to_weights(result, fl_ctx)
        return bool(super()._accept_train_result(client_name, result, fl_ctx, is_unknown_task))

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        super().handle_event(event_type, fl_ctx)
        if event_type == FlipEvents.SEND_RESULT:
            event_data = fl_ctx.get_prop(FLContextKey.EVENT_DATA, None)
            if event_data is None:
                self.log_error(fl_ctx, "Metrics Error: metrics result event was fired but no data found")
                return
            handle_metrics_event(event_data, self._current_round, self._resolve_model_id(fl_ctx), flip=self.flip)

    def _check_abort_signal(self, fl_ctx: FLContext, abort_signal: Signal) -> bool:
        aborted = bool(super()._check_abort_signal(fl_ctx, abort_signal))
        if aborted:
            self.fire_event(FlipEvents.ABORTED, fl_ctx)
        return aborted
