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
# Copied verbatim from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/metrics/utils.py,
# flattened into this tutorial's directory and renamed utils.py -> metrics_utils.py
# so the flat layout keeps a unique module name.

import math

import numpy as np
import torch


class EarlyStopping:
    """Early stops training if a monitored validation loss doesn't improve after a given patience.

    Attributes:
        patience (float): Number of consecutive non-improving calls to wait before stopping.
        verbose (bool): If True, prints a message when the checkpoint is saved.
        counter (int): Number of consecutive calls since the last improvement.
        best_score (Optional[float]): Best (lowest) validation loss seen so far.
        early_stop (bool): Set to True once patience is exceeded.
        val_loss_min (float): Lowest validation loss recorded, updated via save_checkpoint.
    """

    def __init__(self, patience: float = 7, verbose: bool = False) -> None:
        """
        Early stops the training if validation loss doesn't improve after a given patience.

        Args:
            patience (float): How long to wait after last time validation loss improved.
                            Default: 7
            verbose (bool): If True, prints a message for each validation loss improvement.
                            Default: False
        """
        self.patience = patience
        self.verbose = verbose
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.val_loss_min = np.Inf

    def __call__(self, val_loss: float, model: torch.nn.Module) -> None:
        """
        Args:
            val_loss (float): Validation loss value
            model (torch.nn.Module): Model to save. Currently not used
        """
        score = val_loss

        if self.best_score is None:
            self.best_score = score
        elif score > self.best_score:
            self.counter += 1
            print(f"EarlyStopping counter: {self.counter} out of {self.patience}")
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = score
            self.counter = 0

    def save_checkpoint(self, val_loss: float, model: torch.nn.Module) -> None:
        """
        Saves model when validation loss decrease.

        Args:
            val_loss (float): Validation loss value
            model (torch.nn.Module): Model to save
        """
        if self.verbose:
            print(
                f"Validation loss decreased ({self.val_loss_min:.6f} --> {val_loss:.6f}).  Saving model ..."
            )
        torch.save(model.state_dict(), "checkpoint.pt")
        self.val_loss_min = val_loss


class AverageMeter(object):
    """Computes and stores the average and current value, ignoring NaN updates.

    Attributes:
        val (torch.Tensor): Most recently added value (a tensor at reset, a Python float after update() is called).
        avg (torch.Tensor): Running average of all values added so far, excluding NaNs (a tensor at reset, a Python float after update() is called).
        sum (torch.Tensor): Running sum of all values added so far, excluding NaNs.
        count (torch.Tensor): Running count of samples represented by non-NaN updates.
        nan_count (torch.Tensor): Number of times update() was called with a NaN value.
    """

    def __init__(self) -> None:
        """Computes and stores the average and current value."""
        self.reset()

    def reset(self) -> None:
        """Resets all statistics."""
        self.val = torch.tensor(0.0).cuda()
        self.avg = torch.tensor(0.0).cuda()
        self.sum = torch.tensor(0.0).cuda()
        self.count = torch.tensor(0).cuda()
        self.nan_count = torch.tensor(0).cuda()

    def update(self, val: torch.Tensor, n: int = 1) -> None:
        """Updates the meter with the new value.

        Args:
            val (torch.Tensor): The new value to add.
            n (int, optional): The number of samples represented by the new value. Defaults to 1.
        """
        val = val.item()
        self.val = val

        if math.isnan(val):
            self.nan_count += 1

        # sum only if the value is not NaN
        if not math.isnan(val):
            self.sum += val * n
            self.count += n

        if self.count != 0:
            self.avg = (self.sum / self.count).detach().cpu().item()
        else:
            self.avg = torch.tensor(0.0).detach().cpu().item()
