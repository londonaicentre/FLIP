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

import ast
import os
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from nvflare.apis.dxo import DXO, DataKind, MetaKey
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import FLContextKey
from nvflare.app_common.abstract.model import ModelLearnableKey
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.shareablegenerators.full_model_shareable_generator import FullModelShareableGenerator

import flip.constants.flip_constants as fc_module
from flip.constants import PTConstants
from flip.nvflare.components import pt_model_locator
from flip.nvflare.components.pt_model_locator import PTModelLocator
from flip.nvflare.components.pt_model_persistor import InitialCheckpointPTModelPersistor

LOCATOR_SOURCE = Path(pt_model_locator.__file__)
TORCH_LOAD = {"torch.load", "torch.serialization.load"}

# Records every execution of the tripwire a crafted checkpoint tries to smuggle in.
_TRIPWIRE: list[str] = []


def _trip() -> None:
    _TRIPWIRE.append("executed")


class _Payload:
    """Pickles to a call of ``_trip`` — what an attacker's ``.pt`` does with a real command."""

    def __reduce__(self):
        return (_trip, ())


class TestPTModelLocator:
    """Tests for PTModelLocator component"""

    @patch("builtins.__import__")
    def test_init_with_no_model(self, mock_import):
        """Test initialization without model parameter loads from models module"""
        mock_models_module = MagicMock()
        mock_model = MagicMock()
        mock_models_module.get_model.return_value = mock_model

        def import_side_effect(name, *args, **kwargs):
            if name == "models":
                return mock_models_module
            return __import__(name, *args, **kwargs)

        mock_import.side_effect = import_side_effect

        locator = PTModelLocator()
        assert locator.model == mock_model
        assert locator.exclude_vars is None

    def test_init_with_model(self):
        """Test initialization with model parameter"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        assert locator.model == mock_model
        assert locator.exclude_vars is None

    def test_init_with_exclude_vars(self):
        """Test initialization with exclude_vars parameter"""
        mock_model = MagicMock()
        exclude_vars = ["var1", "var2"]
        locator = PTModelLocator(model=mock_model, exclude_vars=exclude_vars)
        assert locator.model == mock_model
        assert locator.exclude_vars == exclude_vars

    def test_get_model_names(self):
        """Test get_model_names returns PTServerName"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        fl_ctx = MagicMock()

        names = locator.get_model_names(fl_ctx)
        assert names == [PTConstants.PTServerName]
        assert len(names) == 1

    @patch.dict(os.environ, {"LOCAL_DEV": "true"}, clear=False)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.exists")
    def test_locate_model_local_dev_success(self, mock_exists, mock_to_dxo, mock_persistence_manager_cls, mock_torch):
        """Test locate_model in local dev mode with existing model"""
        # Reset FlipConstants to pick up the environment change
        import flip.constants.flip_constants as fc_module

        fc_module._flip_constants_instance = None

        # Setup mocks
        mock_model = MagicMock()
        mock_model.__class__.__name__ = "TestModel"
        locator = PTModelLocator(model=mock_model)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"model": "data"}

        mock_persistence_manager = MagicMock()
        mock_ml = MagicMock()
        mock_persistence_manager.to_model_learnable.return_value = mock_ml
        mock_persistence_manager_cls.return_value = mock_persistence_manager

        mock_dxo = MagicMock()
        mock_to_dxo.return_value = mock_dxo

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result == mock_dxo
        mock_exists.assert_called_once()
        mock_torch.load.assert_called_once()
        mock_persistence_manager.to_model_learnable.assert_called_once_with(exclude_vars=None)
        mock_to_dxo.assert_called_once_with(mock_ml)

    @patch.dict(os.environ, {"LOCAL_DEV": "false"}, clear=False)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.exists")
    def test_locate_model_production_mode_success(
        self, mock_exists, mock_to_dxo, mock_persistence_manager_cls, mock_torch
    ):
        """Test locate_model in production mode with existing model"""
        # Reset FlipConstants to pick up the environment change
        import flip.constants.flip_constants as fc_module

        fc_module._flip_constants_instance = None

        # Setup mocks
        mock_model = MagicMock()
        mock_model.__class__.__name__ = "TestModel"
        locator = PTModelLocator(model=mock_model)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = True
        mock_torch.load.return_value = {"model": "data"}

        mock_persistence_manager = MagicMock()
        mock_ml = MagicMock()
        mock_persistence_manager.to_model_learnable.return_value = mock_ml
        mock_persistence_manager_cls.return_value = mock_persistence_manager

        mock_dxo = MagicMock()
        mock_to_dxo.return_value = mock_dxo

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result == mock_dxo
        mock_exists.assert_called_once()
        assert "/test/run/dir/model" in mock_exists.call_args[0][0]
        mock_torch.load.assert_called_once()

    @patch.dict(os.environ, {"LOCAL_DEV": "true"}, clear=False)
    @patch("os.path.exists")
    def test_locate_model_file_not_found(self, mock_exists):
        """Test locate_model returns None when model file doesn't exist"""
        # Reset FlipConstants to pick up the environment change
        import flip.constants.flip_constants as fc_module

        fc_module._flip_constants_instance = None

        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = False

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result is None
        locator.log_error.assert_called_once()
        assert "Model file not found" in str(locator.log_error.call_args)

    @patch.dict(os.environ, {"LOCAL_DEV": "true"}, clear=False)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("os.path.exists")
    def test_locate_model_exception_during_load(self, mock_exists, mock_torch):
        """Test locate_model returns None when exception occurs during model load"""
        # Reset FlipConstants to pick up the environment change
        import flip.constants.flip_constants as fc_module

        fc_module._flip_constants_instance = None

        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.side_effect = Exception("Load error")

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result is None
        locator.log_error.assert_called_once()
        assert "Error in retrieving" in str(locator.log_error.call_args)

    def test_locate_model_invalid_name(self):
        """Test locate_model returns None for invalid model name"""
        mock_model = MagicMock()
        locator = PTModelLocator(model=mock_model)
        locator.log_error = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        # Execute
        result = locator.locate_model("invalid_name", fl_ctx)

        # Verify
        assert result is None
        locator.log_error.assert_called_once()
        assert "doesn't recognize name" in str(locator.log_error.call_args)

    @patch.dict(os.environ, {"LOCAL_DEV": "true"}, clear=False)
    @patch("flip.nvflare.components.pt_model_locator.torch")
    @patch("flip.nvflare.components.pt_model_locator.PTModelPersistenceFormatManager")
    @patch("flip.nvflare.components.pt_model_locator.model_learnable_to_dxo")
    @patch("os.path.exists")
    @patch("builtins.__import__")
    def test_locate_model_without_model_instance(
        self, mock_import, mock_exists, mock_to_dxo, mock_persistence_manager_cls, mock_torch
    ):
        """Test locate_model when initialized without model instance"""
        # Reset FlipConstants to pick up the environment change
        import flip.constants.flip_constants as fc_module

        fc_module._flip_constants_instance = None

        mock_models_module = MagicMock()
        mock_model = MagicMock()
        mock_models_module.get_model.return_value = mock_model

        def import_side_effect(name, *args, **kwargs):
            if name == "models":
                return mock_models_module
            return __import__(name, *args, **kwargs)

        mock_import.side_effect = import_side_effect

        locator = PTModelLocator()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        mock_engine = MagicMock()
        mock_workspace = MagicMock()
        mock_workspace.get_app_dir.return_value = "/test/run/dir"
        mock_engine.get_workspace.return_value = mock_workspace
        fl_ctx.get_engine.return_value = mock_engine
        fl_ctx.get_job_id.return_value = "test-job-id"

        mock_exists.return_value = True
        mock_torch.cuda.is_available.return_value = False
        mock_torch.load.return_value = {"model": "data"}

        mock_persistence_manager = MagicMock()
        mock_ml = MagicMock()
        mock_persistence_manager.to_model_learnable.return_value = mock_ml
        mock_persistence_manager_cls.return_value = mock_persistence_manager

        mock_dxo = MagicMock()
        mock_to_dxo.return_value = mock_dxo

        # Execute
        result = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        # Verify
        assert result == mock_dxo


