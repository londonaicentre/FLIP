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

"""Tests for the ``python -m flip.export`` command line."""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path

import pytest
import torch
from torch import nn

from flip.export.__main__ import _size_mb, build_parser, main

_MODELS_PY = """
from torch import nn


class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Conv2d(1, 2, 1)

    def forward(self, x):
        return self.net(x)


def get_model():
    return Net()
"""


@pytest.fixture(autouse=True)
def _restore_import_state():
    """Undo the ``sys.path`` / ``sys.modules`` mutation that loading an app's models.py performs."""
    original_path = list(sys.path)
    original_models = sys.modules.get("models")
    yield
    sys.path[:] = original_path
    if original_models is None:
        sys.modules.pop("models", None)
    else:
        sys.modules["models"] = original_models


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An application, its export configs and a checkpoint, laid out as the CLI expects to find them.

    Returns:
        Path: The workspace root, holding ``app_files/``, ``export/`` and ``FL_global_model.pt``.
    """
    app_dir = tmp_path / "app_files"
    app_dir.mkdir()
    (app_dir / "models.py").write_text(_MODELS_PY)

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "inference.json").write_text(json.dumps({"preprocessing": {"_target_": "Compose"}}))
    (export_dir / "metadata.json").write_text(json.dumps({"version": "0.0.1"}))

    torch.save(
        OrderedDict((f"net.{key}", value) for key, value in nn.Conv2d(1, 2, 1).state_dict().items()),
        tmp_path / "FL_global_model.pt",
    )
    return tmp_path


class TestFormFlag:
    """``--form`` selects the bundle shape, and defaults to the one the packaging guide documents."""

    def test_defaults_to_torchscript(self):
        """An existing invocation must keep producing exactly what it produced before."""
        assert build_parser().parse_args(["--checkpoint", "c.pt", "--app-dir", "a", "--out", "o"]).form == "torchscript"

    def test_directory_form_writes_a_bundle_tree(self, workspace, capsys):
        """The flag has to reach export_bundle, which is the wiring most likely to be wrong."""
        exit_code = main(
            [
                "--checkpoint",
                str(workspace / "FL_global_model.pt"),
                "--app-dir",
                str(workspace / "app_files"),
                "--out",
                str(workspace / "bundle"),
                "--form",
                "directory",
            ]
        )

        assert exit_code == 0
        assert (workspace / "bundle" / "models" / "model.pt").is_file()
        assert "form              : directory" in capsys.readouterr().out

    def test_directory_form_reports_no_method(self, workspace, capsys):
        """Printing ``method: None`` would claim a compilation that never happened."""
        main(
            [
                "--checkpoint",
                str(workspace / "FL_global_model.pt"),
                "--app-dir",
                str(workspace / "app_files"),
                "--out",
                str(workspace / "bundle"),
                "--form",
                "directory",
            ]
        )

        assert "method" not in capsys.readouterr().out

    def test_torchscript_form_still_reports_its_method(self, workspace, capsys):
        """The default path's output is unchanged."""
        main(
            [
                "--checkpoint",
                str(workspace / "FL_global_model.pt"),
                "--app-dir",
                str(workspace / "app_files"),
                "--out",
                str(workspace / "bundle" / "model.ts"),
            ]
        )

        assert "method            : script" in capsys.readouterr().out


class TestSizeReporting:
    """``st_size`` on a directory reports the inode, not the bundle."""

    def test_a_directory_is_measured_by_its_contents(self, tmp_path):
        """Summing the tree is the only honest answer for the directory form."""
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "model.pt").write_bytes(b"x" * 2_000_000)
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "metadata.json").write_bytes(b"y" * 1_000_000)

        assert _size_mb(tmp_path) == pytest.approx(3.0)

    def test_a_file_is_measured_directly(self, tmp_path):
        """The TorchScript form is one file, and must keep reporting its own size."""
        artefact = tmp_path / "model.ts"
        artefact.write_bytes(b"x" * 1_500_000)

        assert _size_mb(artefact) == pytest.approx(1.5)
