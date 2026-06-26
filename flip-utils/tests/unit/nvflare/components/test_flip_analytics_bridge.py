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

from unittest.mock import MagicMock

import pytest
from nvflare.apis.analytix import ANALYTIC_EVENT_TYPE, AnalyticsData, AnalyticsDataType
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import EventScope, FLContextKey

from flip.constants import FlipEvents
from flip.nvflare.components.flip_analytics_bridge import FlipAnalyticsBridge


def _ctx_with_event_data(event_data, props=None):
    """Build a stand-in FLContext that the bridge can use."""
    props = props or {FLContextKey.EVENT_DATA: event_data}
    engine = MagicMock(name="engine")
    ctx = MagicMock(name="fl_ctx")
    ctx.get_engine.return_value = engine
    ctx.get_prop.side_effect = lambda key, default=None: props.get(key, default)
    ctx.set_prop.side_effect = lambda key, value, **_: props.__setitem__(key, value)
    return ctx, engine, props


def _make_analytic_shareable(tag: str, value: float, step: int | None = None):
    kwargs = {"global_step": step} if step is not None else {}
    data = AnalyticsData(key=tag, value=value, data_type=AnalyticsDataType.SCALAR, **kwargs)
    return data.to_dxo().to_shareable()


class TestFlipAnalyticsBridge:
    def test_ignores_non_analytic_events(self):
        """Events other than ANALYTIC_EVENT_TYPE must be a no-op."""
        bridge = FlipAnalyticsBridge()
        ctx, engine, _ = _ctx_with_event_data(None)
        bridge.handle_event("_some_other_event", ctx)
        engine.fire_event.assert_not_called()

    def test_skips_when_event_data_missing(self):
        bridge = FlipAnalyticsBridge()
        ctx, engine, _ = _ctx_with_event_data(None)
        bridge.handle_event(ANALYTIC_EVENT_TYPE, ctx)
        engine.fire_event.assert_not_called()

    def test_skips_when_event_data_not_shareable(self):
        bridge = FlipAnalyticsBridge()
        ctx, engine, _ = _ctx_with_event_data({"not": "a shareable"})
        bridge.handle_event(ANALYTIC_EVENT_TYPE, ctx)
        engine.fire_event.assert_not_called()

    def test_fires_send_result_with_round_when_step_set(self):
        bridge = FlipAnalyticsBridge()
        shareable = _make_analytic_shareable("TRAIN_LOSS", 0.42, step=5)
        ctx, engine, props = _ctx_with_event_data(shareable)

        bridge.handle_event(ANALYTIC_EVENT_TYPE, ctx)

        engine.fire_event.assert_called_once()
        event_type, fired_ctx = engine.fire_event.call_args.args
        assert event_type == FlipEvents.SEND_RESULT
        assert fired_ctx is ctx
        assert props[FLContextKey.EVENT_SCOPE] == EventScope.FEDERATION
        assert props[FLContextKey.EVENT_ORIGIN] == "flip_client"

        forwarded = from_shareable(props[FLContextKey.EVENT_DATA])
        assert forwarded.data_kind == DataKind.METRICS
        assert forwarded.data == {"label": "TRAIN_LOSS", "value": 0.42, "round": 5}

    def test_fires_send_result_without_round_when_no_step(self):
        bridge = FlipAnalyticsBridge()
        shareable = _make_analytic_shareable("TEST_LOSS", 1.5)
        ctx, engine, props = _ctx_with_event_data(shareable)

        bridge.handle_event(ANALYTIC_EVENT_TYPE, ctx)

        forwarded = from_shareable(props[FLContextKey.EVENT_DATA])
        assert forwarded.data == {"label": "TEST_LOSS", "value": 1.5}

    def test_skips_when_dxo_has_no_value(self):
        """A DXO whose AnalyticsData parses but has value=None should be ignored, not crash."""
        bridge = FlipAnalyticsBridge()
        empty_dxo = DXO(data_kind=DataKind.ANALYTIC, data={"track_key": "x", "track_value": None})
        ctx, engine, _ = _ctx_with_event_data(empty_dxo.to_shareable())
        # The from_dxo path will raise on missing fields; the bridge swallows that.
        bridge.handle_event(ANALYTIC_EVENT_TYPE, ctx)
        engine.fire_event.assert_not_called()


@pytest.mark.parametrize("invalid_event_type", ["foo", "_send_result", "another"])
def test_only_handles_analytic_event(invalid_event_type):
    bridge = FlipAnalyticsBridge()
    ctx, engine, _ = _ctx_with_event_data(_make_analytic_shareable("X", 1.0))
    bridge.handle_event(invalid_event_type, ctx)
    engine.fire_event.assert_not_called()