@pytest.fixture
def local_dev():
    """Pin LOCAL_DEV=true and re-resolve FlipConstants, so the run-dir path is ``<app>/FL_global_model.pt``."""
    with patch.dict(os.environ, {"LOCAL_DEV": "true"}, clear=False):
        fc_module._flip_constants_instance = None
        yield
    fc_module._flip_constants_instance = None


@pytest.fixture
def fl_ctx(tmp_path: Path) -> MagicMock:
    """An FLContext stand-in whose app root and server run directory are both ``tmp_path``."""
    props: dict = {FLContextKey.APP_ROOT: str(tmp_path)}
    ctx = MagicMock()
    ctx.get_prop.side_effect = lambda key, default=None: props.get(key, default)
    ctx.set_prop.side_effect = lambda key, value, **_: props.__setitem__(key, value)
    ctx.get_engine.return_value.get_workspace.return_value.get_app_dir.return_value = str(tmp_path)
    ctx.get_job_id.return_value = "job-1"
    ctx.get_peer_context.return_value = None
    return ctx


def _persist_one_round(model: torch.nn.Module, fl_ctx: MagicMock) -> dict[str, np.ndarray]:
    """Write ``FL_global_model.pt`` exactly as the server does after a training round.

    Returns the global weights the file should hold: the persistor's initial state dict plus the
    all-ones ``WEIGHT_DIFF`` the shareable generator applies.
    """
    persistor = InitialCheckpointPTModelPersistor(model=model)
    assert persistor.global_model_file_name == PTConstants.PTFileModelName
    persistor.handle_event(EventType.START_RUN, fl_ctx)
    initial = persistor.load_model(fl_ctx)
    fl_ctx.set_prop(AppConstants.GLOBAL_MODEL, initial)

    diff = {name: np.ones_like(value) for name, value in initial[ModelLearnableKey.WEIGHTS].items()}
    dxo = DXO(data_kind=DataKind.WEIGHT_DIFF, data=diff)
    # The aggregate's meta is a plain dict keyed by MetaKey strings — empty in FLIP's FedAvg flow;
    # one entry here so the ``meta_props`` branch of the persisted layout is exercised too.
    dxo.set_meta_prop(MetaKey.PROCESSED_ALGORITHM, "none")
    aggregated = FullModelShareableGenerator().shareable_to_learnable(dxo.to_shareable(), fl_ctx)
    persistor.save(aggregated, fl_ctx)

    return {name: value.detach().cpu().numpy() + 1.0 for name, value in model.state_dict().items()}


