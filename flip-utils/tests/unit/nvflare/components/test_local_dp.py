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
"""Unit tests for the NVFLARE local ``(epsilon, delta)`` differential-privacy filter (FLIP#1145).

The refusal tests matter as much as the arithmetic ones: every path that cannot privatise an update
must raise rather than forward it, because forwarding means a raw update leaves the trust.
"""

import math

import numpy as np
import pytest
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable

from flip.nvflare.components.local_dp import LocalDifferentialPrivacy


def _diff(**arrays: np.ndarray) -> DXO:
    return DXO(data_kind=DataKind.WEIGHT_DIFF, data=dict(arrays))


def _run(filt: LocalDifferentialPrivacy, dxo: DXO) -> DXO:
    return filt.process_dxo(dxo, Shareable(), FLContext())


@pytest.fixture
def update() -> dict[str, np.ndarray]:
    """A float update well outside the clipping norm, plus an integer step counter."""
    rng = np.random.default_rng(0)
    return {
        "layer.weight": rng.normal(0, 0.5, (40, 8)).astype(np.float32),
        "layer.bias": rng.normal(0, 0.5, (40,)).astype(np.float32),
        "bn.num_batches_tracked": np.array([7], dtype=np.int64),
    }


class TestMechanism:
    def test_clipping_bounds_the_norm_and_preserves_direction(self, update):
        """FlatClip scales the whole update by one factor, so only its length changes."""
        original = np.concatenate([update["layer.weight"].ravel(), update["layer.bias"].ravel()])
        # sensitivity=0 isolates the clipping step: a zero stddev means no noise is added.
        out = _run(LocalDifferentialPrivacy(clipping_norm=1.0, sensitivity=0.0), _diff(**update))

        clipped = np.concatenate([out.data["layer.weight"].ravel(), out.data["layer.bias"].ravel()])
        assert np.linalg.norm(original) > 1.0, "fixture must start outside the bound to test clipping"
        assert np.linalg.norm(clipped) == pytest.approx(1.0, rel=1e-5)
        cosine = float(original @ clipped / (np.linalg.norm(original) * np.linalg.norm(clipped)))
        assert cosine == pytest.approx(1.0, rel=1e-6)

    def test_an_update_inside_the_bound_is_not_scaled(self):
        """min(1, C/norm) leaves a short update alone; only noise should move it."""
        small = np.full((10,), 0.01, dtype=np.float32)
        out = _run(LocalDifferentialPrivacy(clipping_norm=1.0, sensitivity=0.0), _diff(w=small))
        np.testing.assert_allclose(out.data["w"], small)

    def test_noise_is_added_and_scales_with_the_budget(self, update):
        """A smaller epsilon means more noise: the mechanism is not clipping alone."""
        np.random.seed(0)
        loose = _run(LocalDifferentialPrivacy(sensitivity=1e-2, epsilon=10.0), _diff(**update))
        np.random.seed(0)
        tight = _run(LocalDifferentialPrivacy(sensitivity=1e-2, epsilon=0.1), _diff(**update))

        clipped_only = _run(
            LocalDifferentialPrivacy(sensitivity=0.0), _diff(**{k: v.copy() for k, v in update.items()})
        )
        loose_delta = float(np.abs(loose.data["layer.weight"] - clipped_only.data["layer.weight"]).mean())
        tight_delta = float(np.abs(tight.data["layer.weight"] - clipped_only.data["layer.weight"]).mean())
        assert loose_delta > 0.0
        assert tight_delta > loose_delta

    def test_integer_counters_pass_through_untouched(self, update):
        out = _run(LocalDifferentialPrivacy(), _diff(**update))
        np.testing.assert_array_equal(out.data["bn.num_batches_tracked"], np.array([7], dtype=np.int64))
        assert out.data["bn.num_batches_tracked"].dtype == np.int64

    def test_float32_stays_float32(self, update):
        """Noise is cast to the array's dtype, so the payload does not silently double in size."""
        out = _run(LocalDifferentialPrivacy(), _diff(**update))
        assert out.data["layer.weight"].dtype == np.float32

    def test_noise_stddev_is_the_analytic_gaussian_mechanism(self):
        filt = LocalDifferentialPrivacy(sensitivity=1e-4, epsilon=10.0, delta=1e-5)
        assert filt.noise_stddev == pytest.approx(1e-4 * math.sqrt(2 * math.log(1.25 / 1e-5)) / 10.0)


