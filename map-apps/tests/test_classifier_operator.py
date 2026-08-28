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

"""CPU-only tests for ``FlipXrayClassifierOperator``'s model loading (FLIP#1019).

Every bundle here is synthesised in ``tmp_path`` in the exact directory-form layout
``flip.export._write_directory_bundle`` produces: ``configs/{metadata,inference}.json``, a plain
state dict at ``models/model.pt``, and the generated ``flip_network.py`` entry point over a
``scripts/`` copy of the application. Loading one touches ``torch.jit`` at no point, which is the
property the directory form exists for. No GPU, dataset or network access is needed.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
import torch
from classification.classifier_operator import FlipXrayClassifierOperator
from monai.deploy.core import AppContext, Fragment
from monai.deploy.core.models import Model

IN_FEATURES = 4
OUT_FEATURES = 2
WEIGHT_FILL = 0.5
BIAS_FILL = -1.0

# What flip.export copies into scripts/: the application, whose models.py names the architecture.
_MODELS_PY = textwrap.dedent(
    f"""\
    import torch

    def get_model():
        return torch.nn.Linear({IN_FEATURES}, {OUT_FEATURES})
    """
)

# The generated entry point flip.export writes to the bundle root; network_def points at it.
_FLIP_NETWORK_PY = textwrap.dedent(
    """\
    import os
    import sys

    sys.path.append(os.path.join(os.path.dirname(__file__), "scripts"))

    from scripts.models import get_model  # noqa: E402, F401
    """
)


def _write_bundle(root: Path, *, with_weights: bool = True) -> Path:
    """Synthesise a directory bundle at ``root`` in flip.export's layout."""
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "metadata.json").write_text(json.dumps({"name": root.name, "version": "0.0.1"}))
    (configs / "inference.json").write_text(json.dumps({"network_def": {"_target_": "flip_network.get_model"}}))
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").touch()
    (scripts / "models.py").write_text(_MODELS_PY)
    (root / "flip_network.py").write_text(_FLIP_NETWORK_PY)
    if with_weights:
        network = torch.nn.Linear(IN_FEATURES, OUT_FEATURES)
        torch.nn.init.constant_(network.weight, WEIGHT_FILL)
        torch.nn.init.constant_(network.bias, BIAS_FILL)
        (root / "models").mkdir()
        torch.save(network.state_dict(), root / "models" / "model.pt")
    return root


def _make_operator(model_path: Path, *, app_context: AppContext | None = None, model_name: str = ""):
    return FlipXrayClassifierOperator(
        Fragment(),
        app_context=app_context if app_context is not None else AppContext({}),
        model_name=model_name,
        model_path=model_path,
    )


def _assert_is_loaded_bundle_network(model) -> None:
    assert isinstance(model, torch.nn.Linear)
    assert not model.training, "the bundle network must come back in eval mode"
    assert torch.equal(model.weight, torch.full((OUT_FEATURES, IN_FEATURES), WEIGHT_FILL))
    assert torch.equal(model.bias, torch.full((OUT_FEATURES,), BIAS_FILL))


@pytest.fixture(autouse=True)
def _isolate_bundle_imports():
    """Undo the loader's process-wide footprint between tests.

    ``_load_directory_bundle`` puts the bundle root on ``sys.path`` and imports its
    ``flip_network``/``scripts`` modules — per-process state that is harmless in a MAP (one bundle
    per container) but would leak one test's architecture into the next here.
    """
    path_snapshot = list(sys.path)
    yield
    sys.path[:] = path_snapshot
    for name in ("flip_network", "scripts", "scripts.models"):
        sys.modules.pop(name, None)


def test_loads_bundle_at_model_path_root(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle")
    operator = _make_operator(bundle)
    _assert_is_loaded_bundle_network(operator.model)


def test_finds_bundle_one_level_down(tmp_path):
    _write_bundle(tmp_path / "xray_model")
    operator = _make_operator(tmp_path)
    _assert_is_loaded_bundle_network(operator.model)


def test_ambiguous_bundles_raise_without_model_name(tmp_path):
    _write_bundle(tmp_path / "bundle_a")
    _write_bundle(tmp_path / "bundle_b")
    with pytest.raises(IOError, match=r"bundle_a.*bundle_b"):
        _make_operator(tmp_path)


def test_model_name_chooses_between_bundles(tmp_path):
    _write_bundle(tmp_path / "bundle_a")
    _write_bundle(tmp_path / "bundle_b")
    operator = _make_operator(tmp_path, model_name="bundle_b")
    _assert_is_loaded_bundle_network(operator.model)
    assert str(tmp_path / "bundle_b") in sys.path, "the chosen bundle's root is what goes on sys.path"


def test_missing_weights_raise(tmp_path):
    bundle = _write_bundle(tmp_path / "bundle", with_weights=False)
    with pytest.raises(IOError, match=r"models/model\.pt"):
        _make_operator(bundle)


def test_no_bundle_anywhere_raises(tmp_path):
    (tmp_path / "not_a_bundle").mkdir()
    with pytest.raises(IOError, match=r"found 0"):
        _make_operator(tmp_path)


def test_placeholder_predictor_falls_through_to_bundle(tmp_path):
    """A context Model with no predictor must not short-circuit the load.

    ModelFactory claims HOLOSCAN_MODEL_PATH for a directory bundle it cannot read and hands back a
    placeholder whose ``predictor`` is None; trusting mere presence fails much later, inside
    inference (see ``_get_model``). The guard must fall through to the bundle loader instead.
    """
    bundle = _write_bundle(tmp_path / "bundle")
    context = AppContext({})
    context.models = Model(str(bundle))
    assert context.models.get("").predictor is None, "precondition: the factory placeholder has no predictor"
    operator = _make_operator(bundle, app_context=context)
    _assert_is_loaded_bundle_network(operator.model)


def test_model_from_context_is_used_directly(tmp_path):
    """With a usable predictor in the context, nothing is read from model_path at all."""
    context_model = Model(str(tmp_path / "claimed"))
    context_model.predictor = torch.nn.Identity()
    context = AppContext({})
    context.models = context_model
    operator = _make_operator(tmp_path / "does-not-exist", app_context=context)
    assert operator.model is context_model
