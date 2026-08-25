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
import subprocess
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
from flip.export.__main__ import build_parser, main

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

# A model that carries state across forward passes. Scripting shares its buffers with the eager
# module, so the two disagree the moment either is called — the simplest faithful stand-in for any
# architecture whose export does not compute what the trained model computes.
_STATEFUL_MODELS_PY = """
import torch
from torch import nn


class CountingNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Conv2d(1, 2, 1)
        self.register_buffer("calls", torch.zeros(()))

    def forward(self, x):
        self.calls += 1
        return self.net(x) * self.calls


def get_model():
    return CountingNet()
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
    """Undo the ``sys.path`` / ``sys.modules`` mutation that loading an app's models.py performs.

    ``models`` is restored to whatever it was, rather than simply removed. Other suites register a
    stub module once at import time (``sys.modules.setdefault("models", ...)``), so deleting the
    key here would leave those tests with nothing to find when they run afterwards.
    """
    original_path = list(sys.path)
    original_models = sys.modules.get("models")
    yield
    sys.path[:] = original_path
    if original_models is None:
        sys.modules.pop("models", None)
    else:
        sys.modules["models"] = original_models


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


def _write_export_configs(tmp_path: Path, inference: dict | None = None) -> None:
    """Write the author-supplied bundle configs where the exporter looks for them by default.

    Args:
        tmp_path (Path): Test directory holding ``app_files/`` beside ``export/``.
        inference (dict | None): Inference config to write, defaulting to the shared one.
    """
    export_dir = tmp_path / "export"
    export_dir.mkdir(exist_ok=True)
    (export_dir / "inference.json").write_text(json.dumps(_INFERENCE if inference is None else inference))
    (export_dir / "metadata.json").write_text(json.dumps(_METADATA))


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """Create an application directory alongside an ``export/`` config directory.

    Returns:
        Path: The application directory (``<tmp>/app_files``).
    """
    app_dir = tmp_path / "app_files"
    app_dir.mkdir()
    (app_dir / "models.py").write_text(_MODELS_PY.format(out_channels=2))
    _write_export_configs(tmp_path)
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

    def test_an_export_that_changes_the_maths_is_reported(self, tmp_path):
        """A divergent export is otherwise silent: it writes, it loads, and it computes the wrong thing.

        The probe comparison is the only thing standing between that and a MAP that segments a
        patient with a model the researcher never trained, so the warning has to reach the caller
        rather than only the log.
        """
        app_dir = tmp_path / "app_files"
        app_dir.mkdir()
        (app_dir / "models.py").write_text(_STATEFUL_MODELS_PY)
        _write_export_configs(tmp_path)
        from torch import nn

        reference = nn.Conv2d(1, 2, 1)
        checkpoint = tmp_path / "FL_global_model.pt"
        torch.save(
            OrderedDict(
                model=OrderedDict(
                    [
                        ("net.weight", reference.weight.detach()),
                        ("net.bias", reference.bias.detach()),
                        ("calls", torch.zeros(())),
                    ]
                ),
                train_conf={"train": {"model": "CountingNet"}},
            ),
            checkpoint,
        )

        result = export_bundle(checkpoint, app_dir, tmp_path / "model.ts", example_input_shape=_INPUT_SHAPE)

        assert result.max_abs_delta > 0
        assert any("differs from eager by" in warning for warning in result.warnings)

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
        _write_export_configs(tmp_path)

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

    @pytest.mark.parametrize(("form", "leaf"), [("torchscript", "model.ts"), ("directory", "bundle")])
    def test_unverified_export_is_flagged(self, checkpoint, app, tmp_path, form, leaf):
        """Without a probe shape the export still succeeds, but says it was not verified."""
        result = export_bundle(checkpoint, app, tmp_path / leaf, form=form)

        assert result.max_abs_delta == -1.0
        assert any("not verified" in warning for warning in result.warnings)

    def test_ineligible_job_type_warns_but_does_not_refuse(self, checkpoint, app, tmp_path):
        """An evaluation job produces no new model — worth flagging, not worth blocking."""
        result = export_bundle(
            checkpoint, app, tmp_path / "model.ts", job_type="evaluation", example_input_shape=_INPUT_SHAPE
        )

        assert result.output.is_file()
        assert any("evaluation" in warning for warning in result.warnings)


_SIBLING_MODELS_PY = """
import json
from pathlib import Path

