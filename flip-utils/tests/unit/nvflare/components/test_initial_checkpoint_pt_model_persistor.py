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

from flip.nvflare.components.pt_model_persistor import InitialCheckpointPTModelPersistor


def _fl_ctx(app_dir: str):
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    ws = MagicMock()
    ws.get_app_dir.return_value = app_dir
    fl_ctx.get_engine.return_value.get_workspace.return_value = ws
    fl_ctx.get_job_id.return_value = "job-123"
    return fl_ctx


def _persistor_with_module():
    """Build the persistor without invoking PTFileModelPersistor.__init__ (which needs a real
    model + torch runtime); we only exercise load_model's backbone-resolution/merge logic."""
    p = InitialCheckpointPTModelPersistor.__new__(InitialCheckpointPTModelPersistor)
    p._model_id_arg = "model-abc"
    p.model = MagicMock(spec=__import__("torch").nn.Module)
    p.model.load_state_dict.return_value = ([], [])  # (missing, unexpected)
    p.log_info = MagicMock()
    p.log_error = MagicMock()
    return p


def _write_config(app_dir: str, config: dict):
    custom = os.path.join(app_dir, "custom")
    os.makedirs(custom, exist_ok=True)
    with open(os.path.join(custom, "config.json"), "w") as f:
        json.dump(config, f)
    return custom


class TestInitialCheckpointPTModelPersistor:
    def test_loads_bundled_backbone_into_model(self):
        """SERVER_CHECKPOINT present in custom/ → backbone loaded (strict=False) into the model,
        then the stock load path captures self.model.state_dict()."""
        with tempfile.TemporaryDirectory() as tmp:
            custom = _write_config(tmp, {"SERVER_CHECKPOINT": "backbone.pt"})
            open(os.path.join(custom, "backbone.pt"), "wb").close()

            p = _persistor_with_module()
            fl_ctx = _fl_ctx(tmp)

            with (
                patch("flip.nvflare.components.pt_model_persistor.torch.load", return_value={"w": 1}) as mock_load,
                patch(
                    "nvflare.app_opt.pt.file_model_persistor.PTFileModelPersistor.load_model", return_value="LEARNABLE"
                ) as mock_super,
            ):
                result = p.load_model(fl_ctx)

            assert result == "LEARNABLE"
            mock_load.assert_called_once()
            assert mock_load.call_args[0][0] == os.path.join(custom, "backbone.pt")
            p.model.load_state_dict.assert_called_once_with({"w": 1}, strict=False)
            # source_ckpt must NOT be set — we merge into the model, not use NVFLARE's raw-ckpt path.
            mock_super.assert_called_once()

    @patch("flip.nvflare.components.pt_model_persistor.FlipConstants")
    def test_loads_backbone_from_shared_volume_in_prod(self, mock_constants):
        """custom/ absent + LOCAL_DEV false → resolve the shared-volume path and load it."""
        mock_constants.LOCAL_DEV = False
        mock_constants.SERVER_CHECKPOINT_ROOT = "/shared-checkpoints"
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"SERVER_CHECKPOINT": "backbone.pt"})  # no backbone.pt in custom/
            shared = os.path.join("/shared-checkpoints", "model-abc", "backbone.pt")

            p = _persistor_with_module()
            fl_ctx = _fl_ctx(tmp)

            with (
                patch("flip.nvflare.components.pt_model_persistor.os.path.isfile", side_effect=lambda x: x == shared),
                patch("flip.nvflare.components.pt_model_persistor.get_flip_model_id", return_value="model-abc"),
                patch("flip.nvflare.components.pt_model_persistor.torch.load", return_value={"w": 1}) as mock_load,
                patch("nvflare.app_opt.pt.file_model_persistor.PTFileModelPersistor.load_model", return_value="L"),
            ):
                p.load_model(fl_ctx)

            mock_load.assert_called_once()
            assert mock_load.call_args[0][0] == shared
            p.model.load_state_dict.assert_called_once_with({"w": 1}, strict=False)

    def test_no_server_checkpoint_is_stock_behaviour(self):
        """No SERVER_CHECKPOINT declared → no backbone load; delegates straight to stock persistor
        (safe for every other standard training job)."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"GLOBAL_ROUNDS": 3})  # no SERVER_CHECKPOINT

            p = _persistor_with_module()
            fl_ctx = _fl_ctx(tmp)

            with (
                patch("flip.nvflare.components.pt_model_persistor.torch.load") as mock_load,
                patch(
                    "nvflare.app_opt.pt.file_model_persistor.PTFileModelPersistor.load_model", return_value="STOCK"
                ) as mock_super,
            ):
                result = p.load_model(fl_ctx)

            assert result == "STOCK"
            mock_load.assert_not_called()
            p.model.load_state_dict.assert_not_called()
            mock_super.assert_called_once()

    @patch("flip.nvflare.components.pt_model_persistor.FlipConstants")
    def test_declared_but_missing_everywhere_logs_error_and_delegates(self, mock_constants):
        """Declared but found neither bundled nor on the shared volume (prod) → log error, no load,
        still delegate to stock (which will use the bare model weights)."""
        mock_constants.LOCAL_DEV = False
        mock_constants.SERVER_CHECKPOINT_ROOT = "/shared-checkpoints"
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"SERVER_CHECKPOINT": "backbone.pt"})

            p = _persistor_with_module()
            fl_ctx = _fl_ctx(tmp)

            with (
                patch("flip.nvflare.components.pt_model_persistor.os.path.isfile", return_value=False),
                patch("flip.nvflare.components.pt_model_persistor.get_flip_model_id", return_value="model-abc"),
                patch("flip.nvflare.components.pt_model_persistor.torch.load") as mock_load,
                patch("nvflare.app_opt.pt.file_model_persistor.PTFileModelPersistor.load_model", return_value="L"),
            ):
                p.load_model(fl_ctx)

            mock_load.assert_not_called()
            p.log_error.assert_called()
            assert "not found" in str(p.log_error.call_args)
