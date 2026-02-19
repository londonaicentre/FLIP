import numpy as np
import torch

from flip.utils.utils import convert_weights_to_diff


class TestConvertWeightsToDiff:
    def test_computes_diff_for_single_weight(self):
        global_weights = {"layer1": torch.tensor([1.0, 2.0, 3.0])}
        local_weights = {"layer1": torch.tensor([2.0, 4.0, 6.0])}

        result = convert_weights_to_diff(global_weights, local_weights)

        assert "layer1" in result
        np.testing.assert_array_almost_equal(result["layer1"], [1.0, 2.0, 3.0])

    def test_returns_numpy_arrays(self):
        global_weights = {"w": torch.tensor([1.0])}
        local_weights = {"w": torch.tensor([2.0])}

        result = convert_weights_to_diff(global_weights, local_weights)

        assert isinstance(result["w"], np.ndarray)

    def test_missing_global_weight_skipped(self, capsys):
        """Note: convert_weights_to_diff has a known bug - early return inside loop.
        It returns after processing the first weight that exists in both dicts."""
        global_weights = {}
        local_weights = {"missing_layer": torch.tensor([1.0])}

        convert_weights_to_diff(global_weights, local_weights)

        captured = capsys.readouterr()
        assert "Warning" in captured.out

    def test_detaches_and_moves_to_cpu(self):
        global_weights = {"w": torch.tensor([1.0, 2.0])}
        local_weights = {"w": torch.tensor([3.0, 4.0], requires_grad=True)}

        result = convert_weights_to_diff(global_weights, local_weights)

        assert "w" in result
        np.testing.assert_array_almost_equal(result["w"], [2.0, 2.0])

    def test_early_return_bug_returns_only_first_key(self):
        """Documents the known early-return bug: only the first matching key is processed."""
        global_weights = {"a": torch.tensor([1.0]), "b": torch.tensor([10.0])}
        local_weights = {"a": torch.tensor([2.0]), "b": torch.tensor([20.0])}

        result = convert_weights_to_diff(global_weights, local_weights)

        # Due to the bug, only one key is returned
        assert len(result) == 1
