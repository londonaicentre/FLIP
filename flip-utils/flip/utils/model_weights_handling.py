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


#NOTE: This is the modified file from NVFlare.

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

import torch
from nvflare.apis.dxo import DXO, DataKind, MetaKey

if TYPE_CHECKING:
    pass


def get_model_weights_diff(original_weights: OrderedDict, new_weights: OrderedDict, iterations: int) -> DXO:
    """Compute the weights differences to send a weight update.

    Args:
        original_weights (OrderedDict): weights coming from the server (before training)
        new_weights (OrderedDict): weights coming out of the server (post training)
        iterations (int): number of iterations

    Returns:
        DXO: DXO containing the weight updates for the server.
    """

    # Build the diff one tensor at a time. Converting the whole model to a second
    # ``{name: ndarray}`` dict before subtracting holds an extra full copy of the
    # model in RAM; for large models (hundreds of MB) that spike, multiplied across
    # the clients sharing a simulator process, is enough to drive the box into swap.
    # Converting and subtracting per-key lets each transient array be freed
    # immediately, so the peak is the incoming weights plus the diff being built.
    #
    # Note: on CPU ``tensor.cpu().numpy()`` may alias the model parameter's storage,
    # so we never mutate ``new_arr`` in place — the subtraction allocates a fresh array.
    weight_diff = {}
    for k, v in new_weights.items():
        new_arr = v.cpu().numpy() if isinstance(v, torch.Tensor) else v
        weight_diff[k] = new_arr - original_weights[k]
        del new_arr

    outgoing_dxo = DXO(
        data_kind=DataKind.WEIGHT_DIFF,
        data=weight_diff,
        meta={MetaKey.NUM_STEPS_CURRENT_ROUND: iterations},
    )

    return outgoing_dxo
