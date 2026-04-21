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
#

"""Tests for flip.flower.metrics module.

The flwr package is not a runtime dependency of flip-utils; the module under
test only references Flower types behind a ``TYPE_CHECKING`` guard. These tests
therefore use plain ``Mock`` stand-ins for Flower ``Message`` objects rather
than importing ``flwr``.
"""

from unittest.mock import Mock

from flip.constants.flip_constants import ModelStatus
from flip.flower.metrics import handle_client_exception, handle_client_metrics

VALID_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _build_message(
    metrics: dict | None = None,
    site: str | None = None,
    client_name: str | None = None,
    has_error: bool = False,
    error: object | None = None,
    src_node_id: int = 42,
) -> Mock:
    """Build a minimal stand-in for a Flower reply Message.

    The production module only touches ``has_error()``, ``error``,
    ``content.get(...)``, and ``metadata.src_node_id`` — everything else on the
    real Message class is irrelevant to this test surface.
    """
    content: dict[str, object] = {}
    if metrics is not None:
        content["metrics"] = metrics
    config: dict[str, str] = {}
    if site is not None:
        config["site"] = site
    if client_name is not None:
        config["client_name"] = client_name
    if config:
        content["config"] = config

    msg = Mock()
    msg.has_error.return_value = has_error
    msg.error = error
    msg.content.get.side_effect = content.get
    msg.metadata.src_node_id = src_node_id
    return msg


