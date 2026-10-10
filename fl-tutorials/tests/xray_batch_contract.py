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

"""Behavioral contract for the X-ray trainers, using a tiny CPU model and real BCE."""

from logging import WARNING
from unittest.mock import Mock

import numpy as np
import pytest
import torch
from flip.training import skip_degenerate_batch


def check_batch_guards(module, run_pass, phase, monkeypatch, non_finite, *, all_invalid=False):
    """Exercise the shipped loop: masked, invalid-loss, then partly supervised batch."""
    model = torch.nn.Linear(1, 2)
    with torch.no_grad():
        model.weight.fill_(0.2)
        model.bias.fill_(0.3)
    initial = {name: value.clone() for name, value in model.state_dict().items()}
    grad_modes = []
    forward = model.forward

    def forward_recording(images):
        grad_modes.append(torch.is_grad_enabled())
        return forward(images)

    model.forward = Mock(side_effect=forward_recording)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    monkeypatch.setattr(optimizer, "zero_grad", Mock(wraps=optimizer.zero_grad))
    monkeypatch.setattr(optimizer, "step", Mock(wraps=optimizer.step))
    clip = Mock(wraps=torch.nn.utils.clip_grad_norm_)
    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", clip)
    lesions = module.LesionDict(items=[{"id": 0, "lesion": "A"}, {"id": 1, "lesion": "B"}])
    masked = {"image": torch.ones(2, 1), "A": torch.tensor([-1, -1]), "B": torch.tensor([-1, -1])}
    supervised = {"image": torch.ones(2, 1), "A": torch.tensor([1, 0]), "B": torch.tensor([-1, 1])}
    batches = [masked, supervised, masked if all_invalid else supervised]
    bce_loss = module.get_bce_loss
    good_losses = []

    def loss_with_bad_first_batch(output, labels):
        loss = bce_loss(output, labels)
        if loss_mock.call_count == 1:
            bad_loss = loss * non_finite
            if bad_loss.requires_grad:
                bad_loss.register_hook(lambda gradient: pytest.fail("non-finite loss reached backward"))
            return bad_loss
        good_losses.append(loss.item())
        return loss

    loss_mock = Mock(side_effect=loss_with_bad_first_batch)
    monkeypatch.setattr(module, "get_bce_loss", loss_mock)
    # Keep the real helper's decisions while verifying every phase delegates to it.
    guard = Mock(wraps=skip_degenerate_batch)
    monkeypatch.setattr(module, "skip_degenerate_batch", guard)
    if hasattr(module, "log"):
        warning_log = Mock()
        monkeypatch.setattr(module, "log", warning_log)
    else:
        warning_log = Mock()
        monkeypatch.setattr(module.logger, "warning", warning_log)

    metrics = run_pass(model, batches, lesions, optimizer)

    expected_forwards = 1 if all_invalid else 2
    assert model.forward.call_count == expected_forwards, "fully masked batches reached the model"
    assert loss_mock.call_count == expected_forwards
    assert guard.call_count == len(batches) + expected_forwards
    is_train = phase == "Train"
    assert grad_modes == [is_train] * expected_forwards
    assert optimizer.zero_grad.call_count == (expected_forwards if is_train else 0)
    expected_updates = int(is_train and not all_invalid)
    assert optimizer.step.call_count == expected_updates
    assert clip.call_count == expected_updates
    if expected_updates:
        assert clip.call_args.kwargs == {"max_norm": 1.0}
    for name, value in model.state_dict().items():
        assert torch.isfinite(value).all()
        assert torch.equal(value, initial[name]) == (expected_updates == 0)

    if isinstance(metrics["loss"], list):
        assert metrics["loss"] == good_losses
        for kind in ("precision", "recall", "f1-score"):
            assert all(len(values) == len(good_losses) for values in metrics[kind].values())
    elif all_invalid:
        assert all(np.isnan(value) for value in metrics.values())
    else:
        assert metrics["loss"] == good_losses[0]
        assert all(np.isfinite(value) for value in metrics.values())

    if hasattr(module, "log"):
        warnings = [call.args[1] for call in warning_log.call_args_list if call.args[0] == WARNING]
    else:
        warnings = [call.args[0] for call in warning_log.call_args_list]
    assert len(warnings) == (3 if all_invalid else 2)
    assert f"{phase} batch 1/3: all labels masked (-1), skipping batch" in warnings[0]
    assert f"{phase} batch 2/3: non-finite loss ({non_finite}), skipping batch" in warnings[1]
