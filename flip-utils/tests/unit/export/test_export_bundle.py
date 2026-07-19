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

"""Tests for exporting a FLIP checkpoint as a MONAI Bundle.

These lock in the assumption the packaging path rests on — that aggregated weights load back into
the architecture their application declares — and the properties the exported artefact must have:
it carries its own configuration, it is numerically identical to the eager model, and it records
where it came from.

Real aggregated checkpoints are never committed, so the one silent mutation aggregation introduces
is reconstructed here instead: integer ``num_batches_tracked`` buffers arriving back as floating
point, because every entry of the state dict is averaged as a float.
"""

from __future__ import annotations

import json
import sys
import zipfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import pytest
import torch

from flip.export import (
    PROVENANCE_KEY,
    Provenance,
    describe_checkpoint,
    export_bundle,
    load_app_model,
    load_checkpoint,
)

_MODELS_PY = """
from torch import nn


class WrappedNet(nn.Module):
    def __init__(self, out_channels=2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(1, 4, 3, padding=1),
            nn.BatchNorm2d(4),
            nn.ReLU(),
            nn.Conv2d(4, out_channels, 1),
        )

    def forward(self, x):
        return self.net(x)


def get_model():
    return WrappedNet(out_channels={out_channels})
"""

_INFERENCE = {"preprocessing": {"_target_": "Compose", "transforms": []}}
_METADATA = {
    "version": "0.0.1",
    "network_data_format": {
        "inputs": {"image": {"num_channels": 1, "spatial_shape": [8, 8]}},
        "outputs": {"pred": {"num_channels": 2}},
    },
}

_INPUT_SHAPE = (1, 1, 8, 8)


@pytest.fixture(autouse=True)
def _restore_import_state():
    """Undo the ``sys.path`` / ``sys.modules`` mutation that loading an app's models.py performs."""
    original_path = list(sys.path)
    yield
    sys.path[:] = original_path
    sys.modules.pop("models", None)


def _reference_state_dict(out_channels: int = 2) -> OrderedDict:
    """Build a state dict from the reference architecture."""
    from torch import nn

    net = nn.Sequential(
        nn.Conv2d(1, 4, 3, padding=1),
        nn.BatchNorm2d(4),
        nn.ReLU(),
        nn.Conv2d(4, out_channels, 1),
    )
    return OrderedDict((f"net.{key}", value) for key, value in net.state_dict().items())


def _promote_integer_buffers(state_dict: OrderedDict) -> OrderedDict:
    """Cast ``num_batches_tracked`` buffers to float64, as FLIP's aggregation does."""
    return OrderedDict(
        (key, value.to(torch.float64) if key.endswith("num_batches_tracked") else value)
        for key, value in state_dict.items()
    )


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """Create an application directory alongside an ``export/`` config directory.

    Returns:
        Path: The application directory (``<tmp>/app_files``).
    """
    app_dir = tmp_path / "app_files"
    app_dir.mkdir()
    (app_dir / "models.py").write_text(_MODELS_PY.format(out_channels=2))

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "inference.json").write_text(json.dumps(_INFERENCE))
    (export_dir / "metadata.json").write_text(json.dumps(_METADATA))
    return app_dir


@pytest.fixture
def checkpoint(tmp_path: Path) -> Path:
    """Write an aggregated checkpoint in NVFLARE persistence format, with promoted buffers.

    Returns:
        Path: The checkpoint path.
    """
    path = tmp_path / "FL_global_model.pt"
    torch.save(
        OrderedDict(
            model=_promote_integer_buffers(_reference_state_dict()),
            train_conf={"train": {"model": "WrappedNet"}},
        ),
        path,
    )
    return path


