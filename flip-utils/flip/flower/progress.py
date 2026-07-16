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

"""Flower round-progress emission — the Flower twin of the NVFLARE round relay.

Emits the typed round facts (``FLLogEvent``) that back the hub's round-aware
activity feed. Facts only: the hub composes display text at serve time, so
wording changes never require rebuilding FL images.

Every helper is best-effort — a telemetry failure is logged and must never
break the strategy loop (matching ``flip.flower.metrics``). Only the fl-server
should import from this module; fl-clients hold no hub credentials.

``flip.flower.strategy.FlipFedAvg`` wires these into the FedAvg hooks; app
templates should subclass it rather than calling these directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from flip import FLIP
from flip.flower.metrics import _resolve_site_name, handle_client_exception, handle_client_metrics
from flip.schemas import FLLogEvent

if TYPE_CHECKING:
    from flwr.common.message import Message

logger = logging.getLogger(__name__)

__all__ = [
    "RoundTelemetry",
    "report_round_started",
    "report_client_result",
    "report_round_aggregated",
    "resolve_absent_site",
]

Phase = Literal["train", "evaluate"]


def resolve_absent_site(
    dispatched_nodes: set[int], responded_nodes: set[int], site_by_node: dict[int, str]
) -> str | None:
    """Name the single client that was dispatched a task but never returned a healthy reply.

    Flower stamps a placeholder ``src_node_id`` on the error reply it synthesises
    for an unreachable node, so a dead client cannot be recognised from its own
    reply. It can be recognised by elimination: the strategy knows which nodes it
    dispatched to, which answered, and (from earlier healthy replies) each node's
    site name.

    Returns None when the absence is ambiguous (several nodes silent at once) or
    the absent node never identified itself — guessing would put a real trust's
    name on another trust's failure.

    Args:
        dispatched_nodes: Node ids the round's tasks were sent to.
        responded_nodes: Node ids that returned a healthy reply this round.
        site_by_node: Node id to site name, learned from healthy replies.

    Returns:
        str | None: The absent client's site name, or None when unidentifiable.
    """
    absent = dispatched_nodes - responded_nodes
    if len(absent) != 1:
        return None
    return site_by_node.get(next(iter(absent)))


class RoundTelemetry:
    """Per-run reply bookkeeping: who was dispatched, who answered, who fell out.

    Extracted from ``FlipFedAvg`` (which needs ``flwr`` to import) so CI can pin
    the two invariants that make crashed-client attribution safe:

    - absent clients are named only from sites learned off healthy replies
      (accumulated across the whole run) — never guessed from the placeholder
      identity on a synthesised error reply;
    - each phase keeps its **own** dispatch roster — a train strategy's evaluate
      arm may sample a different cohort, and resolving evaluate absences against
      the train roster would put one trust's name on another trust's failure.
    """

    def __init__(self) -> None:
        # node_id -> site name, learned from healthy replies across the whole run.
        self.site_by_node: dict[int, str] = {}
        self._dispatched_by_phase: dict[Phase, set[int]] = {}

    def record_dispatch(self, phase: Phase, node_ids: set[int]) -> None:
        """Record the node ids a phase's tasks were just sent to (replaces the
        phase's previous roster — each round samples afresh).

        Args:
            phase: Which strategy phase dispatched.
            node_ids: The ``dst_node_id`` of every dispatched message.
        """
        self._dispatched_by_phase[phase] = set(node_ids)

    def dispatched_count(self, phase: Phase) -> int | None:
        """How many clients the phase's current round was dispatched to.

        Args:
            phase: Which strategy phase to count.

        Returns:
            int | None: The roster size, or None when the phase never dispatched.
        """
        dispatched = self._dispatched_by_phase.get(phase)
        return len(dispatched) if dispatched is not None else None

    def forward_replies(
        self, replies: list[Message], phase: Phase, server_round: int, model_id: str, flip: FLIP
    ) -> int:
        """Forward every reply's metrics, exception and round event to the hub.

        Learns each healthy reply's site, then names the one client the phase
        dispatched to that never returned a healthy reply (see
        ``resolve_absent_site``) so its error is attributed to the right trust. fl-clients never reach the
        Central Hub directly — every forward happens here, server-side.

        Args:
            replies: The round's reply Messages.
            phase: The strategy phase these replies belong to.
            server_round: Flower's 1-based round number.
            model_id: The FLIP model ID for the run.
            flip: The FLIP instance used to reach the Central Hub.

        Returns:
            int: How many replies were healthy.
        """
        for msg in replies:
            site = _resolve_site_name(msg)
            if site is not None:
                self.site_by_node[msg.metadata.src_node_id] = site

        responded = {msg.metadata.src_node_id for msg in replies if not msg.has_error()}
        dispatched = self._dispatched_by_phase.get(phase, set())
        absent_site = resolve_absent_site(dispatched, responded, self.site_by_node)

        returned = 0
        for msg in replies:
            handle_client_metrics(msg, server_round, model_id, flip)
            handle_client_exception(msg, model_id, flip, site_name=absent_site)
            if report_client_result(msg, server_round, model_id, flip):
                returned += 1
        return returned


def _serialized_size_bytes(msg: Message) -> int | None:
    """Total serialized byte size of the reply's weight arrays, if any.

    Sums the raw buffer length of each ``Array`` in the reply's ``"arrays"``
    record — the same measure Flower uses internally. Evaluation replies carry
    no arrays and yield ``None``.
    """
    try:
        record = msg.content.get("arrays")
        if record is None:
            return None
        return int(sum(len(arr.data) for arr in record.values()))
    except Exception:
        # Distinguishes "my probe broke" (e.g. a Flower record-shape change)
        # from the legitimate no-arrays case above — otherwise sizes vanish
        # silently forever after an upgrade.
        logger.debug("Could not size a reply's arrays record", exc_info=True)
        return None


def report_round_started(flip: FLIP, model_id: str, server_round: int, total_rounds: int | None) -> None:
    """Report that the server dispatched the global model for a round.

    Args:
        flip: The FLIP instance used to reach the Central Hub.
        model_id: The FLIP model ID for the run.
        server_round: Flower's 1-based round number (already the wire contract).
        total_rounds: The run's round total, when known.
    """
    try:
        flip.send_event(
            model_id=model_id,
            event_type=FLLogEvent.ROUND_STARTED,
            global_round=server_round,
            details={"total_rounds": total_rounds} if total_rounds is not None else None,
        )
    except Exception:
        logger.exception("Failed to report round %d start", server_round)


def report_client_result(msg: Message, server_round: int, model_id: str, flip: FLIP) -> bool:
    """Report one received client reply; returns whether the reply was healthy.

    The boolean feeds the strategy's returned-count, so a telemetry failure on
    a healthy reply still counts it as returned.

    Args:
        msg: A Flower reply Message from a client.
        server_round: Flower's 1-based round number.
        model_id: The FLIP model ID for the run.
        flip: The FLIP instance used to reach the Central Hub.

    Returns:
        bool: True when the reply is healthy (no error), regardless of whether
        the telemetry post succeeded.
    """
    if msg.has_error():
        return False

    try:
        client_name = _resolve_site_name(msg)
        if client_name is None:
            # Without a site the hub cannot attribute the upload to a trust; a
            # hub-level "someone uploaded" row would be worse than silence.
            logger.warning("Skipping an unattributable client result in round %d", server_round)
            return True

        size_bytes = _serialized_size_bytes(msg)
        flip.send_event(
            model_id=model_id,
            event_type=FLLogEvent.CLIENT_RESULT_RECEIVED,
            global_round=server_round,
            client_name=client_name,
            details={"size_bytes": size_bytes} if size_bytes is not None else None,
        )
    except Exception:
        logger.exception("Failed to report a client result for round %d", server_round)
    return True


def report_round_aggregated(
    flip: FLIP, model_id: str, server_round: int, returned: int | None, expected: int | None
) -> None:
    """Report that a round's replies were aggregated.

    Args:
        flip: The FLIP instance used to reach the Central Hub.
        model_id: The FLIP model ID for the run.
        server_round: Flower's 1-based round number.
        returned: Healthy replies received this round, when known.
        expected: Clients the round was dispatched to, when known.
    """
    try:
        details = None
        if returned is not None and expected is not None:
            details = {"returned": returned, "expected": expected}
        flip.send_event(
            model_id=model_id,
            event_type=FLLogEvent.ROUND_AGGREGATED,
            global_round=server_round,
            details=details,
        )
    except Exception:
        logger.exception("Failed to report round %d aggregation", server_round)
