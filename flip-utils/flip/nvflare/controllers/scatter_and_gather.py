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

from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.fl_constant import FLContextKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.app_event_type import AppEventType
from nvflare.app_common.workflows.scatter_and_gather import ScatterAndGather as NVFlareScatterAndGather

from flip import FLIP
from flip.constants import FlipEvents, FlipProps
from flip.nvflare.metrics import handle_metrics_event
from flip.nvflare.runtime import get_flip_model_id
from flip.schemas import FLLogEvent


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
      * :meth:`_accept_train_result` — reports every failed client task to the hub (the
        ``"exception"`` header when the client attached one, a pointed fallback naming the return
        code and the trust-side logs otherwise) and relays each genuinely accepted result to the
        hub as a ``CLIENT_RESULT_RECEIVED`` fact. Client ``WEIGHT_DIFF`` updates reach the
        aggregator untouched — stock NVFLARE semantics: the aggregator averages the diffs and
        ``FullModelShareableGenerator`` (or the FedOpt generator) applies the average to the
        global model. This is natively partial-safe for frozen-backbone head-only updates (keys
        absent from the diff keep their global value), which is what the removed DIFF→WEIGHTS
        reconstruction existed to guarantee (FLIP#684) against the legacy templates'
        ``WEIGHTS``-expecting aggregator. A result whose data kind cannot match the aggregator's
        expectation (e.g. a trainer sending ``params_type="FULL"`` → ``WEIGHTS`` — NVFLARE's
        default when ``params_type`` is omitted) is reported to the hub with an actionable
        message instead of stock's server-log-only rejection.
      * :meth:`handle_event` — relays FLIP metrics on ``FlipEvents.SEND_RESULT``, and panics on
        ``BEFORE_AGGREGATION`` when a training round accepted **zero** client results: stock
        would aggregate an empty diff, apply it as a no-op, and march every remaining round to a
        COMPLETED job whose "trained" model is bit-identical to round 0. There is no legitimate
        run where every client's update is rejected, so that state aborts loudly instead.
      * :meth:`_check_abort_signal` — fires ``FlipEvents.ABORTED`` so downstream components (e.g.
        ``PersistToS3AndCleanup``) can persist results on an aborted run.
    """

    def __init__(self, *args, model_id: str = "", **kwargs) -> None:
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
        # Rounds where a client sent an aggregator-incompatible weights kind (reported once per
        # round); enriches the zero-acceptance abort with its likely cause.
        self._round_kind_mismatches: set[int] = set()

    def _resolve_model_id(self, fl_ctx: FLContext) -> str:
        if self._model_id is None:
            self._model_id = get_flip_model_id(fl_ctx, fallback=self._model_id_fallback)
        return self._model_id

    def _accept_train_result(
        self, client_name: str, result: Shareable, fl_ctx: FLContext, is_unknown_task: bool = False
    ) -> bool:
        rc = result.get_return_code()
        if rc and rc != ReturnCode.OK:
            # FLIP: surface every failed client task to the hub before the base class applies its
            # ignore/panic policy. The "exception" header carries the client's own formatted
            # traceback when something attached one; the Client-API executors return bare replies
            # (e.g. TASK_ABORTED when the training script died pre-flare.init), so without the
            # fallback the researcher gets a generic hub error and the actual cause — a missing
            # enrichment step, an OOM, a typo — exists only in a trust-side container log they
            # cannot read.
            formatted_exception = result.get_header("exception")
            if formatted_exception is None:
                formatted_exception = (
                    f"Client task failed with return code {rc} before reporting a traceback — "
                    "the full error is in the fl-client container logs at the trust."
                )
            self.log_error(fl_ctx, formatted_exception)
            self.flip.send_handled_exception(
                formatted_exception=formatted_exception,
                client_name=client_name,
                model_id=self._resolve_model_id(fl_ctx),
            )
            return bool(super()._accept_train_result(client_name, result, fl_ctx, is_unknown_task))

        # Size the client's actual (possibly partial, head-only) update.
        size_bytes = self._client_update_size(result, fl_ctx)

        self._report_data_kind_mismatch(client_name, result, fl_ctx)

        accepted = bool(super()._accept_train_result(client_name, result, fl_ctx, is_unknown_task))

        # Relay only what the base class genuinely accepted: an aggregator-rejected result
        # (e.g. a stale contribution_round cookie) or a late unknown-task reply (whose weights
        # belong to an earlier round) must not surface as an accepted upload of the current
        # round or inflate the "k of m" in ROUND_AGGREGATED.
        if accepted and not is_unknown_task:
            self._report_client_result(client_name, size_bytes, fl_ctx)
        return accepted

    def _report_data_kind_mismatch(self, client_name: str, result: Shareable, fl_ctx: FLContext) -> None:
        """Report an aggregator-incompatible weights kind to the hub with an actionable message.

        Stock's rejection of a wrong-kind DXO is one ``log_error`` line inside the fl-server
        container ("expected WEIGHT_DIFF but got WEIGHTS") and an INFO "REJECTED" — invisible to
        the researcher, whose local metrics keep streaming while the global model never moves.
        The overwhelmingly likely cause is a trainer sending ``params_type="FULL"`` (NVFLARE's
        default when omitted), so name that fix outright. Best-effort: probing failures must
        never block acceptance (the aggregator remains the authority on rejection).
        """
        try:
            round_num = self._current_round
            if round_num is None or round_num in self._round_kind_mismatches:
                return
            dxo = from_shareable(result)
            expected = getattr(self.aggregator, "expected_data_kind", None)
            if expected is None:
                # An aggregator without the attribute is not this check's business.
                return
            # Runtime shape is the {dxo_name: kind} dict form; hand-constructed aggregators carry
            # the plain enum. Only weights-family kinds are probed — anything else is out of this
            # check's scope and left to the aggregator.
            expected_kinds = set(expected.values()) if isinstance(expected, dict) else {expected}
            if dxo.data_kind in (DataKind.WEIGHTS, DataKind.WEIGHT_DIFF) and dxo.data_kind not in expected_kinds:
                self._round_kind_mismatches.add(round_num)
                message = (
                    f"{client_name}'s training update was sent as {dxo.data_kind}, but the aggregator expects "
                    f"{', '.join(sorted(str(k) for k in expected_kinds))} — every such contribution is rejected. "
                    "A FLIP Client-API trainer must send its update as a diff: "
                    "FLModel(params=<local minus global>, params_type='DIFF'). Note params_type='FULL' is "
                    "NVFLARE's default when params_type is omitted."
                )
                self.log_error(fl_ctx, message)
                self.flip.send_handled_exception(
                    formatted_exception=message,
                    client_name=client_name,
                    model_id=self._resolve_model_id(fl_ctx),
                )
        except Exception as e:
            self.log_debug(fl_ctx, f"Could not probe the client update's data kind: {e}")

    def _panic_if_round_accepted_nothing(self, fl_ctx: FLContext) -> None:
        """Abort the job when a training round is about to aggregate zero accepted results.

        Fired on ``BEFORE_AGGREGATION``, when every client has responded: acceptance counts are
        final, and the empty aggregate has not yet been applied. Without this, stock aggregates
        an empty diff, applies it as a no-op, and completes every remaining round — the job ends
        COMPLETED with a "trained" model bit-identical to round 0.
        """
        round_num = self._current_round
        if round_num is None:
            return
        expected = getattr(self, "_current_num_targets", None)
        if not expected or self._round_acceptances.get(round_num):
            return
        reason = (
            f"Training round {round_num + 1} accepted 0 of {expected} client results — aborting instead of "
            "applying an empty aggregate (the run would otherwise complete with an untrained global model)."
        )
        if round_num in self._round_kind_mismatches:
            reason += (
                " Cause on record this round: client updates were sent with an aggregator-incompatible "
                "data kind — see the earlier data-kind error for the fix."
            )
        try:
            self.flip.send_handled_exception(
                formatted_exception=reason,
                client_name=None,
                model_id=self._resolve_model_id(fl_ctx),
            )
        except Exception as e:
            self.log_error(fl_ctx, f"Failed to relay the zero-acceptance abort to the hub: {e}")
        self.system_panic(reason, fl_ctx)

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
        if event_type == AppEventType.BEFORE_AGGREGATION:
            # Every controller receives every fired event; the _current_round guard inside keeps
            # a not-yet-started sibling controller (multi-phase jobs) from panicking on the
            # active one's rounds, and a finished sibling's last round always has acceptances.
            self._panic_if_round_accepted_nothing(fl_ctx)
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

    def _check_abort_signal(self, fl_ctx: FLContext, abort_signal: Signal) -> bool:
        aborted = bool(super()._check_abort_signal(fl_ctx, abort_signal))
        if aborted:
            self.fire_event(FlipEvents.ABORTED, fl_ctx)
        return aborted
