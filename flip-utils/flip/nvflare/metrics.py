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

from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import EventScope, FedEventHeader, FLContextKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable

from flip import FLIP
from flip.constants.flip_constants import FlipEvents
from flip.utils.utils import Utils


def send_metrics_value(
    label: str, value: float, fl_ctx: FLContext, round: int | None = None, flip: FLIP = FLIP()
) -> None:
    """
    Sends a metric value to the Central Hub.

    If 'round' is provided, it will be included in the event data sent to the Central Hub. This allows the client to
    specify the x-value for the metric, which can be different from the global round number. If 'round' is not provided,
    the Central Hub will use the global round number as the x-value for the metric.

    Args:
        label: The label of the metric.
        value: The value of the metric.
        fl_ctx: The federated learning context.
        round: The local round number (default: None).
    """
    if not isinstance(label, str):
        raise TypeError(f"expect label to be string, but got {type(label)}")

    if not isinstance(fl_ctx, FLContext):
        raise TypeError(f"expect fl_ctx to be FLContext, but got {type(fl_ctx)}")

    engine = fl_ctx.get_engine()
    if engine is None:
        flip.logger.error("Error: no engine in fl_ctx, cannot fire metrics event")
        return

    flip.logger.info("Attempting to fire metrics event...")

    # Create a DXO - if 'round' is provided include it in the data, otherwise just send the label and value
    if round is not None:
        dxo = DXO(data_kind=DataKind.METRICS, data={"label": label, "value": value, "round": round})
    else:
        dxo = DXO(data_kind=DataKind.METRICS, data={"label": label, "value": value})

    event_data = dxo.to_shareable()

    fl_ctx.set_prop(FLContextKey.EVENT_DATA, event_data, private=True, sticky=False)
    fl_ctx.set_prop(
        FLContextKey.EVENT_SCOPE,
        value=EventScope.FEDERATION,
        private=True,
        sticky=False,
    )
    fl_ctx.set_prop(FLContextKey.EVENT_ORIGIN, "flip_client", private=True, sticky=False)

    engine.fire_event(FlipEvents.SEND_RESULT, fl_ctx)

    flip.logger.info("Successfully fired metrics event")


def handle_metrics_event(event_data: Shareable, global_round: int, model_id: str, flip: FLIP = FLIP()) -> None:
    """
    Use on the server to handle metrics data events raised by clients.

    Args:
        event_data: The event data containing the metrics.
        global_round: The global round number (aka _current_round in scatter_and_gather scripts).
        model_id: The ID of the model.
    """
    if Utils.is_valid_uuid(model_id) is False:
        raise ValueError(f"Invalid model ID: {model_id}, cant update model status")

    if not isinstance(global_round, int):
        raise TypeError(f"global_round must be type int but got {type(global_round)}")

    if not isinstance(event_data, Shareable):
        raise TypeError(f"event_data must be type Shareable but got {type(event_data)}")

    client_name = event_data.get_header(FedEventHeader.ORIGIN)
    metrics_data = from_shareable(event_data).data

    # NOTE currently the client name needs to match the trust name in the Central Hub for the metrics to be properly
    # associated with the client's contributions.
    trust_name = client_name.replace("site-", "Trust_")

    if "round" in metrics_data.keys():
        # Override the global rounds with the 'round' value sent by the client if it is provided. This allows the client
        # to specify the x-value for the metric, which can be different from the global round number.
        # TODO let the client specify an x-value for the metric that is not necessarily the round number, and use that
        # x-value when sending the metric to the Central Hub (see https://github.com/londonaicentre/FLIP/issues/148).
        flip.send_metrics(
            client_name=trust_name,
            model_id=model_id,
            label=metrics_data["label"],
            value=metrics_data["value"],
            round=metrics_data["round"],
        )
    else:
        # Legacy behaviour: if the client does not provide 'round', use the global round as the x-value for the metric.
        flip.send_metrics(
            client_name=trust_name,
            model_id=model_id,
            label=metrics_data["label"],
            value=metrics_data["value"],
            round=global_round,
        )
