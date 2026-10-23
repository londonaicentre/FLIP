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

"""The shared guard keeps supervision and rejects invalid scalar losses."""

from unittest.mock import Mock

import pytest
import torch

from flip.training import skip_degenerate_batch


@pytest.mark.parametrize("loss", [None, 0.0, float("nan")])
def test_fully_masked_batch_is_skipped_before_loss_check(loss):
    log = Mock()
    loss_tensor = None if loss is None else torch.tensor(loss)

    assert skip_degenerate_batch(torch.full((2, 2), -1), loss_tensor, log=log, context="Train batch 1/3")

    log.assert_called_once_with("Train batch 1/3: all labels masked (-1), skipping batch")


@pytest.mark.parametrize("labels", [[[0, 1], [1, 0]], [[-1, 1], [0, -1]]], ids=["ordinary", "partially-masked"])
@pytest.mark.parametrize("loss", [None, 0.0, 0.75])
def test_supervised_batch_with_finite_loss_is_kept(labels, loss):
    log = Mock()
    loss_tensor = None if loss is None else torch.tensor(loss)

    assert not skip_degenerate_batch(torch.tensor(labels), loss_tensor, log=log, context="Val batch 2/3")

    log.assert_not_called()


@pytest.mark.parametrize("loss", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "minus-inf"])
def test_non_finite_loss_is_skipped_with_context(loss):
    log = Mock()

    assert skip_degenerate_batch(torch.tensor([[1, -1]]), torch.tensor(loss), log=log, context="Test batch 2/3")

    log.assert_called_once_with(f"Test batch 2/3: non-finite loss ({loss}), skipping batch")
