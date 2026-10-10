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

"""Flower training and validation share the same degenerate-batch behavior."""

import importlib
import sys

import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT
from xray_batch_contract import check_batch_guards


@pytest.fixture
def task(monkeypatch):
    displaced = {name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")}
    for name in displaced:
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(TUTORIALS_ROOT / "flower/xray_classification"))
    try:
        yield importlib.import_module("app.task")
    finally:
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(displaced)


@pytest.mark.parametrize("phase", ["Train", "Val"])
@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "minus-inf"])
@pytest.mark.parametrize("all_invalid", [False, True], ids=["valid-batch-survives", "all-invalid"])
def test_batch_guards_keep_supervised_metrics(task, phase, non_finite, all_invalid, monkeypatch):
    def run_pass(model, batches, lesions, optimizer):
        if phase == "Train":
            return task.train_func(model, batches, optimizer, torch.device("cpu"), lesions)
        return task.validate_func(model, batches, torch.device("cpu"), lesions)

    check_batch_guards(task, run_pass, phase, monkeypatch, non_finite, all_invalid=all_invalid)
