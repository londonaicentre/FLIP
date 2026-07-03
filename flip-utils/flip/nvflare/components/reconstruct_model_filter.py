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

from nvflare.apis.dxo import DataKind
from nvflare.apis.dxo_filter import DXO, DXOFilter
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.app_common.app_constant import AppConstants


class ReconstructFullModel(DXOFilter):
    """Client-side ``task_data_filter``: rebuild the full global model from a trimmed broadcast.

    Pairs with the server-side :class:`TrimBroadcastVars`. After round 0 the server broadcasts only
    the trainable head; this filter retains the full model received at round 0 (frozen backbone +
    head) and merges each subsequent round's head into it, so the client's executor always receives a
    full state dict. User training code is therefore unchanged — it never sees the head-only wire
    payload, only the reconstructed full model.

    Stateful: the retained model persists across rounds (the filter is a job-scoped component,
    instantiated once for the run). If a trimmed update arrives before any full model was cached —
    e.g. a client that (re)joined after round 0 and so never received the backbone — the filter
    raises, which NVFLARE surfaces as a task-data-filter error, failing the round loudly rather than
    silently training on a partial model.
    """

    def __init__(self, data_kinds: list[str] | None = None):
        """
        Args:
            data_kinds: DXO kinds to filter; defaults to WEIGHTS and WEIGHT_DIFF.
        """
        if not data_kinds:
            data_kinds = [DataKind.WEIGHTS, DataKind.WEIGHT_DIFF]
        super().__init__(
            supported_data_kinds=[DataKind.WEIGHTS, DataKind.WEIGHT_DIFF],
            data_kinds_to_filter=data_kinds,
        )
        self._full_weights: dict | None = None

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> DXO | None:
        current_round = shareable.get_header(AppConstants.CURRENT_ROUND)
        weights = dxo.data

        if current_round is None or current_round <= 0:
            # Round 0: the server broadcasts the full model. Cache it (the frozen backbone we will
            # merge later rounds' heads onto) and pass it through unchanged.
            self._full_weights = dict(weights)
            self.log_info(
                fl_ctx,
                f"ReconstructFullModel: cached full global model ({len(weights)} vars) at round {current_round}.",
            )
            return None

        if self._full_weights is None:
            # A trimmed broadcast, but we never cached a full model → this client missed round 0.
            # Fail loudly rather than reconstruct against a non-existent backbone.
            raise RuntimeError(
                f"ReconstructFullModel: received a trimmed broadcast at round {current_round} but no full "
                "model was cached (client likely joined after round 0); cannot reconstruct the frozen backbone."
            )

        # Later rounds: merge the trimmed head into the retained full model. Backbone keys keep their
        # round-0 values; only the matching (head) keys are overwritten with the new global aggregate.
        self._full_weights.update(weights)
        self.log_info(
            fl_ctx,
            f"ReconstructFullModel: merged {len(weights)} broadcast var(s) into retained full model "
            f"({len(self._full_weights)} vars) at round {current_round}.",
        )
        dxo.data = dict(self._full_weights)
        return dxo
