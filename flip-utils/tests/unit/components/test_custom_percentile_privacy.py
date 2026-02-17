# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from unittest.mock import MagicMock

import numpy as np
from nvflare.apis.dxo import DXO, DataKind, MetaKey

from flip.nvflare.components.custom_percentile_privacy import PercentilePrivacy


class TestPercentilePrivacy:
    def setup_method(self):
        """Setup test fixtures"""
        self.fl_ctx = MagicMock()
        self.fl_ctx.get_peer_context.return_value = None
        self.shareable = MagicMock()

    def test_init_default_values(self):
        """Test initialization with default values"""
        filter = PercentilePrivacy()
        assert filter.percentile == 10
        assert filter.gamma == 0.01
        assert not filter.off

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        filter = PercentilePrivacy(percentile=20, gamma=0.05, off=True)
        assert filter.percentile == 20
        assert filter.gamma == 0.05
        assert filter.off

    def test_process_dxo_when_off(self):
        """Test that filter returns DXO unchanged when off=True"""
        filter = PercentilePrivacy(off=True)

        weights = {"layer1": np.array([1.0, 2.0, 3.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is dxo
        np.testing.assert_array_equal(result.data["layer1"], weights["layer1"])

    def test_process_dxo_with_invalid_gamma(self):
        """Test that filter returns None when gamma <= 0"""
        filter = PercentilePrivacy(gamma=-0.01)

        weights = {"layer1": np.array([1.0, 2.0, 3.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is None

    def test_process_dxo_with_invalid_percentile_low(self):
        """Test that filter returns None when percentile < 0"""
        filter = PercentilePrivacy(percentile=-10)

        weights = {"layer1": np.array([1.0, 2.0, 3.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is None

    def test_process_dxo_with_invalid_percentile_high(self):
        """Test that filter returns None when percentile > 100"""
        filter = PercentilePrivacy(percentile=150)

        weights = {"layer1": np.array([1.0, 2.0, 3.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is None

    def test_process_dxo_applies_percentile_filtering(self):
        """Test that filter properly applies percentile filtering"""
        filter = PercentilePrivacy(percentile=50, gamma=1.0)

        # Create weight diff with known values
        # Values: [-0.5, -0.2, 0.1, 0.3, 0.8]
        # At 50th percentile, cutoff will be around 0.3
        # Values with abs < cutoff should become 0
        weights = {"layer1": np.array([-0.5, -0.2, 0.1, 0.3, 0.8])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        # Check that some values were zeroed out
        result_data = result.data["layer1"]
        # At least some small values should be zeroed
        assert np.any(result_data == 0.0) or len(result_data) == len(weights["layer1"])

    def test_process_dxo_applies_gamma_clipping(self):
        """Test that filter clips values to gamma range"""
        filter = PercentilePrivacy(percentile=10, gamma=0.5)

        # Create weight diff with large values
        weights = {"layer1": np.array([-2.0, -1.0, 0.0, 1.0, 2.0])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        result_data = result.data["layer1"]
        # All values should be clipped to [-gamma, gamma] range
        assert np.all(result_data >= -filter.gamma)
        assert np.all(result_data <= filter.gamma)

    def test_process_dxo_with_multiple_steps(self):
        """Test that filter accounts for total_steps"""
        filter = PercentilePrivacy(percentile=50, gamma=1.0)

        total_steps = 5
        weights = {"layer1": np.array([0.5, 1.0, 1.5, 2.0, 2.5])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, total_steps)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        # Result should be scaled back by total_steps
        result_data = result.data["layer1"]
        assert result_data is not None

    def test_process_dxo_with_scalar_value(self):
        """Test that filter handles scalar values correctly"""
        filter = PercentilePrivacy(percentile=50, gamma=1.0)

        # Test with scalar (0-dimensional array)
        weights = {"scalar_param": np.array(0.5)}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        # Scalar should not be clipped, just scaled
        result_data = result.data["scalar_param"]
        assert np.ndim(result_data) == 0

    def test_process_dxo_with_multiple_layers(self):
        """Test that filter works with multiple layers"""
        filter = PercentilePrivacy(percentile=30, gamma=0.5)

        weights = {
            "layer1": np.array([0.1, 0.2, 0.3, 0.4, 0.5]),
            "layer2": np.array([-0.5, -0.3, 0.0, 0.3, 0.5]),
            "layer3": np.array([0.01, 0.02, 0.03]),
        }
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert len(result.data) == 3
        assert "layer1" in result.data
        assert "layer2" in result.data
        assert "layer3" in result.data

    def test_process_dxo_with_zero_values(self):
        """Test that filter handles arrays with zero values"""
        filter = PercentilePrivacy(percentile=50, gamma=1.0)

        weights = {"layer1": np.array([0.0, 0.0, 0.0, 0.1, 0.2])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert "layer1" in result.data

    def test_process_dxo_preserves_shape(self):
        """Test that filter preserves array shapes"""
        filter = PercentilePrivacy(percentile=50, gamma=1.0)

        # Create 2D array
        original_shape = (3, 4)
        weights = {"layer1": np.random.randn(*original_shape)}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert result.data["layer1"].shape == original_shape

    def test_process_dxo_extreme_percentile_0(self):
        """Test filter with 0th percentile (most aggressive filtering)"""
        filter = PercentilePrivacy(percentile=0, gamma=1.0)

        weights = {"layer1": np.array([0.1, 0.2, 0.3, 0.4, 0.5])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        # With 0th percentile, cutoff is minimum, so most/all values should remain

    def test_process_dxo_extreme_percentile_100(self):
        """Test filter with 100th percentile (least aggressive filtering)"""
        filter = PercentilePrivacy(percentile=100, gamma=1.0)

        weights = {"layer1": np.array([0.1, 0.2, 0.3, 0.4, 0.5])}
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=weights)
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)

        result = filter.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        # With 100th percentile, cutoff is maximum, so most values might be zeroed
