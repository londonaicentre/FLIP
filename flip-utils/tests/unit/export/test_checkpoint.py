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

"""``flip.export.checkpoint`` reads both checkpoint shapes the way FLARE's own manager does (FLIP#1047)."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from flip.export.checkpoint import describe_checkpoint, state_dict_from

persistence_format = pytest.importorskip("nvflare.app_opt.pt.model_persistence_format_manager")

WEIGHTS = {"layer.weight": torch.ones(2, 2), "bn.num_batches_tracked": torch.tensor(3.0)}

CHECKPOINTS = {
    "bare": dict(WEIGHTS),
    "persistence": {"model": dict(WEIGHTS), "train_conf": {"epochs": 2}, "meta_props": {"round": 1}},
    "persistence-empty-conf": {"model": dict(WEIGHTS), "train_conf": {}},
}


@pytest.mark.parametrize("name", CHECKPOINTS)
def test_reads_like_flare(name: str):
    data = CHECKPOINTS[name]
    manager = persistence_format.PTModelPersistenceFormatManager(data, default_train_conf=None)

    facts = describe_checkpoint(data, Path("model.pt"))

    assert state_dict_from(data) is manager.var_dict
    assert facts.is_persistence_format == (manager.PERSISTENCE_KEY_MODEL in data)
    assert facts.train_conf == manager.train_conf
    assert facts.meta_props == manager.meta
