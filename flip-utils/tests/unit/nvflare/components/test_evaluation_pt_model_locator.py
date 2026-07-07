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
        assert locator.model_id == ""

    def test_init_with_model_id(self):
        locator = EvaluationPTModelLocator(model_id="model-abc")
        assert locator.model_id == "model-abc"

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

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.isfile")
    def test_probe_validates_unwrapped_persistence_format_weights(
        self, mock_isfile, mock_to_dxo, mock_persistence_cls, mock_torch
    ):
        """The strict-load probe must validate the same (manager-normalised) weights
        that are sent to clients — i.e. the persistence manager's ``var_dict`` — not
        the raw checkpoint. Otherwise an NVFLARE persistence-format checkpoint
        (``{"model": ..., "train_conf": ...}``) false-fails strict loading and logs a
        spurious error, even though the weights are delivered correctly."""
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
            mock_torch.cuda.is_available.return_value = False
            # Raw checkpoint in NVFLARE persistence format: weights nested under "model".
            mock_torch.load.return_value = {"model": {"layer.weight": "W"}, "train_conf": {"x": 1}}

            # The persistence manager normalises that to the unwrapped weights.
            unwrapped = {"layer.weight": "W"}
            mock_persistence_cls.return_value.var_dict = unwrapped
            mock_persistence_cls.return_value.to_model_learnable.return_value = MagicMock()
            mock_to_dxo.return_value = MagicMock()

            mock_net = MagicMock()
            mock_model_paths = {"ResNet": mock_net}

            locator = EvaluationPTModelLocator()
            locator.log_error = MagicMock()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            with patch.dict("sys.modules", {"models": MagicMock(model_paths=mock_model_paths)}):
                locator.locate_model(fl_ctx)

            # The probe must validate the UNWRAPPED weights, not the raw {"model": ...} dict.
            mock_net.load_state_dict.assert_called_once_with(unwrapped, strict=True)
            # And it must NOT log the spurious "could not be loaded" error.
            assert all(
                "could not be loaded" not in str(call) for call in locator.log_error.call_args_list
            )

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants")
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.isfile")
    def test_reads_from_shared_volume_when_not_bundled(
        self, mock_isfile, mock_to_dxo, mock_persistence_cls, mock_torch, mock_constants
    ):
        """When the checkpoint is not bundled in custom/ and LOCAL_DEV is false, the locator
        loads it from <SERVER_CHECKPOINT_ROOT>/<model_id>/<checkpoint> on the shared volume."""
        mock_constants.LOCAL_DEV = False
        mock_constants.SERVER_CHECKPOINT_ROOT = "/shared-checkpoints"

        config = {"models": {"resnet": {"checkpoint": "resnet.pth", "path": "ResNet"}}}
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom")
            os.makedirs(custom_dir)
            with open(os.path.join(custom_dir, "config.json"), "w") as f:
                json.dump(config, f)

            shared_path = os.path.join("/shared-checkpoints", "model-abc", "resnet.pth")
            # Not bundled in custom/, but present on the shared volume.
            mock_isfile.side_effect = lambda p: p == shared_path

            mock_torch.cuda.is_available.return_value = False
            mock_torch.load.return_value = {"layer.weight": "W"}
            mock_persistence_cls.return_value.var_dict = {"layer.weight": "W"}
            mock_persistence_cls.return_value.to_model_learnable.return_value = MagicMock()
            mock_to_dxo.return_value = MagicMock()

            locator = EvaluationPTModelLocator(model_id="model-abc")
            locator.log_error = MagicMock()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            with patch.dict("sys.modules", {"models": MagicMock(model_paths={"ResNet": MagicMock()})}):
                result = locator.locate_model(fl_ctx)

            # Loaded from the shared-volume path, not the (absent) bundled custom/ path.
            mock_torch.load.assert_called_once()
            assert mock_torch.load.call_args[0][0] == shared_path
            assert result.data_kind == DataKind.COLLECTION
            assert set(result.data.keys()) == {"resnet"}
            assert all("not found" not in str(c) for c in locator.log_error.call_args_list)

    @patch("flip.nvflare.components.pt_model_locator.FlipConstants")
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("os.path.isfile")
    def test_missing_everywhere_in_prod_logs_error_and_skips(self, mock_isfile, mock_torch, mock_constants):
        """When the checkpoint is neither bundled nor on the shared volume (LOCAL_DEV false),
        the locator logs an error naming both paths and skips the model (no torch.load)."""
        mock_constants.LOCAL_DEV = False
        mock_constants.SERVER_CHECKPOINT_ROOT = "/shared-checkpoints"

        config = {"models": {"resnet": {"checkpoint": "resnet.pth", "path": "ResNet"}}}
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom")
            os.makedirs(custom_dir)
            with open(os.path.join(custom_dir, "config.json"), "w") as f:
                json.dump(config, f)

            mock_isfile.return_value = False  # nowhere
            mock_torch.cuda.is_available.return_value = False

            locator = EvaluationPTModelLocator(model_id="model-abc")
            locator.log_error = MagicMock()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            with patch.dict("sys.modules", {"models": MagicMock(model_paths={"ResNet": MagicMock()})}):
                result = locator.locate_model(fl_ctx)

            locator.log_error.assert_called()
            msg = str(locator.log_error.call_args_list[0])
            assert "not found" in msg
            assert "shared-volume path" in msg
            mock_torch.load.assert_not_called()
            # Model skipped -> empty collection.
            assert result.data_kind == DataKind.COLLECTION
            assert result.data == {}

    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.isfile")
    def test_multimodel_collection_has_all_models(
        self, mock_isfile, mock_to_dxo, mock_persistence_cls, mock_torch
    ):
        """Two bundled checkpoints -> one COLLECTION DXO keyed by both model names."""
        config = {
            "models": {
                "pretrained": {"checkpoint": "pre.pth", "path": "A"},
                "finetuned": {"checkpoint": "fine.pth", "path": "B"},
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_dir = os.path.join(tmpdir, "custom")
            os.makedirs(custom_dir)
            with open(os.path.join(custom_dir, "config.json"), "w") as f:
                json.dump(config, f)

            mock_isfile.return_value = True  # both bundled in custom/
            mock_torch.cuda.is_available.return_value = False
            mock_torch.load.return_value = {"layer.weight": "W"}
            mock_persistence_cls.return_value.var_dict = {"layer.weight": "W"}
            mock_persistence_cls.return_value.to_model_learnable.return_value = MagicMock()
            mock_to_dxo.return_value = MagicMock()

            locator = EvaluationPTModelLocator()

            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            mock_workspace = MagicMock()
            mock_workspace.get_app_dir.return_value = tmpdir
            fl_ctx.get_engine.return_value.get_workspace.return_value = mock_workspace
            fl_ctx.get_job_id.return_value = "job-123"

            with patch.dict("sys.modules", {"models": MagicMock(model_paths={"A": MagicMock(), "B": MagicMock()})}):
                result = locator.locate_model(fl_ctx)

            assert result.data_kind == DataKind.COLLECTION
            assert set(result.data.keys()) == {"pretrained", "finetuned"}
