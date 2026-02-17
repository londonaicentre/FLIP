# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from unittest.mock import MagicMock, Mock

import pytest
from nvflare.apis.event_type import EventType
from nvflare.app_common.app_event_type import AppEventType

from flip.constants import FlipEvents, ModelStatus
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

        assert handler.model_id == model_id
        assert handler.validation_json_generator_id == "json_generator"
        assert handler.persist_and_cleanup_id == "persist_and_cleanup"
        assert handler.flip == flip
        assert handler.fatal_error is False
        assert handler.final_status is None

    def test_init_with_invalid_model_id_raises_error(self):
        """Test initialization with invalid model UUID raises ValueError"""
        flip = MagicMock()
        with pytest.raises(ValueError, match="is not a valid UUID"):
            ServerEventHandler(model_id="invalid-uuid", flip=flip)

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
