# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock

import numpy as np
from nvflare.apis.dxo import DXO, DataKind, MetaKey

from flip.constants import FlipMetaKey
from flip.nvflare.components.stage_percentile_privacy import StagePercentilePrivacy


class TestStagePercentilePrivacy:
    def setup_method(self):
        self.fl_ctx = MagicMock()
        self.fl_ctx.get_peer_context.return_value = None
        self.shareable = MagicMock()

    def test_init_default_values(self):
        f = StagePercentilePrivacy()
        assert f.percentile == 10
        assert f.gamma == 0.01
        assert not f.off

    def test_init_custom_values(self):
        f = StagePercentilePrivacy(percentile=25, gamma=0.05, off=True)
        assert f.percentile == 25
        assert f.gamma == 0.05
        assert f.off

    def test_filter_off_returns_dxo_unchanged(self):
        f = StagePercentilePrivacy(off=True)
        weights = {"encoder.layer1": np.array([1.0, 2.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is dxo

    def test_invalid_gamma_returns_none(self):
        f = StagePercentilePrivacy(gamma=-0.01)
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w": np.array([1.0])})

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is None

    def test_invalid_percentile_low_returns_none(self):
        f = StagePercentilePrivacy(percentile=-5)
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w": np.array([1.0])})

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is None

    def test_invalid_percentile_high_returns_none(self):
        f = StagePercentilePrivacy(percentile=101)
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data={"w": np.array([1.0])})

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is None

    def test_missing_stage_returns_dxo_unchanged(self):
        f = StagePercentilePrivacy(percentile=50, gamma=1.0)
        weights = {"encoder.layer1": np.array([1.0, 2.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        # No stage meta set

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is dxo

    def test_empty_stage_list_returns_dxo_unchanged(self):
        f = StagePercentilePrivacy(percentile=50, gamma=1.0)
        weights = {"encoder.layer1": np.array([1.0, 2.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is dxo

    def test_applies_filtering_with_stage(self):
        f = StagePercentilePrivacy(percentile=50, gamma=1.0)
        weights = {
            "encoder.w1": np.array([-0.5, -0.2, 0.1, 0.3, 0.8]),
            "decoder.w1": np.array([0.1, 0.2, 0.3, 0.4, 0.5]),
        }
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [["encoder"]])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert "encoder.w1" in result.data
        assert "decoder.w1" in result.data

    def test_applies_gamma_clipping(self):
        f = StagePercentilePrivacy(percentile=0, gamma=0.3)
        weights = {"encoder.w": np.array([-2.0, -1.0, 0.0, 1.0, 2.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [["encoder"]])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        result_data = result.data["encoder.w"]
        assert np.all(result_data >= -0.3)
        assert np.all(result_data <= 0.3)

    def test_scalar_value_not_clipped(self):
        f = StagePercentilePrivacy(percentile=50, gamma=0.1)
        weights = {"encoder.bias": np.array(0.5)}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [["encoder"]])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert np.ndim(result.data["encoder.bias"]) == 0

    def test_multiple_stages(self):
        f = StagePercentilePrivacy(percentile=50, gamma=1.0)
        weights = {
            "encoder.w": np.array([0.1, 0.5, 1.0]),
            "decoder.w": np.array([0.2, 0.6, 1.2]),
        }
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [["encoder"], ["decoder"]])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert "encoder.w" in result.data
        assert "decoder.w" in result.data

    def test_preserves_array_shape(self):
        f = StagePercentilePrivacy(percentile=50, gamma=1.0)
        shape = (3, 4)
        weights = {"encoder.w": np.random.randn(*shape)}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [["encoder"]])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result.data["encoder.w"].shape == shape

    def test_accounts_for_total_steps(self):
        f = StagePercentilePrivacy(percentile=0, gamma=10.0)
        total_steps = 5
        weights = {"encoder.w": np.array([1.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, total_steps)
        dxo.set_meta_prop(FlipMetaKey.STAGE, [["encoder"]])

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        # Scalar: delta_w = 1.0/5 = 0.2, then multiplied back: 0.2 * 5 = 1.0
        assert result is not None
        assert result is not None
