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

import numpy as np
import torch


def check_batch_guards(module, run_pass, phase, monkeypatch, caplog, *, all_invalid=False):
    """Exercise the shipped loop: masked, invalid-loss, then partly supervised batch."""
    model = torch.nn.Linear(1, 2)
    with torch.no_grad():
        model.weight.fill_(0.2)
        model.bias.fill_(0.3)
    initial = {name: value.clone() for name, value in model.state_dict().items()}

    def reject_masked_images(_model, inputs):
        assert torch.equal(inputs[0], torch.ones(2, 1)), "fully masked batches reached the model"

    model.register_forward_pre_hook(reject_masked_images)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    lesions = module.LesionDict(items=[{"id": 0, "lesion": "A"}, {"id": 1, "lesion": "B"}])
    masked = {"image": torch.full((2, 1), 2.0), "A": torch.tensor([-1, -1]), "B": torch.tensor([-1, -1])}
    supervised = {"image": torch.ones(2, 1), "A": torch.tensor([1, 0]), "B": torch.tensor([-1, 1])}
    batches = [masked, supervised, masked if all_invalid else supervised]
    bce_loss = module.get_bce_loss
    good_losses = []
    first_loss = True

    def loss_with_bad_first_batch(output, labels):
        nonlocal first_loss
        loss = bce_loss(output, labels)
        if first_loss:
            first_loss = False
            return loss * float("nan")
        good_losses.append(loss.item())
        return loss

    monkeypatch.setattr(module, "get_bce_loss", loss_with_bad_first_batch)
    caplog.set_level(WARNING)

    metrics = run_pass(model, batches, lesions, optimizer)

    trained = phase == "Train" and not all_invalid
    for name, value in model.state_dict().items():
        assert torch.isfinite(value).all()
        assert torch.equal(value, initial[name]) == (not trained)

    if isinstance(metrics["loss"], list):
        assert metrics["loss"] == good_losses
        for kind in ("precision", "recall", "f1-score"):
            assert all(len(values) == len(good_losses) for values in metrics[kind].values())
    elif all_invalid:
        assert all(np.isnan(value) for value in metrics.values())
    else:
        assert metrics["loss"] == good_losses[0]
        assert all(np.isfinite(value) for value in metrics.values())

    warnings = [record.message for record in caplog.records if record.levelno == WARNING]
    assert f"{phase} batch 1/3: all labels masked (-1), skipping batch" in warnings
    assert f"{phase} batch 2/3: non-finite loss (nan), skipping batch" in warnings
