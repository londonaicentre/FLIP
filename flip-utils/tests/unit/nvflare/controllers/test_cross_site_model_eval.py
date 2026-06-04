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

import pytest
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.controllers.cross_site_model_eval import CrossSiteModelEval


class TestCrossSiteModelEval:
    def test_init_invalid_task_check_period(self):
        with pytest.raises(TypeError):
            CrossSiteModelEval(task_check_period="not-a-float", model_id="123e4567-e89b-12d3-a456-426614174000")

    def test_start_controller_no_engine(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        controller.system_panic = MagicMock()
        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()

    @patch("os.path.exists", return_value=True)
    @patch("shutil.rmtree")
    @patch("os.makedirs")
    def test_start_controller_creates_dirs_and_sets_props(self, mock_makedirs, mock_rmtree, mock_exists):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        client1 = MagicMock()
        client1.name = "c1"
        engine.get_clients.return_value = [client1]
        workspace = MagicMock()
        workspace.get_run_dir.return_value = "/run"
        engine.get_workspace.return_value = workspace
        fl_ctx.get_engine.return_value = engine
        fl_ctx.get_job_id.return_value = "job1"

        controller.fire_event = MagicMock()

        controller.start_controller(fl_ctx)

        # participating client set and event fired
        assert "c1" in controller._participating_clients
        controller.fire_event.assert_called()
        mock_rmtree.assert_called()
        mock_makedirs.assert_called()

    @patch("os.path.exists", return_value=True)
    @patch("shutil.rmtree")
    @patch("os.makedirs")
    def test_start_controller_bad_model_locator(self, mock_makedirs, mock_rmtree, mock_exists):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000", model_locator_id="mloc")
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine
        engine.get_clients.return_value = []
        # provide a non ModelLocator
        engine.get_workspace.return_value = MagicMock()
        engine.get_component.return_value = "not_a_locator"

        controller.system_panic = MagicMock()
        controller.fire_event = MagicMock()
        controller.start_controller(fl_ctx)

        controller.system_panic.assert_called_once()
        mock_rmtree.assert_called()
        mock_makedirs.assert_called()

    def test_control_flow_no_clients_timeout(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller._participating_clients = []
        controller._wait_for_clients_timeout = 0

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        engine.get_clients.return_value = []
        fl_ctx.get_engine.return_value = engine

        abort_signal = MagicMock()
        abort_signal.triggered = False

        # Should return quickly due to zero timeout
        controller.control_flow(abort_signal, fl_ctx)

    def test_control_flow_broadcast_submit_model_called(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller._participating_clients = ["c1", "c2"]
        controller._submit_model_task_name = "submit"

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = MagicMock()

        controller.broadcast = MagicMock()
        controller.broadcast_and_wait = MagicMock()

        abort_signal = MagicMock()
        abort_signal.triggered = False

        controller.control_flow(abort_signal, fl_ctx)

        controller.broadcast.assert_called_once()

    def test_accept_local_model_handles_error_return_code(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller.flip = MagicMock()
        controller.log_error = MagicMock()
        controller.fire_event = MagicMock()

        share = Shareable()
        share.set_header = MagicMock()
        share.get_return_code = MagicMock(return_value=ReturnCode.EXECUTION_EXCEPTION)
        share.get_header = MagicMock(return_value="boom")

        # should call send_handled_exception
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        controller._accept_local_model(client_name="c1", result=share, fl_ctx=fl_ctx)

        controller.flip.send_handled_exception.assert_called()

    def test_accept_local_model_ok_calls_save_and_send(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        controller._cross_val_models_dir = "/models"
        controller._send_validation_task = MagicMock()

        # patch from_shareable to return a DXO-like object
        with patch("flip.nvflare.controllers.cross_site_model_eval.from_shareable", return_value=MagicMock()):
            with patch.object(controller, "_save_validation_content", return_value="/models/c1"):
                share = Shareable()
                share.get_return_code = MagicMock(return_value=ReturnCode.OK)
                fl_ctx = MagicMock()
                fl_ctx.get_peer_context.return_value = None
                controller.fire_event = MagicMock()
                controller._accept_local_model(client_name="c1", result=share, fl_ctx=fl_ctx)

                assert controller._client_models.get("c1") == "/models/c1"
                controller._send_validation_task.assert_called()

    def test_before_send_validate_task_cb_model_not_found(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        client_task = MagicMock()
        client_task.task.props = {AppConstants.MODEL_OWNER: "m1"}

        # make loader raise ValueError
        with patch.object(controller, "_load_validation_content", side_effect=ValueError("nope")):
            controller.system_panic = MagicMock()
            controller.fire_event = MagicMock()
            controller.log_error = MagicMock()
            controller._before_send_validate_task_cb(client_task, fl_ctx)
            controller.system_panic.assert_called()

    def test_locate_server_models_invalid_dxo(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        controller._model_locator = MagicMock()
        controller._model_locator.get_model_names.return_value = ["m1"]
        controller._model_locator.locate_model.return_value = "not_a_dxo"

        controller.system_panic = MagicMock()
        controller.log_info = MagicMock()
        controller.fire_event = MagicMock()
        res = controller._locate_server_models(fl_ctx)
        assert res is False
        controller.system_panic.assert_called()

    def test_locate_server_models_success(self):
        controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        controller._model_locator = MagicMock()
        controller._model_locator.get_model_names.return_value = ["m1"]
        controller._model_locator.locate_model.return_value = MagicMock()

        with patch("flip.nvflare.controllers.cross_site_model_eval.DXO", MagicMock):
            with patch.object(controller, "_save_validation_content", return_value="/models/SRV_m1"):
                controller.log_info = MagicMock()
                controller.fire_event = MagicMock()
                res = controller._locate_server_models(fl_ctx)

        assert res is True
        assert "SRV_m1" in controller._server_models


@pytest.mark.parametrize(
    ("name", "data_kind", "data", "meta"),
    [
        (
            "metrics_sample",
            DataKind.METRICS,
            {"val_acc": 0.007118524517863989},
            {},
        )
    ],
)
def test_save_and_load_validation_content(tmp_path, name, data_kind, data, meta, monkeypatch):
    """Test saving and loading validation content with DXO."""
    from pathlib import Path

    # Arrange: controller instance with a valid UUID (required by __init__)
    controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")

    # ---- 🧩 Mock out logging to avoid FLContext internals ----
    monkeypatch.setattr(controller, "log_debug", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "log_info", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "log_error", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "log_exception", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "system_panic", lambda *a, **kw: None)
    # -----------------------------------------------------------

    fl_ctx = MagicMock()

    # Create a DXO to persist
    dxo = DXO(data_kind=data_kind, data=data, meta=meta)

    # Ensure directory exists
    save_dir = tmp_path / "cross_val_models"
    save_dir.mkdir(parents=True, exist_ok=True)

    # Act: save the DXO, then load it back
    saved_path = controller._save_validation_content(
        name=name,
        save_dir=str(save_dir),
        dxo=dxo,
        fl_ctx=fl_ctx,
    )

    # Assert: a file with this base path should exist (DXO manages its own extension(s))
    assert Path(saved_path).exists() or any(
        Path(str(saved_path) + ext).exists() for ext in (".npy", ".npz", ".json")
    ), f"Expected saved DXO file(s) for base path {saved_path}"

    loaded = controller._load_validation_content(
        name=name,
        load_dir=str(save_dir),
        fl_ctx=fl_ctx,
    )

    # Validate round-trip content
    assert isinstance(loaded, DXO)
    assert loaded.data_kind == data_kind
    assert loaded.data == data
    assert loaded.meta == meta
