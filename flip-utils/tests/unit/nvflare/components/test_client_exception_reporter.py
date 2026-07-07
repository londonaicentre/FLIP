from unittest.mock import MagicMock

from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import make_reply

from flip.nvflare.components import ClientExceptionReporter


class TestClientExceptionReporter:
    def test_reports_client_exception_using_job_metadata(self, monkeypatch):
        reporter = ClientExceptionReporter()
        reporter.flip = MagicMock()
        result = make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": "traceback"})
        fl_ctx = MagicMock(spec=FLContext)
        peer_ctx = MagicMock(spec=FLContext)
        peer_ctx.get_identity_name.return_value = "site-1"
        fl_ctx.get_peer_context.return_value = peer_ctx
        monkeypatch.setattr(
            "flip.nvflare.components.client_exception_reporter.get_flip_model_id",
            lambda ctx: "00000000-0000-0000-0000-000000000001",
        )

        assert reporter.process(result, fl_ctx) is result
        reporter.flip.send_handled_exception.assert_called_once_with(
            formatted_exception="traceback",
            client_name="site-1",
            model_id="00000000-0000-0000-0000-000000000001",
        )

    def test_ignores_success_and_unhandled_return_codes(self):
        reporter = ClientExceptionReporter()
        reporter.flip = MagicMock()

        for rc in (ReturnCode.OK, ReturnCode.EXECUTION_RESULT_ERROR):
            result = make_reply(rc)
            assert reporter.process(result, MagicMock(spec=FLContext)) is result

        reporter.flip.send_handled_exception.assert_not_called()

    def test_ignores_failure_without_exception_header(self):
        reporter = ClientExceptionReporter()
        reporter.flip = MagicMock()
        result = make_reply(ReturnCode.TASK_UNKNOWN)

        assert reporter.process(result, MagicMock(spec=FLContext)) is result
        reporter.flip.send_handled_exception.assert_not_called()

    def test_reporting_failure_does_not_replace_original_result(self, monkeypatch):
        reporter = ClientExceptionReporter()
        reporter.flip = MagicMock()
        reporter.flip.send_handled_exception.side_effect = RuntimeError("hub unavailable")
        result = make_reply(ReturnCode.TASK_UNKNOWN, headers={"exception": "unknown task"})
        fl_ctx = MagicMock(spec=FLContext)
        peer_ctx = MagicMock(spec=FLContext)
        peer_ctx.get_identity_name.return_value = "site-2"
        fl_ctx.get_peer_context.return_value = peer_ctx
        monkeypatch.setattr(
            "flip.nvflare.components.client_exception_reporter.get_flip_model_id",
            lambda ctx: "00000000-0000-0000-0000-000000000001",
        )

        assert reporter.process(result, fl_ctx) is result
        assert result.get_return_code() == ReturnCode.TASK_UNKNOWN

    def test_missing_peer_context_does_not_report(self):
        reporter = ClientExceptionReporter()
        reporter.flip = MagicMock()
        result = make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": "traceback"})
        fl_ctx = MagicMock(spec=FLContext)
        fl_ctx.get_peer_context.return_value = None

        assert reporter.process(result, fl_ctx) is result
        reporter.flip.send_handled_exception.assert_not_called()
