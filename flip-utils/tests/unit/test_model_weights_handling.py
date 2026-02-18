from collections import OrderedDict

import numpy as np
import torch
from nvflare.apis.dxo import DataKind, MetaKey

from flip.utils.model_weights_handling import get_model_weights_diff


class TestGetModelWeightsDiff:
    def test_returns_dxo_with_weight_diff_kind(self):
        original = OrderedDict({"layer1": np.array([1.0, 2.0]), "layer2": np.array([3.0, 4.0])})
        new = OrderedDict({"layer1": np.array([1.5, 2.5]), "layer2": np.array([3.5, 4.5])})

        result = get_model_weights_diff(original, new, iterations=10)

        assert result.data_kind == DataKind.WEIGHT_DIFF

    def test_computes_correct_diff_with_numpy_arrays(self):
        original = OrderedDict({"w": np.array([1.0, 2.0, 3.0])})
        new = OrderedDict({"w": np.array([2.0, 4.0, 6.0])})

        result = get_model_weights_diff(original, new, iterations=5)

        np.testing.assert_array_almost_equal(result.data["w"], [1.0, 2.0, 3.0])

    def test_computes_correct_diff_with_torch_tensors(self):
        original = OrderedDict({"w": np.array([1.0, 2.0])})
        new = OrderedDict({"w": torch.tensor([3.0, 5.0])})

        result = get_model_weights_diff(original, new, iterations=1)

        np.testing.assert_array_almost_equal(result.data["w"], [2.0, 3.0])

    def test_sets_num_steps_meta(self):
        original = OrderedDict({"w": np.array([0.0])})
        new = OrderedDict({"w": np.array([1.0])})

        result = get_model_weights_diff(original, new, iterations=42)

        assert result.get_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND) == 42

    def test_handles_multiple_layers(self):
        original = OrderedDict({"a": np.array([0.0]), "b": np.array([10.0]), "c": np.array([100.0])})
        new = OrderedDict({"a": np.array([1.0]), "b": np.array([20.0]), "c": np.array([300.0])})

        result = get_model_weights_diff(original, new, iterations=1)

        assert set(result.data.keys()) == {"a", "b", "c"}
        np.testing.assert_array_almost_equal(result.data["a"], [1.0])
        np.testing.assert_array_almost_equal(result.data["b"], [10.0])
        np.testing.assert_array_almost_equal(result.data["c"], [200.0])

    def test_handles_zero_diff(self):
        weights = OrderedDict({"w": np.array([5.0, 10.0])})

        result = get_model_weights_diff(weights, OrderedDict({"w": np.array([5.0, 10.0])}), iterations=1)

        np.testing.assert_array_almost_equal(result.data["w"], [0.0, 0.0])
