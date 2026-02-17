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

from unittest.mock import MagicMock, patch

from flip.constants import PTConstants
from flip.nvflare.components.pt_model_locator import PTModelLocator


class TestPTModelLocator:
    """Tests for PTModelLocator component"""

    @patch("builtins.__import__")
    def test_init_with_no_model(self, mock_import):
        """Test initialization without model parameter loads from models module"""
        mock_models_module = MagicMock()
        mock_model = MagicMock()
        mock_models_module.get_model.return_value = mock_model

        def import_side_effect(name, *args, **kwargs):
            if name == "models":
                return mock_models_module
            return __import__(name, *args, **kwargs)

        mock_import.side_effect = import_side_effect

        locator = PTModelLocator()
        assert locator.model == mock_model
        assert locator.exclude_vars is None

    def test_init_with_model(self):
        """Test initialization with model parameter"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        assert locator.model == mock_model
        assert locator.exclude_vars is None

    def test_init_with_exclude_vars(self):
        """Test initialization with exclude_vars parameter"""
        mock_model = MagicMock()
        exclude_vars = ["var1", "var2"]
        locator = PTModelLocator(model=mock_model, exclude_vars=exclude_vars)
        assert locator.model == mock_model
        assert locator.exclude_vars == exclude_vars

    def test_get_model_names(self):
        """Test get_model_names returns PTServerName"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        fl_ctx = MagicMock()

        names = locator.get_model_names(fl_ctx)
        assert names == [PTConstants.PTServerName]
        assert len(names) == 1

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants.LOCAL_DEV", True)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.exists")
    def test_locate_model_local_dev_success(self, mock_exists, mock_to_dxo, mock_persistence_manager_cls, mock_torch):
        """Test locate_model in local dev mode with existing model"""
        # Setup mocks
        mock_model = MagicMock()
        mock_model.__class__.__name__ = "TestModel"
        locator = PTModelLocator(model=mock_model)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"model": "data"}

        mock_persistence_manager = MagicMock()
        mock_ml = MagicMock()
        mock_persistence_manager.to_model_learnable.return_value = mock_ml
        mock_persistence_manager_cls.return_value = mock_persistence_manager

        mock_dxo = MagicMock()
        mock_to_dxo.return_value = mock_dxo

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result == mock_dxo
        mock_exists.assert_called_once()
        mock_torch.load.assert_called_once()
        mock_persistence_manager.to_model_learnable.assert_called_once_with(exclude_vars=None)
        mock_to_dxo.assert_called_once_with(mock_ml)

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants.LOCAL_DEV", False)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.exists")
    def test_locate_model_production_mode_success(
        self, mock_exists, mock_to_dxo, mock_persistence_manager_cls, mock_torch
    ):
        """Test locate_model in production mode with existing model"""
        # Setup mocks
        mock_model = MagicMock()
        mock_model.__class__.__name__ = "TestModel"
        locator = PTModelLocator(model=mock_model)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = True
        mock_torch.load.return_value = {"model": "data"}

        mock_persistence_manager = MagicMock()
        mock_ml = MagicMock()
        mock_persistence_manager.to_model_learnable.return_value = mock_ml
        mock_persistence_manager_cls.return_value = mock_persistence_manager

        mock_dxo = MagicMock()
        mock_to_dxo.return_value = mock_dxo

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result == mock_dxo
        mock_exists.assert_called_once()
        assert "/test/run/dir/model" in mock_exists.call_args[0][0]
        mock_torch.load.assert_called_once()

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants.LOCAL_DEV", True)
    @patch("os.path.exists")
    def test_locate_model_file_not_found(self, mock_exists):
        """Test locate_model returns None when model file doesn't exist"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = False

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result is None
        locator.log_error.assert_called_once()
        assert "Model file not found" in str(locator.log_error.call_args)

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants.LOCAL_DEV", True)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("os.path.exists")
    def test_locate_model_exception_during_load(self, mock_exists, mock_torch):
        """Test locate_model returns None when exception occurs during model load"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.side_effect = Exception("Load error")

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result is None
        locator.log_error.assert_called_once()
        assert "Error in retrieving" in str(locator.log_error.call_args)

    def test_locate_model_invalid_name(self):
        """Test locate_model returns None for invalid model name"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        # Execute
        result = locator.locate_model("invalid_name", fl_ctx)

        # Verify
        assert result is None
        locator.log_error.assert_called_once()
        assert "doesn't recognize name" in str(locator.log_error.call_args)

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants.LOCAL_DEV", True)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.exists")
    @patch("builtins.__import__")
    def test_locate_model_without_model_instance(
        self, mock_import, mock_exists, mock_to_dxo, mock_persistence_manager_cls, mock_torch
    ):
        """Test locate_model when initialized without model instance"""
        mock_models_module = MagicMock()
        mock_model = MagicMock()
        mock_models_module.get_model.return_value = mock_model

        def import_side_effect(name, *args, **kwargs):
            if name == "models":
                return mock_models_module
            return __import__(name, *args, **kwargs)

        mock_import.side_effect = import_side_effect

        locator = PTModelLocator()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"model": "data"}

        mock_persistence_manager = MagicMock()
        mock_ml = MagicMock()
        mock_persistence_manager.to_model_learnable.return_value = mock_ml
        mock_persistence_manager_cls.return_value = mock_persistence_manager

        mock_dxo = MagicMock()
        mock_to_dxo.return_value = mock_dxo

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result == mock_dxo
