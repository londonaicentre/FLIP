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

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

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

    import torch

    first_key = next(iter(new_weights))
    first_val = new_weights[first_key]
    if isinstance(first_val, torch.Tensor):
        new_weights_dict = {k: v.cpu().numpy() for k, v in new_weights.items()}
    else:
        new_weights_dict = dict(new_weights)

    weight_diff = {}
    for k in new_weights_dict.keys():
        weight_diff[k] = new_weights_dict[k] - original_weights[k]

    outgoing_dxo = DXO(
        data_kind=DataKind.WEIGHT_DIFF,
        data=weight_diff,
        meta={MetaKey.NUM_STEPS_CURRENT_ROUND: iterations},
    )

    return outgoing_dxo
