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

_MODEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def _handle(data, global_round=1, origin="site-1", flip=None):
    """Run handle_metrics_event on a METRICS DXO from ``origin``; return the flip mock."""
    event_data = DXO(data_kind=DataKind.METRICS, data=data).to_shareable()
    event_data.set_header(FedEventHeader.ORIGIN, origin)
    flip = flip or Mock()
    handle_metrics_event(event_data=event_data, global_round=global_round, model_id=_MODEL_ID, flip=flip)
    return flip


def _sent_dxo(fl_ctx):
    """The DXO send_metrics_value put into the context's EVENT_DATA."""
    event_data_shareable = next(
        c.args[1] for c in fl_ctx.set_prop.call_args_list if c.args[0] == FLContextKey.EVENT_DATA
    )
    assert event_data_shareable is not None
    return from_shareable(event_data_shareable)


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
        send_metrics_value("loss", 0.5, fl_ctx, x_value=1)

    @pytest.fixture
    def fl_ctx(self):
        fl_ctx = Mock(spec=FLContext)
        fl_ctx.get_engine.return_value = Mock()
        return fl_ctx

    @pytest.mark.parametrize(
        ("args", "kwargs", "expected"),
        [
            pytest.param(
                ("loss", 0.5), {"x_value": 1}, {"label": "loss", "value": 0.5, "x_value": 1}, id="with_x_value"
            ),
            pytest.param(("accuracy", 0.9), {}, {"label": "accuracy", "value": 0.9}, id="without_x_value"),
            # FLIP#148
            pytest.param(
                ("loss", 0.5),
                {"x_value": 2.5, "x_label": "epoch"},
                {"label": "loss", "value": 0.5, "x_value": 2.5, "x_label": "epoch"},
                id="with_x_label",
            ),
        ],
    )
    def test_send_metrics_value_event_data(self, fl_ctx, args, kwargs, expected):
        """send_metrics_value fires SEND_RESULT carrying the metric as a METRICS DXO."""
        send_metrics_value(args[0], args[1], fl_ctx, **kwargs)

        fl_ctx.get_engine.return_value.fire_event.assert_called_once_with(FlipEvents.SEND_RESULT, fl_ctx)
        dxo = _sent_dxo(fl_ctx)
        assert dxo.data_kind == DataKind.METRICS
        assert dxo.data == expected

    def test_send_metrics_value_omits_non_finite_x_value(self, fl_ctx):
        """A non-finite coordinate is caught at its source: omitted from the DXO with a warning."""
        flip = Mock()

        send_metrics_value("loss", 0.5, fl_ctx, x_value=float("inf"), flip=flip)

        assert _sent_dxo(fl_ctx).data == {"label": "loss", "value": 0.5}
        flip.logger.warning.assert_called_once()


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
            handle_metrics_event(event_data, "not_an_int", _MODEL_ID)

    def test_handle_metrics_event_validates_event_data_is_shareable(self):
        """handle_metrics_event should raise TypeError if event_data is not a Shareable."""
        with pytest.raises(TypeError, match="event_data must be type Shareable"):
            handle_metrics_event("not_a_shareable", 1, _MODEL_ID)

    @pytest.mark.parametrize(
        ("data", "origin", "global_round", "x_value", "x_label"),
        [
            # A client x_value places the point on the plot; global_round stays the server's true round.
            pytest.param(
                {"label": "loss", "value": 0.5, "x_value": 7}, "site-1", 1, 7, None, id="x_value_never_overrides_round"
            ),
            # No x_value: forwarded as None, and the hub plots at the global round.
            pytest.param({"label": "accuracy", "value": 0.9}, "site-2", 3, None, None, id="no_x_value"),
            # A pre-x_value client image sends 'round' in the DXO; it must land as the plot coordinate.
            pytest.param({"label": "loss", "value": 0.5, "round": 7}, "site-1", 1, 7, None, id="legacy_round_key"),
            # An explicit None x_value must still fall back to the legacy 'round' coordinate.
            pytest.param(
                {"label": "loss", "value": 0.5, "x_value": None, "round": 4},
                "site-1",
                1,
                4,
                None,
                id="none_x_value_falls_back_to_round",
            ),
            # A client-supplied x_label is forwarded to the hub (FLIP#148).
            pytest.param(
                {"label": "loss", "value": 0.5, "x_value": 5, "x_label": "epoch"},
                "site-3",
                1,
                5,
                "epoch",
                id="x_label_forwarded",
            ),
        ],
    )
    def test_handle_metrics_event_forwards_call(self, data, origin, global_round, x_value, x_label):
        flip = _handle(data, global_round, origin)

        flip.send_metrics.assert_called_once_with(
            client_name=origin,
            model_id=_MODEL_ID,
            label=data["label"],
            value=data["value"],
            global_round=global_round,  # provenance: always the server's round
            x_value=x_value,
            x_label=x_label,
        )

    def test_handle_metrics_event_drops_non_finite_x_value(self):
        """A NaN/inf coordinate is dropped to None (hub plots at the global round), mirroring Flower."""
        flip = _handle({"label": "loss", "value": 0.5, "x_value": float("nan")}, global_round=2)

        assert flip.send_metrics.call_args.kwargs["x_value"] is None
        flip.logger.warning.assert_called_once()

    def test_handle_metrics_event_survives_send_metrics_failure(self):
        """One bad metric must not propagate out of FLARE's event dispatch (matches the Flower loop)."""
        flip = Mock()
        flip.send_metrics.side_effect = RuntimeError("validation blew up")

        _handle({"label": "loss", "value": 0.5}, flip=flip)

        flip.logger.exception.assert_called_once()
