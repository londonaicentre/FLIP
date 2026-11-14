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

from unittest.mock import MagicMock, Mock, call, patch

import pytest
from nvflare.apis.event_type import EventType
from nvflare.app_common.app_event_type import AppEventType

from flip.constants import FlipEvents, FlipProps, ModelStatus
from flip.exceptions import ResultsUploadError
from flip.nvflare.components.evaluation_json_generator import EvaluationJsonGenerator
from flip.nvflare.components.flip_server_event_handler import ServerEventHandler
from flip.nvflare.components.persist_and_cleanup import PersistToS3AndCleanup
from flip.nvflare.components.validation_json_generator import ValidationJsonGenerator
from flip.schemas import FLLogEvent

_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _dispatch(event, *, fatal=False, preset=None, side_effect=None):
    """Fire ``event`` at a fresh handler wired to a validation generator and a persist component.

    ``fatal``/``preset`` set the handler's fatal_error/final_status first; ``side_effect`` is what
    persist_cleanup.execute raises. Returns (flip, handler, fl_ctx, persist_cleanup).
    """
    flip = MagicMock()
    handler = ServerEventHandler(model_id=_MODEL_ID, flip=flip)
    if fatal:
        handler.fatal_error = True
    if preset is not None:
        handler.final_status = preset

    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    engine = MagicMock()
    fl_ctx.get_engine.return_value = engine

    json_generator = Mock(spec=ValidationJsonGenerator)
    persist_cleanup = Mock(spec=PersistToS3AndCleanup)
    if side_effect is not None:
        persist_cleanup.execute.side_effect = side_effect
    else:
        persist_cleanup.execute.return_value = None
    engine.get_component.side_effect = lambda comp_id: (
        json_generator if comp_id == "json_generator" else persist_cleanup
    )

    handler.handle_event(event, fl_ctx)
    return flip, handler, fl_ctx, persist_cleanup


