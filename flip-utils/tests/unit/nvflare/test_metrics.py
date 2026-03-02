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

"""Tests for flip.nvflare.utils.metrics module."""

from unittest.mock import Mock

import pytest
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import FedEventHeader, FLContextKey
from nvflare.apis.fl_context import FLContext

from flip.constants.flip_constants import FlipEvents
from flip.nvflare.metrics import handle_metrics_event, send_metrics_value


class TestSendMetricsValue:
    """Test send_metrics_value method."""

    def test_send_metrics_value_validates_label(self):
        """send_metrics_value should raise TypeError for non-string label."""
        fl_ctx = Mock()
        with pytest.raises(TypeError, match="expect label to be string"):
            send_metrics_value(123, 0.5, fl_ctx, 1)

    def test_send_metrics_value_validates_fl_ctx(self):
        """send_metrics_value should raise TypeError for invalid fl_ctx."""
        with pytest.raises(TypeError, match="expect fl_ctx to be FLContext"):
            send_metrics_value("loss", 0.5, "not_a_context", 1)

    def test_send_metrics_value_handles_missing_engine(self):
        """send_metrics_value should handle missing engine gracefully."""
        fl_ctx = Mock(spec=FLContext)
        fl_ctx.get_engine.return_value = None

        # Should not raise, just logs error
        send_metrics_value("loss", 0.5, fl_ctx, round=1)

    def test_send_metrics_value_fires_event(self):
        """send_metrics_value should fire event when engine is available."""
        fl_ctx = Mock(spec=FLContext)
        mock_engine = Mock()
        fl_ctx.get_engine.return_value = mock_engine

        send_metrics_value("loss", 0.5, fl_ctx, round=1)

        mock_engine.fire_event.assert_called_once()

    def test_send_metrics_value_fires_event_with_correct_data(self):
        """send_metrics_value should fire event with correct data."""
        fl_ctx = Mock(spec=FLContext)
        mock_engine = Mock()
        fl_ctx.get_engine.return_value = mock_engine

        send_metrics_value("loss", 0.5, fl_ctx, round=1)

        # fire_event called with (event_name, fl_ctx)
        mock_engine.fire_event.assert_called_once_with(FlipEvents.SEND_RESULT, fl_ctx)

        # Pull the actual Shareable passed into EVENT_DATA and validate its contents
        event_data_shareable = next(
            c.args[1] for c in fl_ctx.set_prop.call_args_list if c.args[0] == FLContextKey.EVENT_DATA
        )

        assert event_data_shareable is not None

        dxo = from_shareable(event_data_shareable)
        assert dxo.data_kind == DataKind.METRICS
        assert dxo.data == {"label": "loss", "value": 0.5, "round": 1}

    def test_send_metrics_value_fires_event_without_round(self):
        """send_metrics_value should fire event without round if not provided."""
        fl_ctx = Mock(spec=FLContext)
        mock_engine = Mock()
        fl_ctx.get_engine.return_value = mock_engine

        send_metrics_value("accuracy", 0.9, fl_ctx)

        mock_engine.fire_event.assert_called_once_with(FlipEvents.SEND_RESULT, fl_ctx)

        # Pull the actual Shareable passed into EVENT_DATA and validate its contents
        event_data_shareable = next(
            c.args[1] for c in fl_ctx.set_prop.call_args_list if c.args[0] == FLContextKey.EVENT_DATA
        )

        dxo = from_shareable(event_data_shareable)
        assert dxo.data_kind == DataKind.METRICS
        assert dxo.data == {"label": "accuracy", "value": 0.9}


class TestHandleMetricsEvent:
    """Test handle_metrics_event method."""

    def test_handle_metrics_event_validates_model_id(self):
        """handle_metrics_event should raise ValueError for invalid model_id."""
        event_data = Mock()
        with pytest.raises(ValueError, match="Invalid model ID"):
            handle_metrics_event(event_data, 1, "invalid_model_id")

    def test_handle_metrics_event_validates_global_round_is_int(self):
        """handle_metrics_event should raise TypeError if global_round is not an int."""
        event_data = Mock()
        with pytest.raises(TypeError, match="global_round must be type int"):
            handle_metrics_event(event_data, "not_an_int", "123e4567-e89b-12d3-a456-426614174000")

    def test_handle_metrics_event_validates_event_data_is_shareable(self):
        """handle_metrics_event should raise TypeError if event_data is not a Shareable."""
        with pytest.raises(TypeError, match="event_data must be type Shareable"):
            handle_metrics_event("not_a_shareable", 1, "123e4567-e89b-12d3-a456-426614174000")

    def test_handle_metrics_event_extracts_data(self):
        """handle_metrics_event should extract and process metrics data."""
        # Create mock event_data
        dxo = DXO(data_kind=DataKind.METRICS, data={"label": "loss", "value": 0.5, "round": 7})
        event_data = dxo.to_shareable()
        event_data.set_header(FedEventHeader.ORIGIN, "site-1")

        flip = Mock()

        handle_metrics_event(
            event_data=event_data,
            global_round=1,
            model_id="123e4567-e89b-12d3-a456-426614174000",
            flip=flip,
        )

        flip.send_metrics.assert_called_once_with(
            client_name="Trust_1",
            model_id="123e4567-e89b-12d3-a456-426614174000",
            label="loss",
            value=0.5,
            round=7,  # client override
        )

    def test_handle_metrics_event_with_no_round(self):
        """handle_metrics_event should handle event data without round."""
        # Create mock event_data
        dxo = DXO(data_kind=DataKind.METRICS, data={"label": "accuracy", "value": 0.9})
        event_data = dxo.to_shareable()
        event_data.set_header(FedEventHeader.ORIGIN, "site-2")

        flip = Mock()

        handle_metrics_event(
            event_data=event_data,
            global_round=3,
            model_id="123e4567-e89b-12d3-a456-426614174000",
            flip=flip,
        )

        flip.send_metrics.assert_called_once_with(
            client_name="Trust_2",
            model_id="123e4567-e89b-12d3-a456-426614174000",
            label="accuracy",
            value=0.9,
            round=3,  # fallback to global
        )