import blocks
from torch import nn


def _out_channels():
    return json.loads((Path(__file__).parent / "config.json").read_text())["net_config"]["out_channels"]


class WrappedNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = blocks.make_net(_out_channels())

    def forward(self, x):
        return self.net(x)


def get_model():
    return WrappedNet()
"""

_SIBLING_BLOCKS_PY = """
from torch import nn


def make_net(out_channels):
    return nn.Sequential(
        nn.Conv2d(1, 4, 3, padding=1),
        nn.BatchNorm2d(4),
        nn.ReLU(),
        nn.Conv2d(4, out_channels, 1),
    )
"""


@pytest.fixture
def realistic_app(tmp_path: Path) -> Path:
    """An application shaped like the shipped tutorials, rather than a single self-contained file.

    ``models.py`` imports a sibling module flatly and reads a sidecar ``config.json`` next to itself
    — both of which the spleen and Ark+ tutorials do — so the bundle has to carry more than
    ``models.py`` for its ``get_model`` to be callable at all.

    Returns:
        Path: The application directory.
    """
    app_dir = tmp_path / "app_files"
    (app_dir / "__pycache__").mkdir(parents=True)
    (app_dir / "__pycache__" / "models.cpython-312.pyc").write_bytes(b"\x00\x00")
    (app_dir / "models.py").write_text(_SIBLING_MODELS_PY)
    (app_dir / "blocks.py").write_text(_SIBLING_BLOCKS_PY)
    (app_dir / "config.json").write_text(json.dumps({"net_config": {"out_channels": 2}}))
    _write_export_configs(tmp_path)
    return app_dir


class TestDirectoryBundle:
    """The bundle form that needs no ``torch.jit`` (FLIP#1019)."""

    def test_writes_the_layout_the_bundle_operator_looks_for(self, checkpoint, app, tmp_path):
        """MonaiBundleInferenceOperator finds configs/ and models/ at fixed names."""
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        assert (out / "configs" / "inference.json").is_file()
        assert (out / "configs" / "metadata.json").is_file()
        assert (out / "models" / "model.pt").is_file()
        assert (out / "scripts" / "models.py").is_file()

    def test_nothing_in_the_bundle_is_torchscript(self, checkpoint, app, tmp_path):
        """The point of the form: the weights are a plain state dict, not a compiled archive."""
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        with pytest.raises(RuntimeError):
            torch.jit.load(str(out / "models" / "model.pt"))

    def test_reports_that_it_compiled_nothing(self, checkpoint, app, tmp_path):
        """``method`` describes a TorchScript compilation, and there was none."""
        result = export_bundle(checkpoint=checkpoint, app_dir=app, out=tmp_path / "bundle", form="directory")

        assert result.form == "directory"
        assert result.method is None

    def test_weights_reload_into_the_application_architecture(self, checkpoint, app, tmp_path):
        """The saved state dict still fits the models.py travelling beside it."""
        out = tmp_path / "bundle"
        result = export_bundle(
            checkpoint=checkpoint, app_dir=app, out=out, form="directory", example_input_shape=_INPUT_SHAPE
        )

        assert result.max_abs_delta == 0.0
        assert result.warnings == []

    def test_network_def_names_the_copied_application(self, checkpoint, app, tmp_path):
        """The bundle has to say where its architecture comes from; nothing else in it does."""
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        inference = json.loads((out / "configs" / "inference.json").read_text())
        assert inference["network_def"] == {"_target_": "flip_network.get_model"}
        assert inference["preprocessing"] == _INFERENCE["preprocessing"]
        assert (out / "flip_network.py").is_file()

    def test_an_author_declared_network_is_left_alone(self, checkpoint, app, tmp_path):
        """An author who has declared their own architecture keeps it, and is told so."""
        declared = {"_target_": "monai.networks.nets.UNet", "spatial_dims": 2}
        _write_export_configs(tmp_path, inference={**_INFERENCE, "network": declared})

        out = tmp_path / "bundle"
        result = export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        written = json.loads((out / "configs" / "inference.json").read_text())
        assert written["network"] == declared
        assert "network_def" not in written
        assert any("already declares 'network'" in warning for warning in result.warnings)

    def test_sibling_modules_and_sidecar_files_travel_with_models_py(self, checkpoint, realistic_app, tmp_path):
        """A tutorial's get_model() reads a sidecar config and imports a sibling; both must be there."""
        out = tmp_path / "bundle"
        result = export_bundle(
            checkpoint=checkpoint, app_dir=realistic_app, out=out, form="directory", example_input_shape=_INPUT_SHAPE
        )

        assert (out / "scripts" / "blocks.py").is_file()
        assert (out / "scripts" / "config.json").is_file()
        assert result.max_abs_delta == 0.0

    def test_build_artefacts_are_not_copied(self, checkpoint, realistic_app, tmp_path):
        """A stale __pycache__ carries no architecture and would ship compiled bytecode."""
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=realistic_app, out=out, form="directory")

        assert not (out / "scripts" / "__pycache__").exists()

    def test_the_bundle_is_importable_from_a_clean_interpreter(self, checkpoint, realistic_app, tmp_path):
        """The load the MAP performs: import flip_network with only the bundle root on sys.path.

        Run in a subprocess because it is the *absence* of this suite's import state that is under
        test — the flat ``import blocks`` has to resolve from inside the bundle, not from the
        application directory this process already put on ``sys.path``.
        """
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=realistic_app, out=out, form="directory")

        program = (
            "import sys, torch; sys.path.insert(0, sys.argv[1]);"
            "import flip_network; net = flip_network.get_model();"
            "net.load_state_dict(torch.load(sys.argv[1] + '/models/model.pt', weights_only=True), strict=True);"
            "print(type(net).__name__)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", program, str(out)], capture_output=True, text=True, cwd=tmp_path
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "WrappedNet"

    def test_the_copied_application_is_left_verbatim(self, checkpoint, app, tmp_path):
        """Generated code lives in flip_network.py, so the researcher's own files are untouched."""
        original = '"""Standard app module."""\nMARKER = "kept"\n'
        (app / "__init__.py").write_text(original)

        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        assert (out / "scripts" / "__init__.py").read_text() == original
        assert (out / "scripts" / "models.py").read_text() == (app / "models.py").read_text()

    def test_an_application_that_is_not_a_package_becomes_importable(self, checkpoint, app, tmp_path):
        """``flip_network`` imports ``scripts.models``, so ``scripts/`` has to be a package."""
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        assert (out / "scripts" / "__init__.py").read_text() == ""

    def test_a_bundle_refuses_to_be_committed(self, checkpoint, app, tmp_path):
        """A bundle copies the application, which no weights-shaped ignore rule would catch."""
        out = tmp_path / "bundle"
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        assert (out / ".gitignore").read_text().splitlines()[-1] == "*"

    def test_provenance_reaches_the_configs_metadata(self, checkpoint, app, tmp_path):
        """A directory bundle must be as traceable as the TorchScript one."""
        out = tmp_path / "bundle"
        export_bundle(
            checkpoint=checkpoint,
            app_dir=app,
            out=out,
            form="directory",
            provenance=Provenance(model_id="abc-123", participating_trusts=["GSTT", "KCH"]),
            exported_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )

        record = json.loads((out / "configs" / "metadata.json").read_text())[PROVENANCE_KEY]
        assert record["flip_model_id"] == "abc-123"
        assert record["participating_trusts"] == ["GSTT", "KCH"]
        assert record["source_checkpoint"] == "FL_global_model.pt"

    def test_unverified_export_is_flagged(self, checkpoint, app, tmp_path):
        """No probe input means no equivalence check, in either form."""
        result = export_bundle(checkpoint=checkpoint, app_dir=app, out=tmp_path / "bundle", form="directory")

        assert result.max_abs_delta == -1.0
        assert any("numerical equivalence was not verified" in warning for warning in result.warnings)

    def test_no_example_shape_is_needed_even_with_method_trace(self, checkpoint, app, tmp_path):
        """``method`` is a TorchScript concern, so its precondition cannot apply here."""
        out = tmp_path / "bundle"

        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory", method="trace")

        assert (out / "models" / "model.pt").is_file()

    def test_a_re_export_drops_modules_the_application_no_longer_has(self, checkpoint, app, tmp_path):
        """A stale module left from a previous export would shadow the current app on sys.path."""
        out = tmp_path / "bundle"
        (app / "old_helper.py").write_text("VALUE = 1\n")
        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")
        (app / "old_helper.py").unlink()

        export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")

        assert not (out / "scripts" / "old_helper.py").exists()
        assert (out / "scripts" / "models.py").is_file()

    def test_a_large_application_directory_is_called_out(self, checkpoint, app, tmp_path, monkeypatch):
        """The whole app dir travels, so a dataset left beside it silently bloats every bundle."""
        monkeypatch.setattr("flip.export.bundle._SCRIPTS_SIZE_WARN_BYTES", 1_000)
        (app / "leftover_dataset.npy").write_bytes(b"\x00" * 4_000)

        result = export_bundle(checkpoint=checkpoint, app_dir=app, out=tmp_path / "bundle", form="directory")

        assert any("move any data out of it before exporting" in warning for warning in result.warnings)

    def test_refuses_to_write_a_bundle_over_a_file(self, checkpoint, app, tmp_path):
        """``--out bundle/model.ts`` with the wrong form should say so, not half-write a tree."""
        out = tmp_path / "model.ts"
        out.write_bytes(b"not a bundle")

        with pytest.raises(NotADirectoryError, match="existing file"):
            export_bundle(checkpoint=checkpoint, app_dir=app, out=out, form="directory")


