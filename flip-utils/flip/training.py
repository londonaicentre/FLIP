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

"""Batch guards shared by training and evaluation apps using ``flip-utils[full]``."""

from collections.abc import Callable

import torch


def skip_degenerate_batch(
    labels: torch.Tensor,
    loss: torch.Tensor | None = None,
    *,
    log: Callable[[str], object],
    context: str,
) -> bool:
    """Warn and skip batches without supervision or with a non-finite scalar loss.

    Call before the model forward pass to skip labels that are all ``-1``. Call
    again with the computed loss before backward or metric accumulation, so a
    NaN/Inf loss cannot poison weights or the reported average (FLIP#764).
    Partially masked batches still carry supervision and are kept.

    Args:
        labels (torch.Tensor): Batch labels; ``-1`` means unknown/unannotated.
        loss (torch.Tensor | None): Computed scalar loss, if available.
        log (Callable[[str], object]): Warning callback for the caller's logger.
        context (str): Phase and batch position to include in the warning.

    Returns:
        bool: Whether the caller should skip the batch.
    """
    if (labels == -1).all():
        log(f"{context}: all labels masked (-1), skipping batch")
        return True
    if loss is not None and not torch.isfinite(loss):
        log(f"{context}: non-finite loss ({loss.item()}), skipping batch")
        return True
    return False
