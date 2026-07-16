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
from typing import TYPE_CHECKING

from flip import FLIP
from flip.constants.flip_constants import ModelStatus

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

    Per-epoch metric keys following the "<label>.round_<N>" pattern are split so
    each data point is recorded against its own round number, letting the Hub
    plot intra-round progress (e.g. "train_loss.round_5" -> label="TRAIN_LOSS",
    round=5).

    Only the fl-server should call this function — fl-clients must not hold
    the credentials needed to reach the Central Hub.

    Args:
        msg: A Flower reply Message from a client. Expected to contain a
            "metrics" MetricRecord and optionally a "config" ConfigRecord
            with a "site" key identifying the client.
        server_round: The current server round number, used as the default
            x-axis value for any metric that does not embed its own round.
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
    if site_name is None:
        # A healthy reply always carries its site; without one the hub cannot
        # attribute the metric to a trust, so there is nothing useful to send.
        logger.warning("Dropping metrics from an unattributable client (node %s)", msg.metadata.src_node_id)
        return

    for label, value in dict(metrics).items():
        if label in _BOOKKEEPING_KEYS or not isinstance(value, (int, float)):
            continue

        metric_label, metric_round = _parse_metric_key(label, server_round)

        try:
            flip.send_metrics(
                client_name=site_name,
                model_id=model_id,
                label=metric_label.upper(),
                value=float(value),
                round=metric_round,
            )
            logger.info(
                "Forwarded metric %s=%.4f for client %s (round %d)",
                metric_label,
                value,
                site_name,
                metric_round,
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
    site_name: str | None = None,
) -> None:
    """Forward a crashed-client reply to the Central Hub and mark the run ERROR.

    When a Flower client raises, the reply Message arrives with ``has_error()``
    set. This helper both forwards the error string (so the Hub can display it
    alongside the model run) and transitions the run status to ``ERROR`` —
    without the latter, a crashed client would leave the Hub showing a
    still-running run indefinitely.

    A crashed reply carries neither content nor a usable node id, so the caller
    may supply ``site_name`` (``FlipFedAvg`` names the client by elimination).
    When the client cannot be identified the exception is reported model-level
    (``client_name=None``) rather than under a fabricated ``unknown_<node_id>``
    name: hubs older than the round-telemetry release reject an unresolvable
    name outright (losing the traceback), and current hubs keep the row only as
    an "unattributed client" fallback — the fabricated name is noise either way.

    Only the fl-server should call this function — fl-clients must not hold
    the credentials needed to reach the Central Hub.

    Args:
        msg: A Flower reply Message from a client.
        model_id: The FLIP model ID. Validated by the underlying ``flip``
            implementation when it reaches the Central Hub.
        flip: The FLIP instance used to reach the Central Hub.
        site_name: The client's site, when the caller established it out-of-band.
    """
    if not msg.has_error():
        return

    site_name = site_name or _resolve_site_name(msg)
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


def _resolve_site_name(msg: Message) -> str | None:
    """Return the client's site name from its reply, or None when absent.

    Accepts either ``"site"`` (standard tutorial convention) or
    ``"client_name"`` (used by the evaluation tutorial) from the config record
    so downstream apps can pick either key.

    Tolerates content-less messages — Flower raises ``ValueError`` when
    ``msg.content`` is accessed on an errored reply, and the exception handler
    legitimately encounters that case. Such a reply also carries a placeholder
    ``src_node_id``, so it identifies nothing: return None rather than inventing
    an ``unknown_<node_id>`` name the hub cannot resolve to a trust. Callers that
    hold round context (``FlipFedAvg``) name the client by elimination instead;
    see ``flip.flower.progress.resolve_absent_site``.

    Args:
        msg: A Flower reply Message from a client.

    Returns:
        str | None: The site name, or None when the reply does not carry one.
    """
    try:
        config = msg.content.get("config")
    except (ValueError, AttributeError):
        config = None
    if config:
        for key in ("site", "client_name"):
            if key in config:
                return str(config[key])
    return None


def _parse_metric_key(label: str, default_round: int) -> tuple[str, int]:
    """Split a metric key of the form ``<label>.round_<N>`` into (label, N).

    If the suffix is absent or the round component isn't an int, returns
    the original label and ``default_round``.
    """
    if ".round_" not in label:
        return label, default_round

    metric_label, round_str = label.rsplit(".round_", 1)
    try:
        return metric_label, int(round_str)
    except ValueError:
        return label, default_round
