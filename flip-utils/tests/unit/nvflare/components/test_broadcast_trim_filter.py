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

from nvflare.apis.dxo import DXO, DataKind
from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.components.broadcast_trim_filter import TrimBroadcastVars, TrimEvalBroadcastVars


def _ctx():
    # NVFLARE's logging path validates get_peer_context() is an FLContext or None.
    ctx = MagicMock()
    ctx.get_peer_context.return_value = None
    return ctx


def _shareable(current_round):
    """A Shareable whose only meaningful header is the current round."""
    sh = MagicMock()
    sh.get_header.side_effect = lambda key, default=None: (
        current_round if key == AppConstants.CURRENT_ROUND else default
    )
    return sh


def _dxo(data_kind=DataKind.WEIGHTS):
    # A full global model: frozen backbone keys + the trainable head keys.
    return DXO(
        data_kind=data_kind,
        data={
            "patch_embed.proj.weight": 0,
            "layers.0.blocks.0.attn.qkv.weight": 0,
            "omni_heads.0.weight": 1,
            "omni_heads.0.bias": 1,
        },
    )


class TestTrimBroadcastVars:
    def test_round_zero_broadcasts_full_model_untouched(self):
        """Round 0 must ship the full model (the backbone) — filter returns None (no change)."""
        f = TrimBroadcastVars(include_vars="omni_heads")
        assert f.process_dxo(_dxo(), _shareable(0), _ctx()) is None

    def test_later_round_keeps_only_matching_head_keys(self):
        f = TrimBroadcastVars(include_vars="omni_heads")
        out = f.process_dxo(_dxo(), _shareable(3), _ctx())
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}

    def test_does_not_mutate_the_input_dict(self):
        """Non-mutating: on the server dxo.data aliases the global model, so the filter must build a
        NEW dict rather than pop keys from the aliased one — the original dict object stays intact."""
        f = TrimBroadcastVars(include_vars="omni_heads")
        dxo = _dxo()
        original_dict = dxo.data  # the object the server's global model aliases
        original_keys = set(original_dict.keys())
        out = f.process_dxo(dxo, _shareable(3), _ctx())
        assert set(original_dict.keys()) == original_keys  # aliased global-model dict untouched
        assert out.data is not original_dict  # a new dict was produced
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}

    def test_missing_round_header_broadcasts_full_model(self):
        f = TrimBroadcastVars(include_vars="omni_heads")
        assert f.process_dxo(_dxo(), _shareable(None), _ctx()) is None

    def test_noop_when_include_vars_empty(self):
        assert TrimBroadcastVars(include_vars="").process_dxo(_dxo(), _shareable(3), _ctx()) is None
        assert TrimBroadcastVars(include_vars=None).process_dxo(_dxo(), _shareable(3), _ctx()) is None

    def test_warns_and_broadcasts_full_when_regex_matches_nothing(self):
        f = TrimBroadcastVars(include_vars="does_not_exist")
        f.log_warning = MagicMock()
        assert f.process_dxo(_dxo(), _shareable(3), _ctx()) is None
        f.log_warning.assert_called_once()

    def test_supports_weight_diff_kind_too(self):
        f = TrimBroadcastVars(include_vars="omni_heads")
        out = f.process_dxo(_dxo(DataKind.WEIGHT_DIFF), _shareable(2), _ctx())
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}


class TestTrimEvalBroadcastVars:
    """The evaluation counterpart trims the ``validate`` broadcast unconditionally (there is no
    round header on cross-site validation, so the round-gated parent would never trim it)."""

    def test_trims_when_no_round_header(self):
        f = TrimEvalBroadcastVars(include_vars="omni_heads")
        out = f.process_dxo(_dxo(), _shareable(None), _ctx())
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}

    def test_trims_even_at_round_zero(self):
        """Round 0 would pass through untouched in the round-gated parent; the eval variant still
        trims (the client already holds the backbone by validation time)."""
        f = TrimEvalBroadcastVars(include_vars="omni_heads")
        out = f.process_dxo(_dxo(), _shareable(0), _ctx())
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}

    def test_does_not_mutate_the_input_dict(self):
        f = TrimEvalBroadcastVars(include_vars="omni_heads")
        dxo = _dxo()
        original_dict = dxo.data
        original_keys = set(original_dict.keys())
        out = f.process_dxo(dxo, _shareable(None), _ctx())
        assert set(original_dict.keys()) == original_keys
        assert out.data is not original_dict

    def test_warns_and_broadcasts_full_when_regex_matches_nothing(self):
        f = TrimEvalBroadcastVars(include_vars="does_not_exist")
        f.log_warning = MagicMock()
        assert f.process_dxo(_dxo(), _shareable(None), _ctx()) is None
        f.log_warning.assert_called_once()

    def test_noop_when_include_vars_empty(self):
        assert TrimEvalBroadcastVars(include_vars="").process_dxo(_dxo(), _shareable(None), _ctx()) is None
        assert TrimEvalBroadcastVars(include_vars=None).process_dxo(_dxo(), _shareable(None), _ctx()) is None