class TestCheckpointReading:
    """Reading the two on-disk checkpoint shapes."""

    def test_recognises_persistence_format_and_promoted_buffers(self, checkpoint, tmp_path):
        """An aggregated checkpoint is recognised, and its float-promoted buffers surfaced."""
        facts = describe_checkpoint(load_checkpoint(checkpoint), checkpoint)

        assert facts.is_persistence_format is True
        assert facts.train_conf == {"train": {"model": "WrappedNet"}}
        assert facts.promoted_buffers == ["net.1.num_batches_tracked"]
        assert facts.prefixes == ["net"]

    def test_recognises_bare_state_dict(self, tmp_path):
        """A user-uploaded evaluation checkpoint is read as a bare state dict."""
        path = tmp_path / "model.pt"
        torch.save(_reference_state_dict(), path)

        facts = describe_checkpoint(load_checkpoint(path), path)

        assert facts.is_persistence_format is False
        assert facts.train_conf is None

    def test_missing_checkpoint_is_an_explicit_error(self, tmp_path):
        """A missing checkpoint names the path rather than failing obscurely."""
        with pytest.raises(FileNotFoundError, match="No checkpoint at"):
            load_checkpoint(tmp_path / "absent.pt")


class TestAppContract:
    """The application-contract surface the export depends on."""

    def test_missing_models_py_is_an_explicit_error(self, tmp_path):
        """An app directory without models.py names the contract it breaches."""
        with pytest.raises(FileNotFoundError, match="No models.py"):
            load_app_model(tmp_path)

    def test_models_py_without_get_model_is_an_explicit_error(self, tmp_path):
        """A models.py that does not export get_model() names the contract it breaches."""
        (tmp_path / "models.py").write_text("VALUE = 1\n")

        with pytest.raises(AttributeError, match="does not export get_model"):
            load_app_model(tmp_path)

    def test_get_model_returning_non_module_is_rejected(self, tmp_path):
        """get_model() must return an nn.Module, not an arbitrary object."""
        (tmp_path / "models.py").write_text("def get_model():\n    return {'not': 'a module'}\n")

        with pytest.raises(TypeError, match="expected a torch.nn.Module"):
            load_app_model(tmp_path)