class TestRefusals:
    """Every path that cannot privatise must raise; forwarding would release a raw update."""

    def test_a_weights_result_is_refused(self, update):
        dxo = DXO(data_kind=DataKind.WEIGHTS, data={"layer.weight": update["layer.weight"]})
        with pytest.raises(ValueError, match="weight diff"):
            _run(LocalDifferentialPrivacy(), dxo)

    def test_an_unclassifiable_dtype_is_refused(self):
        """A bool mask or complex weight may carry learned values; dtype alone cannot clear it."""
        with pytest.raises(ValueError, match="cannot classify"):
            _run(LocalDifferentialPrivacy(), _diff(w=np.ones((4,), dtype=np.float32), mask=np.ones((4,), dtype=bool)))

    def test_an_update_with_no_float_arrays_is_refused(self):
        with pytest.raises(ValueError, match="no floating-point arrays"):
            _run(LocalDifferentialPrivacy(), _diff(counter=np.array([1], dtype=np.int64)))

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"clipping_norm": 0.0}, "clipping_norm"),
            ({"clipping_norm": -1.0}, "clipping_norm"),
            ({"clipping_norm": math.inf}, "clipping_norm"),
            ({"clipping_norm": math.nan}, "clipping_norm"),
            ({"sensitivity": -1e-9}, "sensitivity"),
            ({"sensitivity": math.nan}, "sensitivity"),
            ({"epsilon": 0.0}, "epsilon"),
            ({"epsilon": math.inf}, "epsilon"),
            ({"epsilon": math.nan}, "epsilon"),
            ({"delta": 0.0}, "delta"),
            ({"delta": 1.0}, "delta"),
            ({"delta": math.nan}, "delta"),
        ],
    )
    def test_invalid_parameters_raise_at_construction(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            LocalDifferentialPrivacy(**kwargs)


class TestOffToggle:
    def test_off_is_a_pass_through(self, update):
        """DP-on and DP-off runs use an identical job, as PercentilePrivacy's `off` allows."""
        out = _run(LocalDifferentialPrivacy(off=True), _diff(**update))
        np.testing.assert_array_equal(out.data["layer.weight"], update["layer.weight"])

    def test_off_does_not_refuse_a_weights_result(self, update):
        """Off means off: the filter is inert, not a validator."""
        dxo = DXO(data_kind=DataKind.WEIGHTS, data={"layer.weight": update["layer.weight"]})
        assert _run(LocalDifferentialPrivacy(off=True), dxo) is dxo


class TestFlowerParity:
    """The two backends must implement the same mechanism, not merely similar ones.

    ``flwr`` is a dev-group dependency of flip-utils and deliberately not a runtime one, so the
    NVFLARE filter reimplements the arithmetic rather than importing it. These tests are what stop
    the two copies drifting: they run wherever ``flwr`` is installed, which includes CI.
    """

    def test_noise_stddev_matches_the_flower_config(self):
        pytest.importorskip("flwr")
        from flip.flower.privacy import LocalDpConfig

        for sensitivity, epsilon, delta in [(1e-4, 10.0, 1e-5), (1e-2, 0.5, 1e-6)]:
            flower = LocalDpConfig(sensitivity=sensitivity, epsilon=epsilon, delta=delta)
            nvflare = LocalDifferentialPrivacy(sensitivity=sensitivity, epsilon=epsilon, delta=delta)
            assert nvflare.noise_stddev == flower.noise_stddev

    def test_defaults_match_the_flower_defaults(self):
        pytest.importorskip("flwr")
        from flip.flower.privacy import LocalDpConfig

        flower, nvflare = LocalDpConfig(), LocalDifferentialPrivacy()
        assert (nvflare.clipping_norm, nvflare.sensitivity, nvflare.epsilon, nvflare.delta) == (
            flower.clipping_norm,
            flower.sensitivity,
            flower.epsilon,
            flower.delta,
        )

    def test_clip_and_noise_agree_with_flowers_helpers_element_for_element(self, update):
        pytest.importorskip("flwr")
        from flwr.supercore.differential_privacy import add_gaussian_noise_inplace, clip_inputs_inplace

        filt = LocalDifferentialPrivacy(clipping_norm=1.0, sensitivity=1e-2, epsilon=1.0)
        floats = ["layer.weight", "layer.bias"]

        np.random.seed(1234)
        ours = _run(filt, _diff(**{k: v.copy() for k, v in update.items()}))

        np.random.seed(1234)
        theirs = [update[name].copy() for name in floats]
        clip_inputs_inplace(theirs, filt.clipping_norm)
        add_gaussian_noise_inplace(theirs, filt.noise_stddev)

        for name, expected in zip(floats, theirs, strict=True):
            np.testing.assert_array_equal(ours.data[name], expected)
