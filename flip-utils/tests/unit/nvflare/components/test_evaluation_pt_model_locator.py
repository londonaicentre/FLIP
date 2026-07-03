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

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from nvflare.apis.dxo import DataKind

from flip.nvflare.components.pt_model_locator import EvaluationPTModelLocator


class TestEvaluationPTModelLocator:
    def test_init(self):
        locator = EvaluationPTModelLocator()
        assert locator.models is None
        assert locator.exclude_vars is None

    def test_init_with_exclude_vars(self):
        locator = EvaluationPTModelLocator(exclude_vars=["var1"])
        assert locator.exclude_vars == ["var1"]

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.isfile")
    def test_locate_model_success(self, mock_isfile, mock_to_dxo, mock_persistence_cls, mock_torch):
        config = {
            "models": {
                "resnet": {"checkpoint": "resnet.pth", "path": "ResNet"},
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom")
            os.makedirs(custom_dir)
            with open(os.path.join(custom_dir, "config.json"), "w") as f:
                json.dump(config, f)

            mock_isfile.return_value = True

            mock_net = MagicMock()
            mock_model_paths = {"ResNet": mock_net}

            mock_torch.cuda.is_available.return_value = False
            mock_torch.load.return_value = {"state_dict": "data"}

            mock_ml = MagicMock()
            mock_persistence_cls.return_value.to_model_learnable.return_value = mock_ml
            mock_dxo = MagicMock()
            mock_to_dxo.return_value = mock_dxo

            locator = EvaluationPTModelLocator()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            with patch.dict("sys.modules", {"models": MagicMock(model_paths=mock_model_paths)}):
                result = locator.locate_model(fl_ctx)

            assert result is not None
            assert result.data_kind == DataKind.COLLECTION

    def test_locate_model_missing_models_key_logs_error(self):
        config = {"no_models_key": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom")
            os.makedirs(custom_dir)
            with open(os.path.join(custom_dir, "config.json"), "w") as f:
                json.dump(config, f)

            locator = EvaluationPTModelLocator()
            locator.log_error = MagicMock()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            # The code unconditionally does `from models import model_paths` after config load
            with patch.dict("sys.modules", {"models": MagicMock(model_paths={})}):
                try:
                    locator.locate_model(fl_ctx)
                except (TypeError, AttributeError):
                    pass

            locator.log_error.assert_called_once()
            assert "models key-element" in str(locator.log_error.call_args)

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.isfile")
    def test_locate_model_checkpoint_not_found_logs_error(
        self, mock_isfile, mock_to_dxo, mock_persistence_cls, mock_torch
    ):
        config = {
            "models": {
                "resnet": {"checkpoint": "missing.pth", "path": "ResNet"},
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom")
            os.makedirs(custom_dir)
            with open(os.path.join(custom_dir, "config.json"), "w") as f:
                json.dump(config, f)

            mock_isfile.return_value = False
            mock_torch.cuda.is_available.return_value = False
            mock_torch.load.return_value = {"state_dict": "data"}

            mock_ml = MagicMock()
            mock_persistence_cls.return_value.to_model_learnable.return_value = mock_ml
            mock_to_dxo.return_value = MagicMock()

            locator = EvaluationPTModelLocator()
            locator.log_error = MagicMock()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            with patch.dict("sys.modules", {"models": MagicMock(model_paths={"ResNet": MagicMock()})}):
                locator.locate_model(fl_ctx)

            locator.log_error.assert_called()
            assert "not found" in str(locator.log_error.call_args_list[0])

    def test_caches_models_on_second_call(self):
        locator = EvaluationPTModelLocator()
        locator.models = {"resnet": MagicMock()}
        locator.model_names = ["resnet"]

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        with (
            patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager") as mock_p,
            patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo") as mock_d,
        ):
            mock_p.return_value.to_model_learnable.return_value = MagicMock()
            mock_d.return_value = MagicMock()

            result = locator.locate_model(fl_ctx)

        assert result is not None
        assert result.data_kind == DataKind.COLLECTION
