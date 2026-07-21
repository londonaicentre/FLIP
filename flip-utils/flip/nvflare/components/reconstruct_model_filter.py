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
from nvflare.apis.fl_constant import FLContextKey
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


class ReconstructFullModelForEval(ReconstructFullModel):
    """Client-side ``task_data_filter`` for BOTH ``train`` and ``validate``: rebuild the full model.

    Extends :class:`ReconstructFullModel` to also reconstruct the full model for post-training
    cross-site validation (``GlobalModelEval``), whose head-only broadcast is produced by the
    server-side :class:`~flip.nvflare.components.broadcast_trim_filter.TrimEvalBroadcastVars`.

    Why one component on two tasks. The frozen backbone the client needs to reconstruct against is
    the one it received at training round 0 — in production the pretrained checkpoint is de-bundled
    server-side and never shipped to clients (see ``InitialCheckpointPTModelPersistor``), so the
    client's ONLY copy of the backbone is the round-0 broadcast cached during training. NVFLARE builds
    a fresh filter instance per filter-chain occurrence, so the training cache is shared with the
    evaluation phase ONLY when the same component instance handles both tasks — i.e. one filter chain
    whose ``tasks`` are ``["train", "validate"]``. This class is therefore wired onto both.

    Behaviour is dispatched by the current task name (set in ``fl_ctx`` before client task-data
    filters run):

    * **train** (or any non-evaluation task): delegate to :class:`ReconstructFullModel` — the proven
      round-gated cache-at-round-0 / merge-thereafter logic is unchanged.
    * **validate**: merge the broadcast (head-only, or a full model if the server fell back) onto the
      retained full model from training and hand the validator a full state dict. Fails loudly if the
      client never cached a full model (it never trained → no backbone) or if the broadcast carries
      keys absent from the retained model (a mismatch that would otherwise validate wrong weights).
    """

    def __init__(self, data_kinds: list[str] | None = None, evaluate_task_name: str = AppConstants.TASK_VALIDATION):
        """
        Args:
            data_kinds: DXO kinds to filter; defaults to WEIGHTS and WEIGHT_DIFF.
            evaluate_task_name: the task name under which cross-site validation broadcasts the model.
                Defaults to NVFLARE's ``AppConstants.TASK_VALIDATION`` ("validate").
        """
        super().__init__(data_kinds=data_kinds)
        self._evaluate_task_name = evaluate_task_name

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> DXO | None:
        task_name = fl_ctx.get_prop(FLContextKey.TASK_NAME)
        if task_name != self._evaluate_task_name:
            # Training (or any non-eval task): unchanged round-gated reconstruction.
            return super().process_dxo(dxo, shareable, fl_ctx)

        if self._full_weights is None:
            # Validation broadcast arrived but no full model was ever cached → this client never
            # trained (so never received the round-0 backbone). Fail loudly rather than validate an
            # incomplete model.
            raise RuntimeError(
                "ReconstructFullModelForEval: received a validation broadcast but no full model was "
                "cached during training (client likely did not participate in training); cannot "
                "reconstruct the frozen backbone for evaluation."
            )

        broadcast = dxo.data
        unexpected = set(broadcast) - set(self._full_weights)
        if unexpected:
            # The broadcast carries keys the retained model doesn't have — a structural mismatch.
            # Refuse rather than silently validate against a wrong/partial model.
            raise RuntimeError(
                f"ReconstructFullModelForEval: validation broadcast has {len(unexpected)} key(s) absent "
                f"from the retained full model (e.g. {sorted(unexpected)[:3]}); refusing to reconstruct."
            )

        merged = dict(self._full_weights)
        merged.update(broadcast)
        self.log_info(
            fl_ctx,
            f"ReconstructFullModelForEval: merged {len(broadcast)} validation broadcast var(s) into the "
            f"retained full model ({len(merged)} vars) for task {task_name!r}.",
        )
        dxo.data = merged
        return dxo
