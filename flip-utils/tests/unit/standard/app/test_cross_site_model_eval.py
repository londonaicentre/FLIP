# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from pathlib import Path
from unittest.mock import Mock

import pytest
from nvflare.apis.dxo import DXO, DataKind  # noqa: E402

from flip.nvflare.controllers import CrossSiteModelEval  # noqa: E402


@pytest.mark.parametrize(
    ("name", "data_kind", "data", "meta"),
    [
        (
            "metrics_sample",
            DataKind.METRICS,
            {"val_acc": 0.007118524517863989},
            {},
        )
    ],
)
def test_save_and_load_validation_content(tmp_path, name, data_kind, data, meta, monkeypatch):
    # Arrange: controller instance with a valid UUID (required by __init__)
    controller = CrossSiteModelEval(model_id="123e4567-e89b-12d3-a456-426614174000")

    # ---- 🧩 Mock out logging to avoid FLContext internals ----
    monkeypatch.setattr(controller, "log_debug", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "log_info", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "log_error", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "log_exception", lambda *a, **kw: None)
    monkeypatch.setattr(controller, "system_panic", lambda *a, **kw: None)
    # -----------------------------------------------------------

    fl_ctx = Mock()

    # Create a DXO to persist
    dxo = DXO(data_kind=data_kind, data=data, meta=meta)

    # Ensure directory exists
    save_dir = tmp_path / "cross_val_models"
    save_dir.mkdir(parents=True, exist_ok=True)

    # Act: save the DXO, then load it back
    saved_path = controller._save_validation_content(
        name=name,
        save_dir=str(save_dir),
        dxo=dxo,
        fl_ctx=fl_ctx,
    )

    # Assert: a file with this base path should exist (DXO manages its own extension(s))
    assert Path(saved_path).exists() or any(
        Path(str(saved_path) + ext).exists() for ext in (".npy", ".npz", ".json")
    ), f"Expected saved DXO file(s) for base path {saved_path}"

    loaded = controller._load_validation_content(
        name=name,
        load_dir=str(save_dir),
        fl_ctx=fl_ctx,
    )

    # Validate round-trip content
    assert isinstance(loaded, DXO)
    assert loaded.data_kind == data_kind
    assert loaded.data == data
    assert loaded.meta == meta
