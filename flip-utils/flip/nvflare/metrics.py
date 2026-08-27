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

import math

from nvflare.apis.dxo import DXO, DataKind, from_shareable
from nvflare.apis.fl_constant import EventScope, FedEventHeader, FLContextKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable

from flip import FLIP
from flip.constants.flip_constants import FlipEvents
from flip.utils.utils import Utils


def send_metrics_value(
    label: str,
    value: float,
    fl_ctx: FLContext,
    x_value: float | None = None,
    x_label: str | None = None,
    flip: FLIP = FLIP(),
) -> None:
    """
    Sends a metric value to the Central Hub.

    The metric is always recorded against the FL global round it is reported in (provenance). Where it is
    *plotted* is the (x_label, x_value) pair: 'x_value' is the x-coordinate (e.g. an epoch counter, a step,
    any float) and 'x_label' names the x-axis (e.g. "epoch"). Both default to the FL global round / the
    "Global Rounds" axis when absent. A plot's identity is (label, x_label), so the same metric logged under
    different x-labels renders as separate plots (see https://github.com/londonaicentre/FLIP/issues/148).

    Args:
        label: The label of the metric.
        value: The value of the metric.
        fl_ctx: The federated learning context.
        x_value: The x-coordinate the metric is plotted at (default: None -> the FL global round).
        x_label: The label naming the x-axis (default: None -> "Global Rounds").
        flip: FLIP instance used for logging (default: FLIP()).
    """
    if not isinstance(label, str):
        raise TypeError(f"expect label to be string, but got {type(label)}")

    if not isinstance(fl_ctx, FLContext):
        raise TypeError(f"expect fl_ctx to be FLContext, but got {type(fl_ctx)}")

    engine = fl_ctx.get_engine()
    if engine is None:
        flip.logger.error("Error: no engine in fl_ctx, cannot fire metrics event")
        return

    # Catch a degenerate coordinate at its source: a NaN/inf x_value would be rejected server-side
    # anyway (the hub schema enforces allow_inf_nan=False), so drop it here with a warning and let the
    # metric plot at the global round instead.
    if x_value is not None and not (isinstance(x_value, (int, float)) and math.isfinite(x_value)):
        flip.logger.warning(f"Dropping non-finite x_value {x_value!r} for metric {label}; plotting at the global round")
        x_value = None

    # Build the DXO data. 'x_value' (x-coordinate) and 'x_label' (x-axis label) are optional and only
    # included when provided, so the server can fall back to the global round / the "Global Rounds" label.
    data: dict = {"label": label, "value": value}
    if x_value is not None:
        data["x_value"] = x_value
    if x_label is not None:
        data["x_label"] = x_label
    dxo = DXO(data_kind=DataKind.METRICS, data=data)

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

    flip.logger.debug(
        "Fired metrics event: label=%s value=%s x_value=%s x_label=%s",
        label,
        value,
        x_value,
        x_label,
    )


def handle_metrics_event(event_data: Shareable, global_round: int, model_id: str, flip: FLIP = FLIP()) -> None:
    """
    Use on the server to handle metrics data events raised by clients.

    Args:
        event_data: The event data containing the metrics.
        global_round: The global round number (aka _current_round in scatter_and_gather scripts).
        model_id: The ID of the model.
        flip: FLIP instance used to forward metrics to the Central Hub (default: FLIP()).
    """
    if Utils.is_valid_uuid(model_id) is False:
        raise ValueError(f"Invalid model ID: {model_id}, cant update model status")

    if not isinstance(global_round, int):
        raise TypeError(f"global_round must be type int but got {type(global_round)}")

    if not isinstance(event_data, Shareable):
        raise TypeError(f"event_data must be type Shareable but got {type(event_data)}")

    # The client name is the participant name we specified in the FLARE provisioning file (project yaml file).
    client_name = event_data.get_header(FedEventHeader.ORIGIN)

    # Extract the metrics data from the event data. The client sends the metric label and value, and optionally
    # an 'x_value' (x-coordinate) and 'x_label' (x-axis label).
    metrics_data = from_shareable(event_data).data

    # global_round is provenance — always the server's current round, never overridden by the client. The
    # client may place the point elsewhere on the plot via 'x_value' (e.g. an epoch/step counter) and name
    # the axis via 'x_label'; absent, the hub plots it at the global round on the "Global Rounds" axis. A
    # plot's identity is (label, x_label), so distinct x-labels render as separate plots (see
    # https://github.com/londonaicentre/FLIP/issues/148). 'round' is the pre-x_value DXO spelling, read as
    # a fallback — including when 'x_value' is present but None — so a stale client image doesn't
    # silently lose its per-epoch coordinates.
    x_value = metrics_data.get("x_value")
    if x_value is None:
        x_value = metrics_data.get("round")

    # This is the guard that holds against old/foreign client images: a non-finite coordinate would
    # fail TrainingMetrics validation inside send_metrics *before* its try block and propagate out of
    # NVFLARE's event dispatch, losing the metric with a server traceback. Mirror the Flower leg
    # (_parse_metric_key): drop it to None so the hub plots the point at the global round.
    if x_value is not None and not (isinstance(x_value, (int, float)) and math.isfinite(x_value)):
        flip.logger.warning(
            f"Dropping non-finite x_value {x_value!r} for metric {metrics_data.get('label')}; "
            f"plotting at the global round"
        )
        x_value = None

    try:
        flip.send_metrics(
            client_name=client_name,
            model_id=model_id,
            label=metrics_data["label"],
            value=metrics_data["value"],
            global_round=global_round,
            x_value=x_value,
            x_label=metrics_data.get("x_label"),
        )
    except Exception:
        # Never let one bad metric escape into NVFLARE's event dispatch (matches the
        # Flower forward loop) — the metric is lost, but the run and the round's
        # remaining metrics carry on.
        flip.logger.exception(f"Failed to forward metric {metrics_data.get('label')} to the hub")
