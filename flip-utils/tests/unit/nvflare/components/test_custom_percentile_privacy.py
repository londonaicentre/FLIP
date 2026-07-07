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

from unittest.mock import MagicMock

import numpy as np
from nvflare.apis.dxo import DXO, DataKind, MetaKey
from nvflare.app_common.filters.percentile_privacy import PercentilePrivacy as NVFlarePercentilePrivacy

from flip.nvflare.components.custom_percentile_privacy import PercentilePrivacy


class TestPercentilePrivacy:
    """FLIP's PercentilePrivacy is a thin subclass of stock NVFLARE's PercentilePrivacy.

    Its only FLIP-specific behaviour is the ``off`` toggle; when the filter is on it must
    delegate to stock unchanged. These tests pin that contract (and that it stays a subclass
    so it tracks upstream), not stock's percentile/gamma maths, which NVFLARE owns and tests.
    """

    def setup_method(self):
        self.fl_ctx = MagicMock()
        self.fl_ctx.get_peer_context.return_value = None
        self.shareable = MagicMock()

    @staticmethod
    def _weight_diff_dxo(data: dict, total_steps: int = 1) -> DXO:
        dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data={k: v.copy() for k, v in data.items()})
        dxo.set_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, total_steps)
        return dxo

    def test_subclasses_stock_percentile_privacy(self):
        """De-fork guard: FLIP's filter must subclass stock so it inherits upstream fixes."""
        assert issubclass(PercentilePrivacy, NVFlarePercentilePrivacy)
        assert isinstance(PercentilePrivacy(), NVFlarePercentilePrivacy)

    def test_init_default_values(self):
        f = PercentilePrivacy()
        assert f.percentile == 10
        assert f.gamma == 0.01
        assert f.off is False

    def test_init_custom_values(self):
        f = PercentilePrivacy(percentile=20, gamma=0.05, off=True)
        assert f.percentile == 20
        assert f.gamma == 0.05
        assert f.off is True

    def test_process_dxo_when_off_returns_input_untouched(self):
        """off=True short-circuits: the exact input DXO is returned, data unmodified."""
        f = PercentilePrivacy(off=True)
        weights = {"layer1": np.array([1.0, 2.0, 3.0])}
        dxo = self._weight_diff_dxo(weights)

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is dxo
        np.testing.assert_array_equal(result.data["layer1"], weights["layer1"])

    def test_process_dxo_when_on_applies_filtering(self):
        """off=False (default) actually filters — sub-cutoff magnitudes are zeroed."""
        f = PercentilePrivacy(percentile=50, gamma=1.0)
        dxo = self._weight_diff_dxo({"layer1": np.array([-0.5, -0.2, 0.1, 0.3, 0.8])})

        result = f.process_dxo(dxo, self.shareable, self.fl_ctx)

        assert result is not None
        assert np.any(result.data["layer1"] == 0.0)

    def test_process_dxo_when_on_matches_stock_output_exactly(self):
        """The whole point of the de-fork: when on, output is byte-identical to stock.

        Covers multiple layers, a scalar param, and total_steps scaling in one shot — this
        replaces the fork's re-implemented percentile/gamma unit tests, which duplicated
        NVFLARE's own coverage.
        """
        data = {
            "layer1": np.array([-0.5, -0.2, 0.1, 0.3, 0.8]),
            "layer2": np.array([[2.0, -2.0], [0.05, -0.4]]),
            "scalar": np.array(0.5),
        }
        flip_result = PercentilePrivacy(percentile=40, gamma=0.5).process_dxo(
            self._weight_diff_dxo(data, total_steps=3), self.shareable, self.fl_ctx
        )
        stock_result = NVFlarePercentilePrivacy(percentile=40, gamma=0.5).process_dxo(
            self._weight_diff_dxo(data, total_steps=3), self.shareable, self.fl_ctx
        )

        assert flip_result is not None
        assert stock_result is not None
        assert flip_result.data.keys() == stock_result.data.keys()
        for name in data:
            np.testing.assert_array_equal(flip_result.data[name], stock_result.data[name])

    def test_process_dxo_when_on_returns_none_for_invalid_gamma(self):
        """Delegation preserves stock's guard clauses (gamma <= 0 -> None)."""
        f = PercentilePrivacy(gamma=-0.01)
        dxo = self._weight_diff_dxo({"layer1": np.array([1.0, 2.0, 3.0])})

        assert f.process_dxo(dxo, self.shareable, self.fl_ctx) is None