@pytest.mark.usefixtures("local_dev")
class TestRunDirCheckpointGuardedLoad:
    """The run-directory loader unpickles with ``weights_only=True`` and still serves the global model (FLIP#1245).

    ``FL_global_model.pt`` is written by NVFLARE's ``PTFileModelPersistor`` (FLIP's
    ``InitialCheckpointPTModelPersistor`` inherits the save path unchanged): ``save_model_file`` pickles
    ``PTModelPersistenceFormatManager.to_persistence_dict()`` — an ``OrderedDict`` whose ``model`` entry is
    an ``OrderedDict`` of tensors (``torch.as_tensor`` over the aggregated numpy weights) beside the plain
    ``train_conf`` and ``meta_props`` dicts. Every type in there is on torch's weights-only allowlist, so
    the guarded load round-trips the persistor's output exactly, while a ``.pt`` carrying anything else — a
    class, a callable — is refused instead of executed in the fl-server process (the FLIP#1206 threat
    class). The fixture drives the real objects rather than a hand-written dict: the persistor's
    ``load_model`` → the stock ``FullModelShareableGenerator`` (what ``ScatterAndGather`` hands the
    persistor after aggregation) → ``persistor.save`` → the locator's ``locate_model``.
    """

    def test_persisted_layout_is_weights_only_safe(self, tmp_path: Path, fl_ctx: MagicMock):
        """The persistor's own output loads under weights_only=True with the documented layout."""
        torch.manual_seed(0)
        _persist_one_round(torch.nn.Linear(3, 2), fl_ctx)

        raw = torch.load(tmp_path / PTConstants.PTFileModelName, map_location="cpu", weights_only=True)

        assert isinstance(raw, OrderedDict)
        assert set(raw) == {"model", "train_conf", "meta_props"}
        assert isinstance(raw["model"], OrderedDict)
        assert all(isinstance(value, torch.Tensor) for value in raw["model"].values())
        assert raw["train_conf"] == {"train": {"model": "Linear"}}
        assert raw["meta_props"] == {MetaKey.PROCESSED_ALGORITHM: "none"}

    def test_locator_round_trips_the_persisted_global_model(self, tmp_path: Path, fl_ctx: MagicMock):
        """The run-dir loader passes weights_only=True and still serves the aggregated weights."""
        torch.manual_seed(0)
        model = torch.nn.Linear(3, 2)
        expected = _persist_one_round(model, fl_ctx)
        assert (tmp_path / PTConstants.PTFileModelName).is_file()

        locator = PTModelLocator(model=model)
        with patch("flip.nvflare.components.pt_model_locator.torch.load", wraps=torch.load) as spy:
            dxo = locator.locate_model(PTConstants.PTServerName, fl_ctx)

        spy.assert_called_once()
        assert spy.call_args.kwargs["weights_only"] is True
        assert dxo is not None
        assert dxo.data_kind == DataKind.WEIGHTS
        assert set(dxo.data) == set(expected)
        for name, value in expected.items():
            np.testing.assert_allclose(dxo.data[name], value)

    def test_checkpoint_with_a_foreign_global_is_refused_not_executed(self, tmp_path: Path, fl_ctx: MagicMock):
        """A .pt that pickles a callable fails the guarded load; the callable never runs."""
        path = tmp_path / PTConstants.PTFileModelName
        torch.save(OrderedDict(model=OrderedDict(weight=torch.zeros(1)), evil=_Payload()), path)

        # Control: the payload is live — full unpickling runs it.
        _TRIPWIRE.clear()
        torch.load(path, map_location="cpu", weights_only=False)
        assert _TRIPWIRE == ["executed"]

        _TRIPWIRE.clear()
        locator = PTModelLocator(model=torch.nn.Linear(1, 1))
        locator.log_error = MagicMock()

        assert locator.locate_model(PTConstants.PTServerName, fl_ctx) is None
        assert _TRIPWIRE == []
        locator.log_error.assert_called_once()
        assert "Error in retrieving" in str(locator.log_error.call_args)


