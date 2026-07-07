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

from flip.nvflare.components.keep_vars_filter import KeepOnlyVars


def _ctx():
    # NVFLARE's logging path validates get_peer_context() is an FLContext or None.
    ctx = MagicMock()
    ctx.get_peer_context.return_value = None
    return ctx


def _dxo(data_kind=DataKind.WEIGHT_DIFF):
    # A frozen-backbone fine-tune diff: backbone keys + the trainable head keys.
    return DXO(
        data_kind=data_kind,
        data={
            "patch_embed.proj.weight": 0,
            "layers.0.blocks.0.attn.qkv.weight": 0,
            "omni_heads.0.weight": 1,
            "omni_heads.0.bias": 1,
        },
    )


class TestKeepOnlyVars:
    def test_keeps_only_matching_head_keys(self):
        f = KeepOnlyVars(include_vars="omni_heads")
        dxo = _dxo()
        out = f.process_dxo(dxo, MagicMock(), _ctx())
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}

    def test_noop_when_include_vars_empty(self):
        """Empty/None regex → filter is skipped (returns None, DXO unchanged)."""
        assert KeepOnlyVars(include_vars="").process_dxo(_dxo(), MagicMock(), _ctx()) is None
        assert KeepOnlyVars(include_vars=None).process_dxo(_dxo(), MagicMock(), _ctx()) is None

    def test_warns_and_drops_all_when_no_match(self):
        f = KeepOnlyVars(include_vars="does_not_exist")
        f.log_warning = MagicMock()
        out = f.process_dxo(_dxo(), MagicMock(), _ctx())
        assert out.data == {}
        f.log_warning.assert_called_once()

    def test_supports_weights_kind_too(self):
        f = KeepOnlyVars(include_vars="omni_heads")
        out = f.process_dxo(_dxo(DataKind.WEIGHTS), MagicMock(), _ctx())
        assert set(out.data.keys()) == {"omni_heads.0.weight", "omni_heads.0.bias"}