class TestHandleClientMetrics:
    def test_errored_message_is_noop(self):
        msg = _build_message(metrics={"loss": 0.5}, site="Trust_1", has_error=True)
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_metrics.assert_not_called()

    def test_missing_metrics_is_noop(self):
        msg = _build_message(metrics=None, site="Trust_1")
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_metrics.assert_not_called()

    def test_empty_metrics_is_noop(self):
        msg = _build_message(metrics={}, site="Trust_1")
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_metrics.assert_not_called()

    def test_forwards_each_numeric_metric_uppercased(self):
        msg = _build_message(metrics={"train_loss": 0.5, "val_dice": 0.8}, site="Trust_1")
        flip = Mock()

        handle_client_metrics(msg, server_round=3, model_id=VALID_MODEL_ID, flip=flip)

        assert flip.send_metrics.call_count == 2
        flip.send_metrics.assert_any_call(
            client_name="Trust_1",
            model_id=VALID_MODEL_ID,
            label="TRAIN_LOSS",
            value=0.5,
            round=3,
        )
        flip.send_metrics.assert_any_call(
            client_name="Trust_1",
            model_id=VALID_MODEL_ID,
            label="VAL_DICE",
            value=0.8,
            round=3,
        )

    def test_skips_bookkeeping_keys(self):
        msg = _build_message(
            metrics={"num-examples": 1024, "num-iterations": 50, "loss": 0.1},
            site="Trust_1",
        )
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        assert flip.send_metrics.call_count == 1
        (_, kwargs), = flip.send_metrics.call_args_list
        assert kwargs["label"] == "LOSS"

    def test_skips_non_numeric_metrics(self):
        msg = _build_message(metrics={"note": "skip me", "loss": 0.1}, site="Trust_1")
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        assert flip.send_metrics.call_count == 1
        (_, kwargs), = flip.send_metrics.call_args_list
        assert kwargs["label"] == "LOSS"

    def test_parses_per_epoch_round_suffix(self):
        msg = _build_message(metrics={"train_loss.round_5": 0.42}, site="Trust_1")
        flip = Mock()

        handle_client_metrics(msg, server_round=99, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_metrics.assert_called_once_with(
            client_name="Trust_1",
            model_id=VALID_MODEL_ID,
            label="TRAIN_LOSS",
            value=0.42,
            round=5,
        )

    def test_malformed_round_suffix_falls_back_to_server_round(self):
        msg = _build_message(metrics={"train_loss.round_notanint": 0.42}, site="Trust_1")
        flip = Mock()

        handle_client_metrics(msg, server_round=7, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_metrics.assert_called_once_with(
            client_name="Trust_1",
            model_id=VALID_MODEL_ID,
            label="TRAIN_LOSS.ROUND_NOTANINT",
            value=0.42,
            round=7,
        )

    def test_falls_back_to_src_node_id_when_site_missing(self):
        msg = _build_message(metrics={"loss": 0.1}, site=None, src_node_id=7)
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        (_, kwargs), = flip.send_metrics.call_args_list
        assert kwargs["client_name"] == "unknown_7"

    def test_accepts_client_name_config_key_as_site_fallback(self):
        msg = _build_message(metrics={"loss": 0.1}, client_name="Trust_5")
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        (_, kwargs), = flip.send_metrics.call_args_list
        assert kwargs["client_name"] == "Trust_5"

    def test_site_takes_precedence_over_client_name(self):
        msg = _build_message(metrics={"loss": 0.1}, site="Trust_1", client_name="Trust_5")
        flip = Mock()

        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        (_, kwargs), = flip.send_metrics.call_args_list
        assert kwargs["client_name"] == "Trust_1"

    def test_hub_exception_does_not_break_loop(self):
        msg = _build_message(metrics={"a": 0.1, "b": 0.2, "c": 0.3}, site="Trust_1")
        flip = Mock()
        flip.send_metrics.side_effect = [RuntimeError("boom"), None, None]

        # Must not raise
        handle_client_metrics(msg, server_round=1, model_id=VALID_MODEL_ID, flip=flip)

        assert flip.send_metrics.call_count == 3


class TestHandleClientException:
    def test_no_error_is_noop(self):
        msg = _build_message(has_error=False)
        flip = Mock()

        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_handled_exception.assert_not_called()
        flip.update_status.assert_not_called()

    def test_forwards_error_string(self):
        msg = _build_message(has_error=True, error=RuntimeError("training failed"), site="Trust_2")
        flip = Mock()

        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_handled_exception.assert_called_once_with(
            formatted_exception="training failed",
            client_name="Trust_2",
            model_id=VALID_MODEL_ID,
        )

    def test_transitions_status_to_error_on_crash(self):
        msg = _build_message(has_error=True, error=RuntimeError("boom"), site="Trust_1")
        flip = Mock()

        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        flip.update_status.assert_called_once_with(VALID_MODEL_ID, ModelStatus.ERROR)

    def test_default_error_string_when_error_missing(self):
        msg = _build_message(has_error=True, error=None, site="Trust_2")
        flip = Mock()

        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_handled_exception.assert_called_once_with(
            formatted_exception="Unknown client error",
            client_name="Trust_2",
            model_id=VALID_MODEL_ID,
        )
        flip.update_status.assert_called_once_with(VALID_MODEL_ID, ModelStatus.ERROR)

    def test_falls_back_to_src_node_id_when_site_missing(self):
        msg = _build_message(has_error=True, error=RuntimeError("x"), site=None, src_node_id=11)
        flip = Mock()

        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        (_, kwargs), = flip.send_handled_exception.call_args_list
        assert kwargs["client_name"] == "unknown_11"

    def test_hub_exception_is_swallowed(self):
        msg = _build_message(has_error=True, error=RuntimeError("x"), site="Trust_1")
        flip = Mock()
        flip.send_handled_exception.side_effect = RuntimeError("hub down")

        # Must not raise
        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        flip.send_handled_exception.assert_called_once()
        # Status transition still attempted even if the exception forward failed.
        flip.update_status.assert_called_once_with(VALID_MODEL_ID, ModelStatus.ERROR)

    def test_update_status_failure_does_not_propagate(self):
        msg = _build_message(has_error=True, error=RuntimeError("x"), site="Trust_1")
        flip = Mock()
        flip.update_status.side_effect = RuntimeError("hub down")

        # Must not raise — the log forward already happened; the status update failure
        # is logged but not propagated so other crashed replies can still be processed.
        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        flip.update_status.assert_called_once_with(VALID_MODEL_ID, ModelStatus.ERROR)

    def test_content_access_value_error_on_errored_reply(self):
        # Flower raises ValueError on msg.content when a reply carries only an error;
        # the handler must still resolve a site name and transition status.
        msg = Mock()
        msg.has_error.return_value = True
        msg.error = RuntimeError("boom")
        type(msg).content = property(lambda self: (_ for _ in ()).throw(ValueError("no content")))
        msg.metadata.src_node_id = 99
        flip = Mock()

        handle_client_exception(msg, model_id=VALID_MODEL_ID, flip=flip)

        (_, kwargs), = flip.send_handled_exception.call_args_list
        assert kwargs["client_name"] == "unknown_99"
        flip.update_status.assert_called_once_with(VALID_MODEL_ID, ModelStatus.ERROR)
