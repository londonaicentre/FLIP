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

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from nvflare.apis.dxo import DataKind

from flip.nvflare.components.pt_model_locator import EvaluationModelLocator


def _fl_ctx_for(tmpdir: str) -> MagicMock:
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    workspace = MagicMock()
    workspace.get_app_dir.return_value = tmpdir
    fl_ctx.get_engine.return_value.get_workspace.return_value = workspace
    fl_ctx.get_job_id.return_value = "job-123"
    return fl_ctx


def _write_config(tmpdir: str, config: dict) -> str:
    custom_dir = os.path.join(tmpdir, "custom")
    os.makedirs(custom_dir, exist_ok=True)
    with open(os.path.join(custom_dir, "config.json"), "w") as f:
        json.dump(config, f)
    return custom_dir


class TestEvaluationModelLocator:
    def test_init(self):
        locator = EvaluationModelLocator()
        assert locator.models is None

    @patch("flip.nvflare.components.pt_model_locator.torch")
    def test_get_model_names_reads_config(self, mock_torch):
        config = {"models": {"monai_spleen_unet": {"checkpoint": "model.pt", "path": "unet"}}}
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"state_dict": "data"}

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = _write_config(tmpdir, config)
            # checkpoint must exist for it to be loaded
            open(os.path.join(custom_dir, "model.pt"), "w").close()

            locator = EvaluationModelLocator()
            names = locator.get_model_names(_fl_ctx_for(tmpdir))

        assert names == ["monai_spleen_unet"]

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    def test_locate_model_returns_weights_dxo(self, mock_to_dxo, mock_persistence_cls, mock_torch):
        config = {"models": {"monai_spleen_unet": {"checkpoint": "model.pt", "path": "unet"}}}
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"layer.weight": "data"}
        mock_persistence_cls.return_value.to_model_learnable.return_value = MagicMock()
        weights_dxo = MagicMock()
        weights_dxo.data_kind = DataKind.WEIGHTS
        mock_to_dxo.return_value = weights_dxo

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = _write_config(tmpdir, config)
            open(os.path.join(custom_dir, "model.pt"), "w").close()

            locator = EvaluationModelLocator()
            fl_ctx = _fl_ctx_for(tmpdir)
            result = locator.locate_model("monai_spleen_unet", fl_ctx)

        # A single WEIGHTS DXO (not a COLLECTION) is what CrossSiteModelEval broadcasts as one FLModel.
        assert result is weights_dxo
        assert result.data_kind == DataKind.WEIGHTS

    @patch("flip.nvflare.components.pt_model_locator.torch")
    def test_locate_unknown_model_returns_none(self, mock_torch):
        config = {"models": {"monai_spleen_unet": {"checkpoint": "model.pt", "path": "unet"}}}
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"state_dict": "data"}

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = _write_config(tmpdir, config)
            open(os.path.join(custom_dir, "model.pt"), "w").close()

            locator = EvaluationModelLocator()
            locator.log_error = MagicMock()
            result = locator.locate_model("does_not_exist", _fl_ctx_for(tmpdir))

        assert result is None
        locator.log_error.assert_called_once()

    def test_missing_models_key_logs_error(self):
        config = {"no_models": True}
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_config(tmpdir, config)
            locator = EvaluationModelLocator()
            locator.log_error = MagicMock()
            names = locator.get_model_names(_fl_ctx_for(tmpdir))

        assert names == []
        locator.log_error.assert_called_once()
        assert "models" in str(locator.log_error.call_args)

    @patch("flip.nvflare.components.pt_model_locator.torch")
    def test_missing_checkpoint_is_skipped(self, mock_torch):
        config = {"models": {"m": {"checkpoint": "missing.pt", "path": "unet"}}}
        mock_torch.cuda.is_available.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_config(tmpdir, config)  # no missing.pt on disk
            locator = EvaluationModelLocator()
            locator.log_error = MagicMock()
            names = locator.get_model_names(_fl_ctx_for(tmpdir))

        assert names == []
        locator.log_error.assert_called()
        assert "not found" in str(locator.log_error.call_args_list[0])
