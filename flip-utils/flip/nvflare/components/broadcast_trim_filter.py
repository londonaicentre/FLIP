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

import re

from nvflare.apis.dxo import DataKind
from nvflare.apis.dxo_filter import DXO, DXOFilter
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.app_common.app_constant import AppConstants


class TrimBroadcastVars(DXOFilter):
    """Server-side ``task_data_filter``: after the first round, broadcast ONLY the trainable weights
    (those whose key matches ``include_vars``) instead of the full global model.

    The server→client mirror of :class:`KeepOnlyVars` (which trims the client→server update). For a
    frozen-backbone fine-tune the backbone is identical on every client after round 0, so
    re-broadcasting all ~759 MiB of it every round is pure waste: it saturates the wire and drives
    server-side heap growth (each broadcast is serialised into hundreds of MB-sized chunks). This
    trims the broadcast down to just the head after round 0; the client's :class:`ReconstructFullModel`
    filter merges it back onto the backbone delivered at round 0, so the trainer still receives a full
    state dict — no change to user training code is required.

    Round 0 is passed through UNTOUCHED so every client receives the backbone once (FLIP always runs
    ``start_round == 0``; the first round is where the backbone must ship). The filter is
    **non-mutating**: it builds a new dict rather than popping keys from ``dxo.data``, which on the
    server aliases the retained global model — popping would corrupt the server's own weights.
    """

    def __init__(self, include_vars: str | None = None, data_kinds: list[str] | None = None):
        """
        Args:
            include_vars: regex; only weight keys whose name matches (``re.search``) stay in the
                broadcast after round 0. If empty/None the filter is a no-op (full model every round).
            data_kinds: DXO kinds to filter; defaults to WEIGHTS and WEIGHT_DIFF.
        """
        if not data_kinds:
            data_kinds = [DataKind.WEIGHT_DIFF, DataKind.WEIGHTS]
        super().__init__(
            supported_data_kinds=[DataKind.WEIGHTS, DataKind.WEIGHT_DIFF],
            data_kinds_to_filter=data_kinds,
        )
        # Stored under the exact constructor-param name so NVFLARE's FedJob/Recipe export round-trips it:
        # FedJob serialises a component's args by matching instance attributes to __init__ param names, so
        # without this the recipe-exported config would drop include_vars and the filter would silently
        # become a no-op. (The deploy-time fl-server injection writes args.include_vars explicitly, so only
        # the recipe/export path depended on this.)
        self.include_vars = include_vars
        # None when no (or empty) regex is given — the filter is then a no-op (full model every round).
        self.pattern = re.compile(include_vars) if isinstance(include_vars, str) and include_vars else None

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> DXO | None:
        if self.pattern is None:
            return None

        current_round = shareable.get_header(AppConstants.CURRENT_ROUND)
        if current_round is None or current_round <= 0:
            # Round 0 (or a missing round header): broadcast the full model so every client receives
            # the frozen backbone once. Returning None leaves the DXO untouched.
            return None

        return self._trim_to_pattern(dxo, fl_ctx)

    def _trim_to_pattern(self, dxo: DXO, fl_ctx: FLContext) -> DXO | None:
        """Trim ``dxo.data`` to only the weight keys matching ``self.pattern``.

        Shared by the round-gated training filter and the always-trim evaluation variant
        (:class:`TrimEvalBroadcastVars`). Non-mutating on the caller's dict — it builds a new dict —
        because on the server ``dxo.data`` aliases the retained global model, so popping keys would
        corrupt the server's own weights.

        Args:
            dxo (DXO): the outgoing broadcast DXO whose ``data`` (a weights dict) is trimmed.
            fl_ctx (FLContext): the FL context, used only for logging.

        Returns:
            DXO | None: the DXO with its data trimmed to the matching (head) keys, or ``None`` (leave
            the DXO untouched, i.e. broadcast the full model) when the regex matches no keys.
        """
        assert self.pattern is not None  # callers guard the no-pattern (no-op) case before dispatching here
        weights = dxo.data
        trimmed = {name: value for name, value in weights.items() if self.pattern.search(name)}
        if not trimmed:
            # Matching nothing almost certainly means a wrong regex — broadcast the full model rather
            # than an empty one (clients would have nothing to train from / validate).
            self.log_warning(
                fl_ctx,
                f"{type(self).__name__}: regex {self.pattern.pattern!r} matched no keys; broadcasting full model.",
            )
            return None

        self.log_info(
            fl_ctx,
            f"{type(self).__name__}: broadcasting {len(trimmed)} of {len(weights)} var(s) "
            f"matching {self.pattern.pattern!r}.",
        )
        dxo.data = trimmed
        return dxo


class TrimEvalBroadcastVars(TrimBroadcastVars):
    """Server-side ``task_data_filter`` for the ``validate`` task: broadcast ONLY the trainable head.

    The evaluation counterpart of :class:`TrimBroadcastVars`. Post-training cross-site validation
    (``GlobalModelEval``) re-broadcasts the full ~759 MiB global model to every client purely so it
    can be scored — but for a frozen-backbone fine-tune the backbone is byte-identical to the
    pretrained checkpoint each client already received at training round 0, so re-shipping it is pure
    waste. This trims the ``validate`` broadcast down to just the head; the client's
    :class:`ReconstructFullModelForEval` merges it back onto the backbone it cached during training,
    so the validator still receives a full state dict — no change to user validation code is required.

    Unlike :class:`TrimBroadcastVars`, the trim is **unconditional**: the cross-site-validation task
    carries no ``CURRENT_ROUND`` header (the round-gated parent would therefore never trim it), and
    the client always already holds the backbone by the time validation runs.
    """

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> DXO | None:
        if self.pattern is None:
            return None
        return self._trim_to_pattern(dxo, fl_ctx)
