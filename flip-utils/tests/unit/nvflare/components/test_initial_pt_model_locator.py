# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, patch

from flip.constants import PTConstants
from flip.nvflare.components.pt_model_locator import InitialPTModelLocator


class TestInitialPTModelLocator:
    @patch("builtins.__import__")
    def test_init_with_no_model(self, mock_import):
        mock_models_module = MagicMock()
        mock_model = MagicMock()
        mock_models_module.get_model.return_value = mock_model

        def import_side_effect(name, *args, **kwargs):
            if name == "models":
                return mock_models_module
            return __import__(name, *args, **kwargs)

        mock_import.side_effect = import_side_effect

        locator = InitialPTModelLocator()
        assert locator.model == mock_model
        assert locator.exclude_vars is None

    def test_init_with_model(self):
        mock_model = MagicMock()
        locator = InitialPTModelLocator(model=mock_model)
        assert locator.model == mock_model

    def test_get_model_names(self):
        locator = InitialPTModelLocator(model=MagicMock())
        fl_ctx = MagicMock()
        names = locator.get_model_names(fl_ctx)
        assert names == [PTConstants.PTServerName]

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("os.path.exists")
    def test_locate_model_success(self, mock_exists, mock_persistence_cls, mock_torch):
        mock_model = MagicMock()
        locator = InitialPTModelLocator(model=mock_model)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/dir"
        fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
        fl_ctx.get_job_id.return_value = "job-123"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"weights": "data"}

        mock_ml = MagicMock()
        mock_persistence_cls.return_value.to_model_learnable.return_value = mock_ml

        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        assert result == mock_ml
        mock_torch.load.assert_called_once()

    @patch("os.path.exists")
    def test_locate_model_not_found_tries_safehouse(self, mock_exists):
        locator = InitialPTModelLocator(model=MagicMock())
        locator.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/dir"
        fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
        fl_ctx.get_job_id.return_value = "job-123"

        # Both primary and safehouse paths don't exist
        mock_exists.return_value = False

        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        assert result is None
        # Should have checked both primary and safehouse paths
        assert mock_exists.call_count == 2

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("os.path.exists")
    def test_locate_model_runtime_error_returns_none(self, mock_exists, mock_persistence_cls, mock_torch):
        locator = InitialPTModelLocator(model=MagicMock())
        locator.log_info = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/dir"
        fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
        fl_ctx.get_job_id.return_value = "job-123"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"weights": "data"}

        mock_persistence_cls.return_value.to_model_learnable.side_effect = RuntimeError("Bad weights")

        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        assert result is None

    def test_locate_model_invalid_name(self):
        locator = InitialPTModelLocator(model=MagicMock())
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        result = locator.locate_model("bad_name", fl_ctx)

        assert result is None
        locator.log_error.assert_called_once()

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("os.path.exists")
    def test_locate_model_exception_during_load(self, mock_exists, mock_torch):
        locator = InitialPTModelLocator(model=MagicMock())
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/dir"
        fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
        fl_ctx.get_job_id.return_value = "job-123"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.side_effect = Exception("Load failed")

        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        assert result is None
        locator.log_error.assert_called_once()

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("os.path.exists")
    def test_locate_model_uses_weights_only(self, mock_exists, mock_persistence_cls, mock_torch):
        locator = InitialPTModelLocator(model=MagicMock())

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/dir"
        fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
        fl_ctx.get_job_id.return_value = "job-123"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"weights": "data"}
        mock_persistence_cls.return_value.to_model_learnable.return_value = MagicMock()

        locator.locate_model(PTConstants.PTServerName, fl_ctx)

        call_kwargs = mock_torch.load.call_args
        assert call_kwargs[1].get("weights_only") is True