def _dotted(node: ast.expr) -> str:
    """``torch.load`` for a Name/Attribute chain, the bare name for a Name, '' for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> canonical dotted name, for ``import a.b as c`` and ``from a.b import c as d``."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:  # `import torch.cuda` binds `torch`
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _torch_load_calls(tree: ast.Module) -> list[ast.Call]:
    aliases = _import_aliases(tree)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if not dotted:
            continue
        head, _, rest = dotted.partition(".")
        canonical = aliases.get(head, head) + (f".{rest}" if rest else "")
        if canonical in TORCH_LOAD:
            calls.append(node)
    return calls


def test_every_torch_load_in_the_locator_is_weights_only():
    """Static guard: each ``torch.load`` in pt_model_locator.py passes a literal ``weights_only=True``.

    The style of ``fl-tutorials/tests/test_offline_apps.py`` — import aliases resolved, a ``**kwargs``
    splat is an offence, and an empty match set fails so the guard cannot pass vacuously.
    """
    tree = ast.parse(LOCATOR_SOURCE.read_text(), filename=str(LOCATOR_SOURCE))
    calls = _torch_load_calls(tree)
    assert calls, f"no torch.load call found in {LOCATOR_SOURCE.name}; the guard has nothing to check"

    offenders = []
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        if None in keywords:  # ``**kwargs`` splat could carry anything
            offenders.append(f"line {call.lineno}: torch.load with a **kwargs splat")
            continue
        value = keywords.get("weights_only")
        if not (isinstance(value, ast.Constant) and value.value is True):
            offenders.append(f"line {call.lineno}: torch.load without weights_only=True")
    assert not offenders, "\n".join(offenders)
