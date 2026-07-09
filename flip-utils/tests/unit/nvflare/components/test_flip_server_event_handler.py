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

from unittest.mock import MagicMock, Mock, patch

import pytest
from nvflare.apis.event_type import EventType
from nvflare.app_common.app_event_type import AppEventType

from flip.constants import FlipEvents, ModelStatus
from flip.exceptions import ResultsUploadError
from flip.nvflare.components.evaluation_json_generator import EvaluationJsonGenerator
from flip.nvflare.components.flip_server_event_handler import ServerEventHandler
from flip.nvflare.components.persist_and_cleanup import PersistToS3AndCleanup
from flip.nvflare.components.validation_json_generator import ValidationJsonGenerator


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

    def test_handle_event_initial_model_loaded(self):
        """Test handle_event with INITIAL_MODEL_LOADED event"""
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

        handler.handle_event(AppEventType.INITIAL_MODEL_LOADED, fl_ctx)

        flip.update_status.assert_called_with(model_id, ModelStatus.PREPARED)

    def test_handle_event_training_started(self):
        """Test handle_event with TRAINING_STARTED event"""
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

        handler.handle_event(AppEventType.TRAINING_STARTED, fl_ctx)

        flip.update_status.assert_called_with(model_id, ModelStatus.TRAINING_STARTED)

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

    def test_handle_event_end_run_success(self):
        """Test handle_event with END_RUN event - success case"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.return_value = None
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        persist_cleanup.execute.assert_called_once_with(fl_ctx)
        assert handler.final_status == ModelStatus.RESULTS_UPLOADED
        flip.update_status.assert_called_with(model_id, ModelStatus.RESULTS_UPLOADED)

    def test_handle_event_end_run_with_fatal_error(self):
        """Test handle_event with END_RUN event when fatal error occurred"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.fatal_error = True

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.ERROR
        flip.update_status.assert_called_with(model_id, ModelStatus.ERROR)

    def test_handle_event_end_run_with_stopped_status(self):
        """Test handle_event with END_RUN event when already stopped"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.final_status = ModelStatus.STOPPED

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.STOPPED
        flip.update_status.assert_called_with(model_id, ModelStatus.STOPPED)

    def test_handle_event_end_run_with_exception(self):
        """Test handle_event with END_RUN event when persist_and_cleanup raises exception"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = Exception("Persist failed")
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.ERROR
        flip.update_status.assert_called_with(model_id, ModelStatus.ERROR)

    def test_handle_event_end_run_with_results_upload_error(self):
        """Test handle_event with END_RUN when results upload specifically fails"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = ResultsUploadError("S3 upload failed")
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.RESULTS_UPLOAD_FAILED
        flip.update_status.assert_called_with(model_id, ModelStatus.RESULTS_UPLOAD_FAILED)

    def test_handle_event_end_run_results_upload_error_with_fatal_error(self):
        """A recorded fatal system error takes precedence over a trailing upload failure."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.fatal_error = True

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = ResultsUploadError("S3 upload failed")
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.ERROR
        flip.update_status.assert_called_with(model_id, ModelStatus.ERROR)

    def test_handle_event_end_run_results_upload_error_with_stopped_status(self):
        """A user-requested abort (STOPPED) is preserved over a trailing upload failure."""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        handler = ServerEventHandler(model_id=model_id, flip=flip)
        handler.final_status = ModelStatus.STOPPED

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        json_generator = Mock(spec=ValidationJsonGenerator)
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = ResultsUploadError("S3 upload failed")
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.STOPPED
        flip.update_status.assert_called_with(model_id, ModelStatus.STOPPED)

    def test_handle_event_training_finished(self):
        """Test handle_event with TRAINING_FINISHED event"""
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

        handler.handle_event(AppEventType.TRAINING_FINISHED, fl_ctx)

        # Should log info but not change status

    def test_handle_event_results_upload_completed(self):
        """Test handle_event with RESULTS_UPLOAD_COMPLETED event"""
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

        handler.handle_event(FlipEvents.RESULTS_UPLOAD_COMPLETED, fl_ctx)

        # Should log info but not change status

    def test_handle_event_start_run(self):
        """Test handle_event with START_RUN event"""
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

        handler.handle_event(EventType.START_RUN, fl_ctx)

        # Should log info but not change status

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

        # This should raise AttributeError since validation_json_generator is None
        with pytest.raises(AttributeError, match="'NoneType' object has no attribute"):
            handler.handle_event(FlipEvents.TRAINING_INITIATED, fl_ctx)

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
        handler.system_panic.assert_called_once()
        assert "must be PersistToS3AndCleanup" in str(handler.system_panic.call_args)

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
        persist_cleanup = Mock(spec=PersistToS3AndCleanup)
        persist_cleanup.execute.side_effect = ResultsUploadError("boom")
        engine.get_component.side_effect = lambda comp_id: (
            json_generator if comp_id == "json_generator" else persist_cleanup
        )

        handler.handle_event(EventType.END_RUN, fl_ctx)

        assert handler.final_status == ModelStatus.RESULTS_UPLOAD_FAILED
