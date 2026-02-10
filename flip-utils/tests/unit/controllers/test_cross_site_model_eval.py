from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.shareable import Shareable
from nvflare.app_common.app_constant import AppConstants

from flip.controllers.cross_site_model_eval import CrossSiteModelEval


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

    def test_start_controller_bad_model_locator(self):
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
        with patch("flip.controllers.cross_site_model_eval.from_shareable", return_value=MagicMock()):
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

        with patch("flip.controllers.cross_site_model_eval.DXO", MagicMock):
            with patch.object(controller, "_save_validation_content", return_value="/models/SRV_m1"):
                controller.log_info = MagicMock()
                controller.fire_event = MagicMock()
                res = controller._locate_server_models(fl_ctx)

        assert res is True
        assert "SRV_m1" in controller._server_models
