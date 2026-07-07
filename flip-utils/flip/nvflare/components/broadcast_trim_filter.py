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

        weights = dxo.data
        trimmed = {name: value for name, value in weights.items() if self.pattern.search(name)}
        if not trimmed:
            # Matching nothing almost certainly means a wrong regex — broadcast the full model rather
            # than an empty one (clients would have nothing to train from).
            self.log_warning(
                fl_ctx,
                f"TrimBroadcastVars: regex {self.pattern.pattern!r} matched no keys; broadcasting full model.",
            )
            return None

        self.log_info(
            fl_ctx,
            f"TrimBroadcastVars: broadcasting {len(trimmed)} of {len(weights)} var(s) "
            f"matching {self.pattern.pattern!r} at round {current_round}.",
        )
        dxo.data = trimmed
        return dxo