class TestExportBundle:
    """The exported artefact and its properties."""

    def test_exports_a_self_contained_bundle(self, checkpoint, app, tmp_path):
        """The bundle carries its configuration as TorchScript extra files."""
        out = tmp_path / "bundle" / "model.ts"

        result = export_bundle(checkpoint, app, out, example_input_shape=_INPUT_SHAPE)

        assert out.is_file()
        assert result.method == "script"
        assert result.num_state_entries == len(_reference_state_dict())
        embedded = zipfile.ZipFile(out).namelist()
        assert any(name.endswith("extra/inference.json") for name in embedded)
        assert any(name.endswith("extra/metadata.json") for name in embedded)

    def test_scripted_model_is_numerically_identical_to_eager(self, checkpoint, app, tmp_path):
        """Scripting must not change what the model computes."""
        out = tmp_path / "model.ts"

        result = export_bundle(checkpoint, app, out, example_input_shape=_INPUT_SHAPE)

        assert result.max_abs_delta == 0.0

    def test_exported_bundle_reloads_from_disk_and_still_matches(self, checkpoint, app, tmp_path):
        """The saved artefact is self-contained — a MAP carries no FLIP app code."""
        out = tmp_path / "model.ts"
        export_bundle(checkpoint, app, out, example_input_shape=_INPUT_SHAPE)
        eager = load_app_model(app)
        eager.load_state_dict(load_checkpoint(checkpoint)["model"], strict=True)
        eager.eval()

        reloaded = torch.jit.load(str(out), map_location="cpu").eval()

        probe = torch.randn(*_INPUT_SHAPE)
        with torch.no_grad():
            assert torch.equal(reloaded(probe), eager(probe))

    def test_author_configs_are_embedded_unchanged(self, checkpoint, app, tmp_path):
        """inference.json is the author's, carried through verbatim — the exporter never invents it."""
        out = tmp_path / "model.ts"
        export_bundle(checkpoint, app, out, example_input_shape=_INPUT_SHAPE)

        embedded = json.loads(zipfile.ZipFile(out).read("model/extra/inference.json"))

        assert embedded == _INFERENCE

    def test_provenance_is_embedded_in_metadata(self, checkpoint, app, tmp_path):
        """A deployed bundle can be traced back to the federated run that produced it."""
        out = tmp_path / "model.ts"
        provenance = Provenance(
            model_id="abc-123",
            project_id="proj-9",
            participating_trusts=["GSTT", "KCH"],
            global_rounds=10,
            final_aggregate_metric="Dice=0.91",
        )

        export_bundle(
            checkpoint,
            app,
            out,
            provenance=provenance,
            example_input_shape=_INPUT_SHAPE,
            exported_at=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )

        metadata = json.loads(zipfile.ZipFile(out).read("model/extra/metadata.json"))
        record = metadata[PROVENANCE_KEY]
        assert record["flip_model_id"] == "abc-123"
        assert record["participating_trusts"] == ["GSTT", "KCH"]
        assert record["source_checkpoint"] == "FL_global_model.pt"
        assert record["exported_at"] == "2026-07-19T12:00:00+00:00"
        assert record["not_for_clinical_use"] is True
        # The author's own metadata must survive alongside the stamped provenance.
        assert metadata["network_data_format"] == _METADATA["network_data_format"]

    def test_provenance_is_recorded_even_when_not_supplied(self, checkpoint, app, tmp_path):
        """An artefact is never left untraceable, even on a bare export."""
        out = tmp_path / "model.ts"

        export_bundle(checkpoint, app, out, example_input_shape=_INPUT_SHAPE)

        record = json.loads(zipfile.ZipFile(out).read("model/extra/metadata.json"))[PROVENANCE_KEY]
        assert record["source_checkpoint"] == "FL_global_model.pt"
        assert record["not_for_clinical_use"] is True

    def test_architecture_mismatch_fails_loudly(self, checkpoint, tmp_path):
        """A checkpoint that does not fit the declared model raises rather than half-loading."""
        app_dir = tmp_path / "other_app"
        app_dir.mkdir()
        (app_dir / "models.py").write_text(_MODELS_PY.format(out_channels=5))
        export_dir = tmp_path / "export"
        export_dir.mkdir(exist_ok=True)
        (export_dir / "inference.json").write_text(json.dumps(_INFERENCE))
        (export_dir / "metadata.json").write_text(json.dumps(_METADATA))

        with pytest.raises(RuntimeError, match="size mismatch|Error"):
            export_bundle(checkpoint, app_dir, tmp_path / "model.ts", example_input_shape=_INPUT_SHAPE)

    def test_missing_bundle_config_explains_it_is_author_supplied(self, checkpoint, tmp_path):
        """The error points at the app contract rather than looking like a tool failure."""
        app_dir = tmp_path / "app_files"
        app_dir.mkdir()
        (app_dir / "models.py").write_text(_MODELS_PY.format(out_channels=2))

        with pytest.raises(FileNotFoundError, match="written by the app author, not"):
            export_bundle(checkpoint, app_dir, tmp_path / "model.ts")

    def test_trace_requires_an_example_shape(self, checkpoint, app, tmp_path):
        """Tracing needs a concrete input, and says so rather than failing inside torch."""
        with pytest.raises(ValueError, match="requires example_input_shape"):
            export_bundle(checkpoint, app, tmp_path / "model.ts", method="trace")

    def test_trace_is_available_as_a_fallback(self, checkpoint, app, tmp_path):
        """Tracing works when a shape is declared, for models that will not script."""
        out = tmp_path / "model.ts"

        result = export_bundle(checkpoint, app, out, method="trace", example_input_shape=_INPUT_SHAPE)

        assert result.method == "trace"
        assert result.max_abs_delta == 0.0

    def test_unverified_export_is_flagged(self, checkpoint, app, tmp_path):
        """Without a probe shape the export still succeeds, but says it was not verified."""
        result = export_bundle(checkpoint, app, tmp_path / "model.ts")

        assert result.max_abs_delta == -1.0
        assert any("not verified" in warning for warning in result.warnings)

    def test_ineligible_job_type_warns_but_does_not_refuse(self, checkpoint, app, tmp_path):
        """An evaluation job produces no new model — worth flagging, not worth blocking."""
        result = export_bundle(
            checkpoint, app, tmp_path / "model.ts", job_type="evaluation", example_input_shape=_INPUT_SHAPE
        )

        assert result.output.is_file()
        assert any("evaluation" in warning for warning in result.warnings)
