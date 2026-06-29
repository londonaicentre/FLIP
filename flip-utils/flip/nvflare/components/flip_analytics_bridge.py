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

from nvflare.apis.analytix import ANALYTIC_EVENT_TYPE, AnalyticsData
from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_constant import EventScope, FLContextKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable

from flip.constants import FlipEvents


class FlipAnalyticsBridge(FLComponent):
    """Translates NVFLARE analytics events fired by Client API scripts into
    FLIP's ``FlipEvents.SEND_RESULT`` federated events.

    Trainer scripts written against the NVFLARE Client API publish metrics
    through ``SummaryWriter`` / ``flare.log``. The ``InProcessClientAPIExecutor``
    forwards those records onto the client process as ``ANALYTIC_EVENT_TYPE``
    events. This widget rewraps each record as a ``DataKind.METRICS`` DXO and
    re-fires it under ``FlipEvents.SEND_RESULT`` with federation scope, so the
    server-side controller (``ScatterAndGather``) keeps receiving metrics
    through the same pipeline that ``send_metrics_value`` used to drive.
    """

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        if event_type != ANALYTIC_EVENT_TYPE:
            return

        event_data = fl_ctx.get_prop(FLContextKey.EVENT_DATA, None)
        if not isinstance(event_data, Shareable):
            return

        try:
            dxo = from_shareable(event_data)
        except Exception:
            self.log_exception(fl_ctx, "Could not parse analytic DXO from event data")
            return

        analytic = AnalyticsData.from_dxo(dxo)
        if analytic is None or analytic.value is None:
            return

        metric_data: dict = {"label": analytic.tag, "value": analytic.value}
        if analytic.step is not None:
            metric_data["round"] = analytic.step

        new_dxo = DXO(data_kind=DataKind.METRICS, data=metric_data)

        fl_ctx.set_prop(FLContextKey.EVENT_DATA, new_dxo.to_shareable(), private=True, sticky=False)
        fl_ctx.set_prop(FLContextKey.EVENT_SCOPE, EventScope.FEDERATION, private=True, sticky=False)
        fl_ctx.set_prop(FLContextKey.EVENT_ORIGIN, "flip_client", private=True, sticky=False)

        engine = fl_ctx.get_engine()
        if engine is None:
            self.log_error(fl_ctx, "No engine available on fl_ctx; cannot bridge metrics event")
            return

        engine.fire_event(FlipEvents.SEND_RESULT, fl_ctx)
