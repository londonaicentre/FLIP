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

from unittest.mock import MagicMock

import pytest
import torch
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_constant import FLContextKey
from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.components.reconstruct_model_filter import ReconstructFullModel, ReconstructFullModelForEval


def _ctx():
    ctx = MagicMock()
    ctx.get_peer_context.return_value = None
    return ctx


def _task_ctx(task_name):
    """An FL context that reports the given current task name to task-aware filters."""
    ctx = MagicMock()
    ctx.get_peer_context.return_value = None
    ctx.get_prop.side_effect = lambda key, default=None: (task_name if key == FLContextKey.TASK_NAME else default)
    return ctx


def _shareable(current_round):
    sh = MagicMock()
    sh.get_header.side_effect = lambda key, default=None: (
        current_round if key == AppConstants.CURRENT_ROUND else default
    )
    return sh


def _dxo(data):
    return DXO(data_kind=DataKind.WEIGHTS, data=data)


class TestReconstructFullModel:
    def test_round_zero_caches_full_model_and_passes_through(self):
        f = ReconstructFullModel()
        full = _dxo({"backbone.0": 5, "backbone.1": 6, "omni_heads.0.weight": 1, "omni_heads.0.bias": 2})
        # Returns None (DXO unchanged) at round 0, and caches the full model for later merges.
        assert f.process_dxo(full, _shareable(0), _ctx()) is None
        assert set(f._full_weights.keys()) == {"backbone.0", "backbone.1", "omni_heads.0.weight", "omni_heads.0.bias"}

    def test_later_round_merges_head_into_retained_full_model(self):
        f = ReconstructFullModel()
        f.process_dxo(_dxo({"backbone.0": 5, "omni_heads.0.weight": 1}), _shareable(0), _ctx())

        # Round 1 broadcast carries only the trainable head.
        out = f.process_dxo(_dxo({"omni_heads.0.weight": 9}), _shareable(1), _ctx())

        assert set(out.data.keys()) == {"backbone.0", "omni_heads.0.weight"}
        assert out.data["backbone.0"] == 5  # frozen backbone retained from round 0
        assert out.data["omni_heads.0.weight"] == 9  # head updated to the new aggregate

    def test_trimmed_broadcast_before_full_model_raises(self):
        f = ReconstructFullModel()
        with pytest.raises(RuntimeError, match="no full model was cached"):
            f.process_dxo(_dxo({"omni_heads.0.weight": 9}), _shareable(3), _ctx())

    def test_missing_round_header_is_treated_as_full(self):
        f = ReconstructFullModel()
        assert f.process_dxo(_dxo({"a": 1, "b": 2}), _shareable(None), _ctx()) is None
        assert f._full_weights is not None

    def test_reconstructed_dict_is_a_copy_not_the_retained_state(self):
        """The outgoing dict must not be the same object as the retained cache (avoids aliasing)."""
        f = ReconstructFullModel()
        f.process_dxo(_dxo({"backbone.0": 5, "omni_heads.0.weight": 1}), _shareable(0), _ctx())
        out = f.process_dxo(_dxo({"omni_heads.0.weight": 9}), _shareable(1), _ctx())
        assert out.data is not f._full_weights