class TestTorchScriptFailure:
    """What a caller is told when ``torch.jit`` will not do its job (FLIP#1019)."""

    def test_a_compilation_failure_points_at_the_directory_form(self, checkpoint, app, tmp_path, monkeypatch):
        """The remedy is the same whether the model will not script or torch.jit has been removed."""

        def _gone(*args, **kwargs):
            raise AttributeError("module 'torch' has no attribute 'jit'")

        monkeypatch.setattr(torch.jit, "script", _gone)

        with pytest.raises(RuntimeError) as raised:
            export_bundle(checkpoint=checkpoint, app_dir=app, out=tmp_path / "model.ts")

        assert "form='directory'" in str(raised.value)
        assert "unsupported on Python 3.14+" in str(raised.value)
        # Advice must not cost the caller the underlying error.
        assert isinstance(raised.value.__cause__, AttributeError)


def _run_cli(checkpoint: Path, app: Path, out: Path, *extra: str) -> int:
    """Invoke the module's entry point the way a researcher would.

    Returns:
        int: The process exit code.
    """
    return main(["--checkpoint", str(checkpoint), "--app-dir", str(app), "--out", str(out), *extra])


class TestCommandLine:
    """``python -m flip.export`` — the wiring most likely to break is the flag reaching the call."""

    def test_form_defaults_to_torchscript(self):
        """An existing invocation must keep producing exactly what it produced before."""
        parsed = build_parser().parse_args(["--checkpoint", "c.pt", "--app-dir", "a", "--out", "o"])

        assert parsed.form == "torchscript"

    def test_directory_form_writes_a_bundle_and_claims_no_method(self, checkpoint, app, tmp_path, capsys):
        """Printing a method would claim a TorchScript compilation that never happened."""
        out = tmp_path / "bundle"

        exit_code = _run_cli(checkpoint, app, out, "--form", "directory")

        assert exit_code == 0
        assert (out / "models" / "model.pt").is_file()
        printed = capsys.readouterr().out
        assert "form              : directory" in printed
        assert "method" not in printed

    def test_torchscript_form_still_reports_its_method(self, checkpoint, app, tmp_path, capsys):
        """The default path's output is unchanged."""
        _run_cli(checkpoint, app, tmp_path / "model.ts")

        assert "method            : script" in capsys.readouterr().out


class TestBundleSize:
    """``ExportResult.size_bytes``, which the CLI prints for either form."""

    def test_a_directory_bundle_is_measured_by_its_contents(self, checkpoint, app, tmp_path):
        """``st_size`` on a directory reports the inode — a few kB, whatever the bundle holds."""
        out = tmp_path / "bundle"

        result = export_bundle(checkpoint, app, out, form="directory")

        assert result.size_bytes > (out / "models" / "model.pt").stat().st_size
        assert result.size_bytes != out.stat().st_size

    def test_a_torchscript_bundle_is_measured_directly(self, checkpoint, app, tmp_path):
        """The TorchScript form is one file, and must report its own size."""
        out = tmp_path / "model.ts"

        result = export_bundle(checkpoint, app, out)

        assert result.size_bytes == out.stat().st_size