class TestServerEventHandler:
    """Tests for ServerEventHandler component"""

    def test_init_with_valid_model_id(self):
        """Test initialization with valid model UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        assert handler._model_id_fallback == model_id
        assert handler._model_id is None
        assert handler.validation_json_generator_id == "json_generator"
        assert handler.persist_and_cleanup_id == "persist_and_cleanup"
        assert handler.flip == flip
        assert handler.fatal_error is False
        assert handler.final_status is None

    def test_resolves_model_id_from_fallback_arg(self):
        """_update_status resolves the model ID from the constructor fallback when no job-metadata prop is set."""
        model_id = "abcdef01-2345-6789-abcd-ef0123456789"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None
        handler._update_status(fl_ctx, ModelStatus.INITIATED)
        flip.update_status.assert_called_once_with(model_id, ModelStatus.INITIATED)

    def test_resolve_model_id_caches_after_first_call(self):
        """_resolve_model_id resolves once and caches: a second call does not re-invoke get_flip_model_id."""
        model_id = "abcdef01-2345-6789-abcd-ef0123456789"
        handler = ServerEventHandler(model_id=model_id)
        fl_ctx = MagicMock()

        with patch(
            "flip.nvflare.components.flip_server_event_handler.get_flip_model_id", return_value=model_id
        ) as mock_resolve:
            assert handler._resolve_model_id(fl_ctx) == model_id
            assert handler._resolve_model_id(fl_ctx) == model_id

        assert mock_resolve.call_count == 1

    def test_init_with_custom_component_ids(self):
        """Test initialization with custom component IDs"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(
            model_id=model_id,
            validation_json_generator_id="custom_json_gen",
            persist_and_cleanup_id="custom_persist",
            flip=flip,
        )

        assert handler.validation_json_generator_id == "custom_json_gen"
        assert handler.persist_and_cleanup_id == "custom_persist"

    def test_handle_event_training_initiated(self):
        """Test handle_event with TRAINING_INITIATED event"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        # Setup mocks
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        # Execute
        handler.handle_event(FlipEvents.TRAINING_INITIATED, fl_ctx)

        # Verify
        json_generator.handle_evaluation_events.assert_called_once()
        flip.update_status.assert_called_with(model_id, ModelStatus.INITIATED)

    @pytest.mark.parametrize(
        ("event", "status"),
        [
            pytest.param(AppEventType.INITIAL_MODEL_LOADED, ModelStatus.PREPARED, id="initial_model_loaded"),
            pytest.param(AppEventType.TRAINING_STARTED, ModelStatus.RUNNING, id="training_started"),
            # Evaluation jobs signal start with TASK_INITIATED (#782).
            pytest.param(FlipEvents.TASK_INITIATED, ModelStatus.RUNNING, id="task_initiated"),
        ],
    )
    def test_handle_event_updates_status(self, event, status):
        """handle_event relays each lifecycle event to the hub as its model status."""
        flip, _, _, _ = _dispatch(event)

        flip.update_status.assert_called_with(_MODEL_ID, status)

    def test_handle_event_fatal_system_error(self):
        """Test handle_event with FATAL_SYSTEM_ERROR event"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.log_error = MagicMock()  # Mock log_error to avoid FLContext type check

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.FATAL_SYSTEM_ERROR, fl_ctx)

        assert handler.fatal_error is True

    def test_handle_event_aborted(self):
        """Test handle_event with ABORTED event"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(FlipEvents.ABORTED, fl_ctx)

        assert handler.final_status == ModelStatus.STOPPED

    @pytest.mark.parametrize(
        ("fatal", "preset", "side_effect", "expected"),
        [
            (False, None, None, ModelStatus.RESULTS_UPLOADED),
            (True, None, None, ModelStatus.ERROR),
            (False, ModelStatus.STOPPED, None, ModelStatus.STOPPED),
            (False, None, Exception("Persist failed"), ModelStatus.ERROR),
            (False, None, ResultsUploadError("S3 upload failed"), ModelStatus.RESULTS_UPLOAD_FAILED),
            # A recorded fatal system error takes precedence over a trailing upload failure.
            (True, None, ResultsUploadError("S3 upload failed"), ModelStatus.ERROR),
            # A user-requested abort (STOPPED) is preserved over a trailing upload failure.
            (False, ModelStatus.STOPPED, ResultsUploadError("S3 upload failed"), ModelStatus.STOPPED),
        ],
        ids=[
            "success",
            "fatal",
            "stopped",
            "persist_raises",
            "upload_failed",
            "upload_failed_fatal_wins",
            "upload_failed_stopped_wins",
        ],
    )
    def test_handle_event_end_run(self, fatal, preset, side_effect, expected):
        """END_RUN maps the persist/upload outcome and any earlier fatal error or abort to the final status."""
        flip, handler, fl_ctx, persist_cleanup = _dispatch(
            EventType.END_RUN, fatal=fatal, preset=preset, side_effect=side_effect
        )

        if side_effect is None and not fatal and preset is None:
            persist_cleanup.execute.assert_called_once_with(fl_ctx)
        assert handler.final_status == expected
        flip.update_status.assert_called_with(_MODEL_ID, expected)

    @pytest.mark.parametrize(
        "event",
        [
            pytest.param(AppEventType.TRAINING_FINISHED, id="training_finished"),
            pytest.param(FlipEvents.RESULTS_UPLOAD_COMPLETED, id="results_upload_completed"),
            pytest.param(EventType.START_RUN, id="start_run"),
        ],
    )
    def test_handle_event_informational(self, event):
        """Informational events are dispatched without raising (they log, not change status)."""
        _dispatch(event)

    def test_handle_event_invalid_json_generator_component(self):
        """Test handle_event when validation_json_generator is not correct type"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.system_panic = MagicMock(side_effect=lambda *args, **kwargs: None)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Return None
        engine.get_component.return_value = None

        handler.handle_event(FlipEvents.TRAINING_INITIATED, fl_ctx)

        # The missing component is reported once, and the event is not relayed to the hub
        # on top of it: the panic already means the run is going down.
        handler.system_panic.assert_called_once()
        assert "must have 'handle_evaluation_events' method" in str(handler.system_panic.call_args)
        flip.update_status.assert_not_called()

    def test_handle_event_invalid_persist_cleanup_component(self):
        """Test handle_event when persist_and_cleanup is not correct type"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Return correct json_generator but wrong persist_and_cleanup
        json_generator = Mock(spec=ValidationJsonGenerator)
        engine.get_component.side_effect = lambda comp_id: json_generator if comp_id == "json_generator" else None

        # Set json_generator initially then try to trigger persist_and_cleanup check
        handler.validation_json_generator = json_generator
        handler.handle_event(FlipEvents.TRAINING_INITIATED, fl_ctx)

        handler.system_panic.assert_called_once()
        assert "must be PersistToS3AndCleanup" in str(handler.system_panic.call_args)

    def test_end_run_missing_persist_cleanup_reports_error(self):
        """A missing persist-and-cleanup component must still terminate the hub-side model status."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        engine.get_component.side_effect = lambda component_id: (
            json_generator if component_id == "json_generator" else None
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        handler.system_panic.assert_called_once()
        assert handler.final_status == ModelStatus.ERROR
        flip.update_status.assert_called_once_with(model_id, ModelStatus.ERROR)

    # --- FLIP#754: an evaluation whose every validate task failed must not report success ---

    @staticmethod
    def _end_run(handler, *, all_tasks_failed, flip):
        """Drive END_RUN with an evaluation generator reporting the given all-failed verdict."""
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=EvaluationJsonGenerator)
        json_generator.all_tasks_failed.return_value = all_tasks_failed
        json_generator.hub_metrics.return_value = {}
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.return_value = None
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)
        return persist_cleanup

    def test_end_run_reports_error_when_every_evaluation_task_failed(self):
        """FLIP#754: 4/4 aborted validate tasks previously reported RESULTS_UPLOADED."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        self._end_run(handler, all_tasks_failed=True, flip=flip)

        assert handler.final_status == ModelStatus.ERROR
        flip.update_status.assert_called_with(model_id, ModelStatus.ERROR)

    def test_end_run_still_uploads_results_when_every_evaluation_task_failed(self):
        """The zip carries error_log.txt and evaluation_failures.json, so it must still upload."""
        flip = MagicMock()
        handler = ServerEventHandler(model_id="123e4567-e89b-12d3-a456-426614174000", flip=flip)

        persist_cleanup = self._end_run(handler, all_tasks_failed=True, flip=flip)

        persist_cleanup.execute.assert_called_once()

    def test_end_run_reports_results_uploaded_on_partial_evaluation_failure(self):
        """Some clients succeeded: the successful results still count as a completed run."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        self._end_run(handler, all_tasks_failed=False, flip=flip)

        assert handler.final_status == ModelStatus.RESULTS_UPLOADED
        flip.update_status.assert_called_with(model_id, ModelStatus.RESULTS_UPLOADED)

    def test_end_run_keeps_stopped_when_a_user_abort_failed_every_task(self):
        """A user-requested stop aborts every in-flight validate task; that is STOPPED, not ERROR."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.final_status = ModelStatus.STOPPED

        self._end_run(handler, all_tasks_failed=True, flip=flip)

        assert handler.final_status == ModelStatus.STOPPED
        flip.update_status.assert_called_with(model_id, ModelStatus.STOPPED)

    def test_end_run_reports_error_when_a_fatal_error_accompanies_a_failed_evaluation(self):
        """A fatal system error stays the most salient terminal state."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.fatal_error = True

        self._end_run(handler, all_tasks_failed=True, flip=flip)

        assert handler.final_status == ModelStatus.ERROR

    def test_end_run_reports_upload_failed_when_upload_breaks_after_a_clean_evaluation(self):
        """Regression guard: the new all-failed branch must not shadow RESULTS_UPLOAD_FAILED."""
        flip = MagicMock()
        handler = ServerEventHandler(model_id="123e4567-e89b-12d3-a456-426614174000", flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=EvaluationJsonGenerator)
        json_generator.all_tasks_failed.return_value = False
        json_generator.hub_metrics.return_value = {}
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = ResultsUploadError("boom")
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.RESULTS_UPLOAD_FAILED


