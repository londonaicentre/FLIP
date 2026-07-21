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

from nvflare.apis.event_type import EventType
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType

from flip import FLIP
from flip.constants import FlipEvents, FlipProps, ModelStatus
from flip.exceptions import ResultsUploadError
from flip.nvflare.components.evaluation_json_generator import EvaluationJsonGenerator
from flip.nvflare.components.persist_and_cleanup import PersistToS3AndCleanup
from flip.nvflare.runtime import get_flip_model_id
from flip.schemas import FLLogEvent


class ServerEventHandler(FLComponent):
    """ServerEventHandler is a generic component that handles system events triggered by nvflare
    or custom flip events. It executes logic inside its own event handler but may also call
    other component's event handlers directly to overcome the non-deterministic order
    in which nvflare handles events i.e handling ValidationJsonGenerator component events.

    Args:
        model_id (str, optional): ID of the model. When empty, the model ID is resolved lazily
            from job metadata via ``get_flip_model_id`` on first status update.
        validation_json_generator_id (str, optional): Component ID for the validation JSON generator.
        persist_and_cleanup_id (str, optional): Component ID for the persist-and-cleanup component.
        flip (FLIP, optional): FLIP client instance.
    """

    def __init__(
        self,
        model_id: str = "",
        validation_json_generator_id: str = "json_generator",
        persist_and_cleanup_id: str = "persist_and_cleanup",
        flip: FLIP = FLIP(),
    ):
        super(ServerEventHandler, self).__init__()

        self._model_id_fallback = model_id
        self._model_id: str | None = None
        self.validation_json_generator_id = validation_json_generator_id
        self.validation_json_generator = None
        self.persist_and_cleanup_id = persist_and_cleanup_id
        self.persist_and_cleanup = None
        self.flip = flip

        self.fatal_error = False
        self.final_status: ModelStatus | None = None

    def _resolve_model_id(self, fl_ctx: FLContext) -> str:
        """Resolve model ID lazily from job metadata, falling back to the constructor arg.

        Args:
            fl_ctx (FLContext): The FL context for the current job.

        Returns:
            str: The resolved model ID.
        """
        if self._model_id is None:
            self._model_id = get_flip_model_id(fl_ctx, fallback=self._model_id_fallback)
        return self._model_id

    def _update_status(self, fl_ctx: FLContext, status: ModelStatus | None) -> None:
        """Resolve model ID lazily and update training status.

        Args:
            fl_ctx (FLContext): The FL context for the current job.
            status (ModelStatus | None): The new model status to set.
        """
        self.flip.update_status(self._resolve_model_id(fl_ctx), status)

    def _relay_round_event(self, fl_ctx: FLContext, event: FLLogEvent) -> None:
        """Relay a stock round boundary to the hub as a typed fact.

        Facts only — display text is composed hub-side. NVFLARE's
        ``CURRENT_ROUND`` prop is 0-based; the wire contract is 1-based.
        ``ROUND_AGGREGATED`` counts come from the sticky props seeded here at
        round start ("0 of m") and overwritten by the FLIP ScatterAndGather
        controller as it accepts client results.

        Args:
            fl_ctx (FLContext): The FL context carrying the round props.
            event (FLLogEvent): ROUND_STARTED or ROUND_AGGREGATED.
        """
        current_round = fl_ctx.get_prop(AppConstants.CURRENT_ROUND)
        if current_round is None:
            return

        details = None
        if event == FLLogEvent.ROUND_STARTED:
            num_rounds = fl_ctx.get_prop(AppConstants.NUM_ROUNDS)
            if num_rounds is not None:
                details = {"total_rounds": num_rounds}
            # Reset the counts at round start so ROUND_DONE never reports the previous
            # round's numbers — and seed them to "0 of m" rather than clearing to None,
            # so a round in which every client fails still closes with an honest count
            # (mirroring the Flower arm) instead of a bare "Round N aggregated". The
            # denominator is derived exactly as stock SAG derives _current_num_targets
            # (len(engine.get_clients())) — the controller attribute itself still holds
            # the previous round's value when ROUND_STARTED fires. Acceptances overwrite
            # both counts; an unreadable engine degrades to the uncounted wording.
            expected = self._expected_client_count(fl_ctx)
            fl_ctx.set_prop(FlipProps.ROUND_RETURNED, 0 if expected is not None else None, private=True, sticky=True)
            fl_ctx.set_prop(FlipProps.ROUND_EXPECTED, expected, private=True, sticky=True)
        else:
            returned = fl_ctx.get_prop(FlipProps.ROUND_RETURNED)
            expected = fl_ctx.get_prop(FlipProps.ROUND_EXPECTED)
            if returned is not None and expected is not None:
                details = {"returned": returned, "expected": expected}

        self.flip.send_event(
            model_id=self._resolve_model_id(fl_ctx),
            event_type=event,
            global_round=current_round + 1,
            details=details,
        )

    def _expected_client_count(self, fl_ctx: FLContext) -> int | None:
        """Best-effort count of the clients targeted this round; ``None`` when unavailable.

        Args:
            fl_ctx (FLContext): The FL context to read the engine from.

        Returns:
            int | None: ``len(engine.get_clients())``, or ``None`` when the engine
            cannot be read (the round then closes with the uncounted wording).
        """
        try:
            return len(fl_ctx.get_engine().get_clients())
        except Exception as e:
            self.log_debug(fl_ctx, f"Could not read the participating client count: {e}")
            return None

    def _evaluation_wholly_failed(self) -> bool:
        """Whether this is an evaluation job in which every validate task failed.

        Training jobs wire the base ``ValidationJsonGenerator``, which tracks no failures, so the
        isinstance check also serves as the "is this an evaluation job" test.

        Returns:
            bool: True when the evaluation produced failures and no results at all.
        """
        generator = self.validation_json_generator
        return isinstance(generator, EvaluationJsonGenerator) and generator.all_tasks_failed()

    def _terminal_status(self, default: ModelStatus) -> ModelStatus:
        """Resolve the run's terminal status, most salient cause first.

        A recorded fatal system error is the root cause and outranks everything. A user-requested
        abort outranks the evaluation failures it necessarily caused (aborting a run cancels its
        in-flight validate tasks, which must not be reported as ERROR). An evaluation in which every
        validate task failed outranks ``default`` — otherwise a wholly failed run reports success on
        an empty results file (FLIP#754), or reports a trailing upload failure as its cause.

        Args:
            default (ModelStatus): The status to use when no failure cause is recorded — the outcome
                of the upload itself.

        Returns:
            ModelStatus: The status to report to the hub.
        """
        if self.fatal_error:
            return ModelStatus.ERROR
        if self.final_status == ModelStatus.STOPPED:
            return ModelStatus.STOPPED
        if self._evaluation_wholly_failed():
            return ModelStatus.ERROR
        return default

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        self.__set_dependencies(fl_ctx)

        self.validation_json_generator.handle_evaluation_events(event_type, fl_ctx)

        if event_type == EventType.FATAL_SYSTEM_ERROR:
            self.log_error(fl_ctx, "Fatal system error event received")
            self.fatal_error = True

        elif event_type == FlipEvents.TRAINING_INITIATED:
            self.log_info(fl_ctx, "Training initiated event received")
            self._update_status(fl_ctx, ModelStatus.INITIATED)

        elif event_type == AppEventType.INITIAL_MODEL_LOADED:
            self.log_info(fl_ctx, "Initial model loaded event received")
            self._update_status(fl_ctx, ModelStatus.PREPARED)

        elif event_type == AppEventType.TRAINING_STARTED:
            self.log_info(fl_ctx, "Training started event received")
            self._update_status(fl_ctx, ModelStatus.TRAINING_STARTED)

        elif event_type == AppEventType.ROUND_STARTED:
            self._relay_round_event(fl_ctx, FLLogEvent.ROUND_STARTED)

        elif event_type == AppEventType.ROUND_DONE:
            self._relay_round_event(fl_ctx, FLLogEvent.ROUND_AGGREGATED)

        elif event_type == AppEventType.TRAINING_FINISHED:
            self.log_info(fl_ctx, "Training finished event received")

        elif event_type == FlipEvents.RESULTS_UPLOAD_COMPLETED:
            self.log_info(fl_ctx, "Results upload completed event received")

        elif event_type == FlipEvents.ABORTED:
            self.log_info(fl_ctx, "Aborted event received")
            self.final_status = ModelStatus.STOPPED

        elif event_type == EventType.START_RUN:
            self.log_info(fl_ctx, "Start run event received")

        elif event_type == EventType.END_RUN:
            self.log_info(fl_ctx, "End run event received")

            try:
                # The results are uploaded even when the evaluation wholly failed: the zip carries
                # the error_log.txt and evaluation_failures.json that explain why.
                self.persist_and_cleanup.execute(fl_ctx)
                self.final_status = self._terminal_status(ModelStatus.RESULTS_UPLOADED)
            except ResultsUploadError:
                self.final_status = self._terminal_status(ModelStatus.RESULTS_UPLOAD_FAILED)
            except Exception:
                self.final_status = ModelStatus.ERROR

            self._update_status(fl_ctx, self.final_status)

    def __set_dependencies(self, fl_ctx: FLContext) -> None:
        if self.validation_json_generator is None:
            engine = fl_ctx.get_engine()
            self.validation_json_generator = engine.get_component(self.validation_json_generator_id)

            if self.validation_json_generator is None or not hasattr(
                self.validation_json_generator, "handle_evaluation_events"
            ):
                self.system_panic(
                    f"'validation_json_generator_id' component must have 'handle_evaluation_events' method. "
                    f"But got: {type(self.validation_json_generator)}",
                    fl_ctx,
                )
                return

        if self.persist_and_cleanup is None:
            engine = fl_ctx.get_engine()
            self.persist_and_cleanup = engine.get_component(self.persist_and_cleanup_id)

            if self.persist_and_cleanup is None or not isinstance(self.persist_and_cleanup, PersistToS3AndCleanup):
                self.system_panic(
                    f"'persist_and_cleanup_id' component must be PersistToS3AndCleanup. "
                    f"But got: {type(self.persist_and_cleanup)}",
                    fl_ctx,
                )
                return
