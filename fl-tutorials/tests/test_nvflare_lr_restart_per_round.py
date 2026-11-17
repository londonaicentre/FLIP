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

"""The NVFLARE EHR trainer restarts its learning-rate decay every global round (FLIP#1834).

A FLARE client process lives for the whole job, so a scheduler built once keeps decaying across
rounds and every round after the first trains at a vanishing rate. ``local_train`` is what runs per
round, so the schedule is pinned there: each call runs ``LR_START`` -> ``LR_END`` over its local
epochs, on the optimizer the caller keeps. The Flower twin has the same schedule by construction —
it builds the optimizer and scheduler inside ``train()``.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import Mock

import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT, load_module

APP_DIR = TUTORIALS_ROOT / "nvflare/tabular_classification/ehr_risk_prediction/app_files"
SIBLINGS = ("feature_engineering", "models")
MODULE_NAME = "ehr_trainer_under_test"


@pytest.fixture
def trainer(monkeypatch):
    # The deployed script imports siblings by bare name; isolate those from other tutorials.
    saved = {name: sys.modules.pop(name) for name in SIBLINGS if name in sys.modules}
    monkeypatch.syspath_prepend(str(APP_DIR))
    try:
        yield load_module(MODULE_NAME, APP_DIR / "trainer.py")
    finally:
        for name in (*SIBLINGS, MODULE_NAME):
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_every_round_decays_from_lr_start_to_lr_end(trainer, monkeypatch) -> None:
    config = json.loads((APP_DIR / "config.json").read_text())
    lr_start, lr_end, epochs = config["LR_START"], config["LR_END"], config["LOCAL_ROUNDS"]
    optimizer = torch.optim.Adam(torch.nn.Linear(1, 1).parameters(), lr=lr_start)
    training_lrs: list[float] = []

    def fake_run_epoch(model, loader, device, criterion, optimizer=None):
        if optimizer is not None:  # the training pass; the validation pass carries no optimizer
            training_lrs.append(optimizer.param_groups[0]["lr"])
            optimizer.step()
        return {"loss": 0.0, "auroc": 0.5, "accuracy": 0.5}

    monkeypatch.setattr(trainer, "run_epoch", fake_run_epoch)

    loaders = {"train": [None], "val": [None]}
    for global_round in range(3):
        trainer.local_train(
            torch.nn.Linear(1, 1),
            loaders,
            optimizer,
            lr_start,
            lr_end,
            criterion=None,
            val_criterion=None,
            device=torch.device("cpu"),
            epochs=epochs,
            writer=Mock(),
            global_round=global_round,
        )
        assert optimizer.param_groups[0]["lr"] == pytest.approx(lr_end)

    one_round = [lr_start * (lr_end / lr_start) ** (epoch / epochs) for epoch in range(epochs)]
    assert training_lrs == pytest.approx(one_round * 3)