class TestEvaluationMetricsForwarding:
    """FLIP#1526: an evaluation's returned metrics reach the hub from the server, at END_RUN."""

    MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"

    def _end_run(self, json_generator, flip):
        handler = ServerEventHandler(model_id=self.MODEL_ID, flip=flip)
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )
        handler.handle_event(EventType.END_RUN, fl_ctx)
        return handler

    @staticmethod
    def _evaluation_generator(hub_metrics):
        generator = Mock(spec=EvaluationJsonGenerator)
        generator.all_tasks_failed.return_value = False
        generator.hub_metrics.return_value = hub_metrics
        return generator

    def test_each_clients_metrics_are_sent_at_round_zero(self):
        flip = MagicMock()
        generator = self._evaluation_generator({"Trust_1": {"MEAN_DICE": 0.9}, "Trust_2": {"MEAN_DICE": 0.8}})

        self._end_run(generator, flip)

        assert flip.send_metrics.call_args_list == [
            call(client_name="Trust_1", model_id=self.MODEL_ID, label="MEAN_DICE", value=0.9, global_round=0),
            call(client_name="Trust_2", model_id=self.MODEL_ID, label="MEAN_DICE", value=0.8, global_round=0),
        ]

    def test_metrics_are_sent_before_the_terminal_status(self):
        flip = MagicMock()
        generator = self._evaluation_generator({"Trust_1": {"MEAN_DICE": 0.9}})

        self._end_run(generator, flip)

        names = [c[0] for c in flip.method_calls]
        assert names.index("send_metrics") < names.index("update_status")

    def test_results_are_uploaded_before_metrics_are_sent(self):
        """A slow hub must not hold up the results upload."""
        order = []
        flip = MagicMock()
        flip.send_metrics.side_effect = lambda **_: order.append("send_metrics")
        generator = self._evaluation_generator({"Trust_1": {"MEAN_DICE": 0.9}})
        handler = ServerEventHandler(model_id=self.MODEL_ID, flip=flip)
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = lambda _: order.append("upload")
        fl_ctx.get_engine.return_value.get_component.side_effect = lambda comp_id: (
            generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert order == ["upload", "send_metrics"]

    def test_a_failing_metric_does_not_stop_the_others_or_end_run(self):
        flip = MagicMock()
        flip.send_metrics.side_effect = [ValueError("schema"), None]
        generator = self._evaluation_generator({"Trust_1": {"A": 1.0, "B": 2.0}})

        handler = self._end_run(generator, flip)

        assert flip.send_metrics.call_count == 2
        assert handler.final_status == ModelStatus.RESULTS_UPLOADED

    def test_unreadable_metrics_do_not_stop_end_run(self):
        flip = MagicMock()
        generator = self._evaluation_generator({})
        generator.hub_metrics.side_effect = TypeError("bad reply")

        handler = self._end_run(generator, flip)

        flip.send_metrics.assert_not_called()
        assert handler.final_status == ModelStatus.RESULTS_UPLOADED

    def test_training_jobs_forward_nothing(self):
        """The training validate phase streams its metrics from the clients; nothing extra here."""
        flip = MagicMock()

        self._end_run(Mock(spec=ValidationJsonGenerator), flip)

        flip.send_metrics.assert_not_called()


class TestRoundEventRelay:
    """ROUND_STARTED / ROUND_DONE are relayed to the hub as typed facts (1-based rounds)."""

    MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"

    def _handler_and_ctx(self, props):
        flip = MagicMock()
        handler = ServerEventHandler(model_id=self.MODEL_ID, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine
        engine.get_clients.return_value = [Mock(), Mock(), Mock()]
        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )
        fl_ctx.get_prop.side_effect = lambda key, default=None: props.get(key, default)
        return handler, flip, fl_ctx

    def test_round_started_relays_one_based_round_and_total(self):
        from nvflare.app_common.app_constant import AppConstants

        # FLARE's _current_round is 0-based: prop 2 means round 3 of 5.
        handler, flip, fl_ctx = self._handler_and_ctx({AppConstants.CURRENT_ROUND: 2, AppConstants.NUM_ROUNDS: 5})

        handler.handle_event(AppEventType.ROUND_STARTED, fl_ctx)

        flip.send_event.assert_called_once_with(
            model_id=self.MODEL_ID,
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=3,
            details={"total_rounds": 5},
        )

    def test_round_started_seeds_the_counts_so_an_all_fail_round_reports_zero(self):
        """A round with zero accepted results must not report round N-1's counts on its
        ROUND_DONE — the sticky props are re-seeded to "0 of m" at every round start, so
        the all-clients-failed round closes with an honest count, not a bare line."""
        from nvflare.app_common.app_constant import AppConstants

        handler, flip, fl_ctx = self._handler_and_ctx({AppConstants.CURRENT_ROUND: 2, AppConstants.NUM_ROUNDS: 5})

        handler.handle_event(AppEventType.ROUND_STARTED, fl_ctx)

        seeded = {call.args[0]: call.args[1] for call in fl_ctx.set_prop.call_args_list}
        assert seeded[FlipProps.ROUND_RETURNED] == 0
        assert seeded[FlipProps.ROUND_EXPECTED] == 3  # the harness engine's client count

    def test_round_started_with_unreadable_engine_degrades_to_uncounted(self):
        """No client roster to seed from → both counts clear to None and the round
        degrades to the bare aggregated wording rather than claiming a denominator."""
        from nvflare.app_common.app_constant import AppConstants

        handler, flip, fl_ctx = self._handler_and_ctx({AppConstants.CURRENT_ROUND: 2})
        fl_ctx.get_engine.return_value.get_clients.side_effect = RuntimeError("engine gone")

        handler.handle_event(AppEventType.ROUND_STARTED, fl_ctx)

        seeded = {call.args[0]: call.args[1] for call in fl_ctx.set_prop.call_args_list}
        assert seeded[FlipProps.ROUND_RETURNED] is None
        assert seeded[FlipProps.ROUND_EXPECTED] is None

    def test_round_started_without_total_omits_it(self):
        from nvflare.app_common.app_constant import AppConstants

        handler, flip, fl_ctx = self._handler_and_ctx({AppConstants.CURRENT_ROUND: 0})

        handler.handle_event(AppEventType.ROUND_STARTED, fl_ctx)

        flip.send_event.assert_called_once_with(
            model_id=self.MODEL_ID,
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=1,
            details=None,
        )

    def test_round_done_relays_aggregated_with_counts(self):
        from nvflare.app_common.app_constant import AppConstants

        handler, flip, fl_ctx = self._handler_and_ctx(
            {
                AppConstants.CURRENT_ROUND: 2,
                FlipProps.ROUND_RETURNED: 4,
                FlipProps.ROUND_EXPECTED: 5,
            }
        )

        handler.handle_event(AppEventType.ROUND_DONE, fl_ctx)

        flip.send_event.assert_called_once_with(
            model_id=self.MODEL_ID,
            event_type=FLLogEvent.ROUND_AGGREGATED,
            global_round=3,
            details={"returned": 4, "expected": 5},
        )

    def test_round_done_without_counts_omits_details(self):
        from nvflare.app_common.app_constant import AppConstants

        handler, flip, fl_ctx = self._handler_and_ctx({AppConstants.CURRENT_ROUND: 0})

        handler.handle_event(AppEventType.ROUND_DONE, fl_ctx)

        flip.send_event.assert_called_once_with(
            model_id=self.MODEL_ID,
            event_type=FLLogEvent.ROUND_AGGREGATED,
            global_round=1,
            details=None,
        )

    def test_no_round_prop_means_no_event(self):
        """A ROUND_STARTED without CURRENT_ROUND on the context relays nothing."""
        handler, flip, fl_ctx = self._handler_and_ctx({})

        handler.handle_event(AppEventType.ROUND_STARTED, fl_ctx)
        handler.handle_event(AppEventType.ROUND_DONE, fl_ctx)

        flip.send_event.assert_not_called()
