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

"""Flower metrics utilities — mirrors flip.nvflare.metrics for the Flower framework.

In Flower, clients return metrics in their reply Message via MetricRecord.
The server-side strategy receives these in aggregate_train / aggregate_evaluate
and should call handle_client_metrics to forward them to the Central Hub.

Only the fl-server should import from this module — it forwards to the
Central Hub using credentials that must never reach the fl-client containers.

Usage (server-side, in a FedAvg strategy subclass):

    from flip.flower.metrics import handle_client_metrics, handle_client_exception

    def aggregate_train(self, server_round, replies):
        for msg in replies:
            handle_client_metrics(msg, server_round, self.model_id, self.flip)
            handle_client_exception(msg, self.model_id, self.flip)
        return super().aggregate_train(server_round, replies)
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from flip import FLIP
from flip.constants.flip_constants import ModelStatus
from flip.schemas import split_x_label

if TYPE_CHECKING:
    from flwr.common.message import Message

logger = logging.getLogger(__name__)

__all__ = ["handle_client_metrics", "handle_client_exception"]

# Metric keys that are bookkeeping rather than reportable metrics.
_BOOKKEEPING_KEYS = frozenset({"num-examples", "num-iterations"})


def handle_client_metrics(
    msg: Message,
    server_round: int,
    model_id: str,
    flip: FLIP = FLIP(),
) -> None:
    """Forward per-client metrics from a Flower reply Message to the Central Hub.

    Extracts all numeric metrics from the client's MetricRecord and sends each
    one to the Central Hub via flip.send_metrics. The metric label is converted
    to uppercase to match the FLIP convention (e.g. "train_loss" -> "TRAIN_LOSS").

    Metric keys following the "<label>[@<x_label>][.x_<V>]" pattern are split so each data point carries
    its own plot coordinate (a float x-value) and optional x-axis label, letting the Hub plot intra-round
    progress (e.g. "train_loss@epoch.x_5" -> label="TRAIN_LOSS", x_label="epoch", x_value=5.0). The FL
    global round is always recorded alongside as provenance and is the default plot coordinate for keys
    with no ".x_" suffix.

    Only the fl-server should call this function — fl-clients must not hold
    the credentials needed to reach the Central Hub.

    Args:
        msg: A Flower reply Message from a client. Expected to contain a
            "metrics" MetricRecord and optionally a "config" ConfigRecord
            with a "site" key identifying the client.
        server_round: The current server round number — recorded as every
            metric's provenance global round, and the default plot coordinate
            for any metric that does not embed its own ".x_<V>" suffix.
        model_id: The FLIP model ID. Validated by the underlying ``flip``
            implementation when it reaches the Central Hub; the handler
            itself is tolerant so LOCAL_DEV runs with placeholder ids work.
        flip: The FLIP instance used to reach the Central Hub.
    """
    if msg.has_error():
        return

    metrics = msg.content.get("metrics")
    if not metrics:
        return

    site_name = _resolve_site_name(msg)

    for label, value in dict(metrics).items():
        if label in _BOOKKEEPING_KEYS or not isinstance(value, (int, float)):
            continue

        metric_label, metric_x_value, metric_x_label = _parse_metric_key(label)

        try:
            flip.send_metrics(
                client_name=site_name,
                model_id=model_id,
                label=metric_label.upper(),
                value=float(value),
                global_round=server_round,
                x_value=metric_x_value,
                x_label=metric_x_label,
            )
            logger.info(
                "Forwarded metric %s=%.4f for client %s (round %d, x_value %s)",
                metric_label,
                value,
                site_name,
                server_round,
                metric_x_value,
            )
        except Exception:
            # Never let one bad metric break the aggregation loop; the hub
            # client already logs its own HTTP failures, but guard here so an
            # unexpected error in one iteration doesn't drop the remaining
            # metrics for other clients in the same round.
            logger.exception("Failed to forward metric %s for client %s", label, site_name)


def handle_client_exception(
    msg: Message,
    model_id: str,
    flip: FLIP = FLIP(),
) -> None:
    """Forward a crashed-client reply to the Central Hub and mark the run ERROR.

    When a Flower client raises, the reply Message arrives with ``has_error()``
    set. This helper both forwards the error string (so the Hub can display it
    alongside the model run) and transitions the run status to ``ERROR`` —
    without the latter, a crashed client would leave the Hub showing a
    still-running run indefinitely.

    Only the fl-server should call this function — fl-clients must not hold
    the credentials needed to reach the Central Hub.

    Args:
        msg: A Flower reply Message from a client.
        model_id: The FLIP model ID. Validated by the underlying ``flip``
            implementation when it reaches the Central Hub.
        flip: The FLIP instance used to reach the Central Hub.
    """
    if not msg.has_error():
        return

    site_name = _resolve_site_name(msg)
    error_msg = str(msg.error) if msg.error else "Unknown client error"

    try:
        flip.send_handled_exception(
            formatted_exception=error_msg,
            client_name=site_name,
            model_id=model_id,
        )
        logger.warning("Forwarded client exception for %s: %s", site_name, error_msg)
    except Exception:
        logger.exception("Failed to forward client exception for %s", site_name)

    try:
        flip.update_status(model_id, ModelStatus.ERROR)
    except Exception:
        logger.exception("Failed to transition model %s to ERROR status", model_id)


def _resolve_site_name(msg: Message) -> str:
    """Return the client's site name, falling back to the source node id.

    Accepts either ``"site"`` (standard tutorial convention) or
    ``"client_name"`` (used by the evaluation tutorial) from the config record
    so downstream apps can pick either key.

    Tolerates content-less messages — Flower raises ``ValueError`` when
    ``msg.content`` is accessed on an errored reply, and the exception
    handler legitimately encounters that case.
    """
    try:
        config = msg.content.get("config")
    except (ValueError, AttributeError):
        config = None
    if config:
        for key in ("site", "client_name"):
            if key in config:
                return config[key]
    return f"unknown_{msg.metadata.src_node_id}"


# Trailing-suffix spellings that set a metric's plot x-coordinate. ".x_" is the canonical form;
# ".round_" is the deprecated pre-x_value spelling, kept parsing so existing apps don't break.
_X_VALUE_SUFFIXES = (".x_", ".round_")


def _parse_metric_key(key: str) -> tuple[str, float | None, str | None]:
    """Split a Flower metric key into ``(label, x_value, x_label)``.

    The key grammar is ``<label>[@<x_label>][.x_<V>]``:

    - ``.x_<V>`` (optional, trailing) sets the plot x-coordinate; ``V`` is a float literal (``.x_5``,
      ``.x_1.5``, ``.x_1e-3``). Absent or non-numeric -> ``None`` (the hub plots the metric at its FL
      global round). ``.round_<N>`` is the deprecated pre-x_value spelling and still parses.
    - ``@<x_label>`` (optional) names the x-axis, e.g. "epoch"; absent -> ``None`` (the hub defaults it
      to "Global Round"). A plot's identity is (label, x_label), so distinct x-labels render as separate
      plots — see https://github.com/londonaicentre/FLIP/issues/148.

    Examples: ``"loss"`` -> ("loss", None, None); ``"loss.x_1.5"`` -> ("loss", 1.5, None);
    ``"loss@epoch"`` -> ("loss", None, "epoch"); ``"loss@epoch.x_5"`` -> ("loss", 5.0, "epoch").
    """
    x_value: float | None = None
    for suffix in _X_VALUE_SUFFIXES:
        if suffix in key:
            base, x_value_str = key.rsplit(suffix, 1)
            try:
                parsed = float(x_value_str)
            except ValueError:
                # Non-numeric suffix: leave the key (and its suffix) intact.
                continue
            # nan/inf would survive to the DB and break JSON-encoding the hub's metrics response.
            if math.isfinite(parsed):
                key, x_value = base, parsed
                break

    key, x_label = split_x_label(key)

    return key, x_value, x_label
