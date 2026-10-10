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

"""NVFLARE training, validation and cross-site evaluation use the shared guard."""

import sys
from unittest.mock import Mock

import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT, load_module
from xray_batch_contract import check_batch_guards

APP_DIR = TUTORIALS_ROOT / "nvflare/image_classification/xray_classification/app_files"


@pytest.fixture
def trainer(monkeypatch):
    # The deployed script imports siblings by bare name; isolate those from other tutorials.
    siblings = {
        name: sys.modules.pop(name)
        for name in ("data_utils", "loss_and_metrics", "models", "transforms")
        if name in sys.modules
    }
    monkeypatch.syspath_prepend(str(APP_DIR))
    try:
        yield load_module("xray_trainer_under_test", APP_DIR / "trainer.py")
    finally:
        for name in ("data_utils", "loss_and_metrics", "models", "transforms", "xray_trainer_under_test"):
            sys.modules.pop(name, None)
        sys.modules.update(siblings)


@pytest.mark.parametrize("phase", ["Train", "Val", "Test"])
@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "minus-inf"])
@pytest.mark.parametrize("all_invalid", [False, True], ids=["valid-batch-survives", "all-invalid"])
def test_batch_guards_keep_supervised_metrics(trainer, phase, non_finite, all_invalid, monkeypatch):
    def run_pass(model, batches, lesions, optimizer):
        if phase == "Test":
            writer = Mock()
            metrics = trainer.cross_site_validate(model, batches, lesions, torch.device("cpu"), writer)
            assert writer.add_scalar.call_args_list[0].args[0] == "TEST_LOSS"
            return metrics
        return trainer.epoch_loop(
            model, batches, lesions, torch.device("cpu"), optimizer if phase == "Train" else None, epoch=2, phase=phase
        )

    check_batch_guards(trainer, run_pass, phase, monkeypatch, non_finite, all_invalid=all_invalid)
