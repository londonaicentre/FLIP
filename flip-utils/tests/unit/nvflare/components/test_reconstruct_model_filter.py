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
from nvflare.apis.dxo import DXO, DataKind
from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.components.reconstruct_model_filter import ReconstructFullModel


def _ctx():
    ctx = MagicMock()
    ctx.get_peer_context.return_value = None
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
