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

from unittest.mock import MagicMock, Mock, patch

import pytest
from nvflare.apis.shareable import Shareable
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor

from flip.components.persist_and_cleanup import PersistToS3AndCleanup
from flip.constants import PTConstants


class TestPersistToS3AndCleanup:
    """Tests for PersistToS3AndCleanup component"""

    def test_init_with_valid_model_id(self):
        """Test initialization with valid model UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        assert component.model_id == model_id
        assert component.persistor_id == "persistor"
        assert component.flip == flip
        assert component.model_persistor is None
        assert component.model_inventory == {}

    def test_init_with_invalid_model_id_raises_error(self):
        """Test initialization with invalid model UUID raises ValueError"""
        flip = MagicMock()
        with pytest.raises(ValueError, match="is not a valid UUID"):
            PersistToS3AndCleanup(model_id="invalid-uuid", flip=flip)

    def test_init_with_custom_persistor_id(self):
        """Test initialization with custom persistor_id"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, persistor_id="custom_persistor", flip=flip)

        assert component.persistor_id == "custom_persistor"

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    def test_execute_no_engine(self, mock_constants):
        """Test execute when engine is not found"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        component.execute(fl_ctx)

        component.system_panic.assert_called_once()
        assert "Engine not found" in str(component.system_panic.call_args)

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    def test_execute_invalid_persistor_component(self, mock_constants):
        """Test execute when persistor component is not PTFileModelPersistor"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Return wrong type for persistor
        engine.get_component.return_value = MagicMock()

        component.execute(fl_ctx)

        component.system_panic.assert_called_once()
        assert "must be PTFileModelPersistor" in str(component.system_panic.call_args)

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    def test_execute_success_with_model_inventory(self, mock_constants):
        """Test successful execute with model inventory"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        workspace.get_run_dir.return_value = "/workspace/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job-123"

        # Mock PTFileModelPersistor
        persistor = Mock(spec=PTFileModelPersistor)
        model_location = MagicMock()
        model_location.location = "/path/to/model"
        persistor.get_model_inventory.return_value = {PTConstants.PTFileModelName: model_location}
        engine.get_component.return_value = persistor

        with patch.object(component, "upload_results_to_s3_bucket"):
            with patch.object(component, "cleanup"):
                with patch.object(component, "fire_event"):
                    component.execute(fl_ctx)

        assert component.model_dir == "/path/to/model"

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    def test_execute_without_model_inventory(self, mock_constants):
        """Test execute when model inventory doesn't have model"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_warning = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        workspace.get_run_dir.return_value = "/workspace/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job-123"

        persistor = Mock(spec=PTFileModelPersistor)
        persistor.get_model_inventory.return_value = {}
        engine.get_component.return_value = persistor

        with patch.object(component, "upload_results_to_s3_bucket"):
            with patch.object(component, "cleanup"):
                with patch.object(component, "fire_event"):
                    component.execute(fl_ctx)

        component.log_warning.assert_called_once()
        assert "Unable to retrieve" in str(component.log_warning.call_args)

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.make_archive")
    @patch("shutil.move")
    @patch("shutil.rmtree")
    @patch("os.path.isfile")
    @patch("os.path.isdir")
    def test_upload_results_to_s3_bucket_dev_mode(
        self, mock_isdir, mock_isfile, mock_rmtree, mock_move, mock_make_archive, mock_constants
    ):
        """Test upload_results_to_s3_bucket in dev mode"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        workspace.get_run_dir.return_value = "/workspace/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job-123"

        mock_isfile.return_value = True
        mock_isdir.return_value = True

        component.upload_results_to_s3_bucket(fl_ctx)

        # In dev mode, should not upload to S3
        mock_make_archive.assert_called_once()
        mock_move.assert_called_once()

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("boto3.client")
    @patch("shutil.make_archive")
    @patch("shutil.move")
    @patch("shutil.rmtree")
    @patch("os.path.isfile")
    @patch("os.path.isdir")
    def test_upload_results_to_s3_bucket_production_mode(
        self, mock_isdir, mock_isfile, mock_rmtree, mock_move, mock_make_archive, mock_boto3, mock_constants
    ):
        """Test upload_results_to_s3_bucket in production mode"""
        mock_constants.LOCAL_DEV = False
        mock_constants.UPLOADED_FEDERATED_DATA_BUCKET = "s3://my-bucket/path"
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        workspace.get_run_dir.return_value = "/workspace/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job-123"

        mock_isfile.return_value = True
        mock_isdir.return_value = True

        s3_client = MagicMock()
        mock_boto3.return_value = s3_client

        component.upload_results_to_s3_bucket(fl_ctx)

        # In production mode, should upload to S3
        s3_client.upload_file.assert_called_once()

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("os.path.isfile")
    @patch("os.path.isdir")
    def test_upload_results_to_s3_bucket_file_not_found(self, mock_isdir, mock_isfile, mock_constants):
        """Test upload_results_to_s3_bucket when file not found"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        workspace.get_run_dir.return_value = "/workspace/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job-123"

        mock_isfile.return_value = False
        mock_isdir.return_value = False

        with patch("shutil.make_archive", side_effect=FileNotFoundError("test")):
            with pytest.raises(Exception):
                component.upload_results_to_s3_bucket(fl_ctx)

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.rmtree")
    @patch("os.path.isdir")
    def test_cleanup_dev_mode(self, mock_isdir, mock_rmtree, mock_constants):
        """Test cleanup in dev mode"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine

        mock_isdir.return_value = True

        component.cleanup(fl_ctx)

        # In dev mode, should not delete directories
        mock_rmtree.assert_not_called()

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.rmtree")
    @patch("os.path.isdir")
    def test_cleanup_production_mode(self, mock_isdir, mock_rmtree, mock_constants):
        """Test cleanup in production mode"""
        mock_constants.LOCAL_DEV = False
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine

        mock_isdir.return_value = True

        component.cleanup(fl_ctx)

        # In production mode, should delete directories
        assert mock_rmtree.call_count == 2  # transfer and save dirs

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.rmtree")
    @patch("os.path.isdir")
    def test_cleanup_exception_handling(self, mock_isdir, mock_rmtree, mock_constants):
        """Test cleanup exception handling"""
        mock_constants.LOCAL_DEV = False
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine

        mock_isdir.return_value = True
        mock_rmtree.side_effect = Exception("Test exception")

        with pytest.raises(Exception):
            component.cleanup(fl_ctx)

        component.log_error.assert_called()

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    def test_execute_base_exception_handling(self, mock_constants):
        """Test execute with BaseException"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_info = MagicMock()
        component.log_exception = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Mock persistor as invalid to trigger panic
        persistor = "invalid_persistor"
        engine.get_component.return_value = persistor

        shareable = Shareable()

        with pytest.raises(Exception):
            component.execute("task", shareable, fl_ctx, MagicMock())

    @patch("flip.components.persist_and_cleanup.FlipConstants")
    @patch("flip.components.persist_and_cleanup.os.path.exists")
    @patch("builtins.open")
    def test_upload_results_with_general_exception(self, mock_open, mock_exists, mock_constants):
        """Test upload_results_to_s3_bucket with general exception"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_info = MagicMock()
        component.log_error = MagicMock()
        component.cleanup = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        workspace = MagicMock()
        workspace.get_root_dir.return_value = "/workspace"
        workspace.get_run_dir.return_value = "/workspace/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job-123"

        mock_exists.return_value = True

        with patch("shutil.make_archive", side_effect=Exception("Upload failed")):
            with pytest.raises(Exception):
                component.upload_results_to_s3_bucket(fl_ctx)

        component.cleanup.assert_called_once()