class TestReconstructFullModelForEval:
    """The eval-aware variant handles the same instance across ``train`` and ``validate`` — training
    populates the backbone cache; validation merges the head-only broadcast back onto it."""

    def test_train_task_delegates_to_round_gated_parent(self):
        """On the train task it is byte-for-byte the parent: round 0 caches + passes through, later
        rounds merge the head."""
        f = ReconstructFullModelForEval()
        cached = f.process_dxo(
            _dxo({"backbone.0": 5, "omni_heads.0.weight": 1}), _shareable(0), _task_ctx("train")
        )
        assert cached is None  # round 0: cached and passed through unchanged
        out = f.process_dxo(_dxo({"omni_heads.0.weight": 9}), _shareable(1), _task_ctx("train"))
        assert out.data == {"backbone.0": 5, "omni_heads.0.weight": 9}

    def test_validate_merges_head_onto_cached_backbone(self):
        f = ReconstructFullModelForEval()
        # Training cached the round-0 full model (frozen backbone + an initial head).
        f.process_dxo(
            _dxo({"backbone.0": 5, "backbone.1": 6, "omni_heads.0.weight": 1}), _shareable(0), _task_ctx("train")
        )
        # Cross-site validation broadcast, trimmed by the server to the head only.
        out = f.process_dxo(_dxo({"omni_heads.0.weight": 42}), _shareable(None), _task_ctx("validate"))
        assert out.data == {"backbone.0": 5, "backbone.1": 6, "omni_heads.0.weight": 42}

    def test_validate_without_cache_raises(self):
        """A client that never trained has no cached backbone → fail loudly rather than validate a
        headless/partial model."""
        f = ReconstructFullModelForEval()
        with pytest.raises(RuntimeError, match="no full model was cached during training"):
            f.process_dxo(_dxo({"omni_heads.0.weight": 42}), _shareable(None), _task_ctx("validate"))

    def test_validate_with_unexpected_keys_raises(self):
        """A broadcast key absent from the retained model is a structural mismatch → refuse."""
        f = ReconstructFullModelForEval()
        f.process_dxo(_dxo({"backbone.0": 5, "omni_heads.0.weight": 1}), _shareable(0), _task_ctx("train"))
        with pytest.raises(RuntimeError, match="absent from the retained full model"):
            f.process_dxo(_dxo({"phantom.key": 7}), _shareable(None), _task_ctx("validate"))

    def test_validate_full_broadcast_fallback_reconstructs(self):
        """If the server fell back to a full broadcast (regex matched nothing), merging a full model
        onto the cache still yields exactly that full model."""
        f = ReconstructFullModelForEval()
        f.process_dxo(_dxo({"backbone.0": 5, "omni_heads.0.weight": 1}), _shareable(0), _task_ctx("train"))
        out = f.process_dxo(
            _dxo({"backbone.0": 5, "omni_heads.0.weight": 9}), _shareable(None), _task_ctx("validate")
        )
        assert out.data == {"backbone.0": 5, "omni_heads.0.weight": 9}

    def test_reconstructed_dict_is_a_copy_not_the_retained_state(self):
        f = ReconstructFullModelForEval()
        f.process_dxo(_dxo({"backbone.0": 5, "omni_heads.0.weight": 1}), _shareable(0), _task_ctx("train"))
        out = f.process_dxo(_dxo({"omni_heads.0.weight": 9}), _shareable(None), _task_ctx("validate"))
        assert out.data is not f._full_weights

    def test_numeric_parity_with_full_broadcast(self):
        """The reconstructed (cached backbone + broadcast head) state dict is tensor-equal to the full
        global model the server WOULD have broadcast — the head-only eval path is lossless."""
        backbone = {"backbone.0": torch.randn(4, 4), "backbone.1": torch.randn(8)}
        global_head = {"omni_heads.0.weight": torch.randn(5, 4), "omni_heads.0.bias": torch.randn(5)}
        full_global = {**backbone, **global_head}  # what a full validate broadcast would carry

        f = ReconstructFullModelForEval()
        # Client cached the round-0 full model: the SAME frozen backbone, an older (pre-aggregation) head.
        cached = {**backbone, "omni_heads.0.weight": torch.randn(5, 4), "omni_heads.0.bias": torch.randn(5)}
        f.process_dxo(_dxo(dict(cached)), _shareable(0), _task_ctx("train"))
        # Validate: server trimmed the global model to just the head.
        out = f.process_dxo(_dxo(dict(global_head)), _shareable(None), _task_ctx("validate"))

        assert set(out.data.keys()) == set(full_global.keys())
        for key, value in full_global.items():
            assert torch.equal(out.data[key], value), f"tensor mismatch at {key}"
