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

import json
import os
from unittest.mock import MagicMock

import pytest
from nvflare.apis.fl_constant import FLContextKey

from flip.nvflare.runtime import (
    FLIP_CUSTOM_PROPS_KEY,
    FLIP_MODEL_ID_CONFIG_KEY,
    FLIP_MODEL_ID_KEY,
    get_app_config,
    get_flip_model_id,
    get_job_custom_props,
)

_VALID = "abcdef01-2345-6789-abcd-ef0123456789"


def _ctx(meta):
    ctx = MagicMock(name="fl_ctx")
    ctx.get_prop.side_effect = lambda key, default=None: meta if key == FLContextKey.JOB_META else default
    return ctx


def _ctx_workspace(meta_path=None, engine=True, workspace=True, job_id="job-1"):
    """fl_ctx whose JOB_META prop is absent, forcing the workspace meta.json fallback path."""
    ctx = MagicMock(name="fl_ctx")
    ctx.get_prop.side_effect = lambda key, default=None: None if key == FLContextKey.JOB_META else default
    if not engine:
        ctx.get_engine.return_value = None
        return ctx
    eng = MagicMock(name="engine")
    ws = MagicMock(name="workspace") if workspace else None
    if ws is not None and meta_path is not None:
        ws.get_job_meta_path.return_value = meta_path
    eng.get_workspace.return_value = ws
    ctx.get_engine.return_value = eng
    ctx.get_job_id.return_value = job_id
    return ctx


def test_valid_fallback_uuid_is_used_without_meta():
    assert get_flip_model_id(_ctx(None), fallback=_VALID) == _VALID


def test_resolves_from_custom_props_when_no_fallback():
    meta = {FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: _VALID}}
    assert get_flip_model_id(_ctx(meta)) == _VALID


def test_empty_fallback_falls_through_to_custom_props():
    meta = {FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: _VALID}}
    assert get_flip_model_id(_ctx(meta), fallback="") == _VALID


def test_raises_when_neither_fallback_nor_custom_props():
    with pytest.raises(ValueError, match="FLIP model_id is not available"):
        get_flip_model_id(_ctx({}), fallback="not-a-uuid")


def test_get_job_custom_props_returns_empty_dict_when_absent():
    assert get_job_custom_props(_ctx(None)) == {}


def test_workspace_fallback_returns_empty_when_no_engine():
    # _load_meta_from_workspace: engine is None -> None -> {} custom_props
    assert get_job_custom_props(_ctx_workspace(engine=False)) == {}


def test_workspace_fallback_returns_empty_when_no_workspace():
    # _load_meta_from_workspace: engine present but workspace is None -> None -> {}
    assert get_job_custom_props(_ctx_workspace(workspace=False)) == {}


@pytest.mark.parametrize("meta_path", [1, None], ids=["fd-like int", "bare MagicMock"])
def test_workspace_fallback_never_opens_a_non_path_as_a_file_descriptor(meta_path):
    # open() accepts any __index__-able object as a file descriptor — an int, or a MagicMock (whose
    # __index__ is 1) — which would wrap stdout and close it on exit. A workspace answering with a
    # non-path must fall through to {} and leave fd 1 untouched.
    assert get_job_custom_props(_ctx_workspace(meta_path=meta_path)) == {}
    os.fstat(1)  # raises EBADF if the fallback closed stdout


def test_workspace_fallback_reads_custom_props_from_meta_json(tmp_path):
    # _load_meta_from_workspace: reads meta.json from the run dir when JOB_META isn't on the ctx
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: _VALID}}))
    assert get_job_custom_props(_ctx_workspace(meta_path=str(meta))) == {FLIP_MODEL_ID_KEY: _VALID}
    # and the full resolve goes through it too
    assert get_flip_model_id(_ctx_workspace(meta_path=str(meta))) == _VALID


# ── the app's config.json, where the FLIP-API writes the model id (FLIP#70) ──────────

_OTHER = "00000000-0000-0000-0000-000000000001"  # the placeholder FLIP's recipes leave in custom_props


def _ctx_app(tmp_path, config=None, meta=None):
    """fl_ctx whose run workspace holds the app's custom/config.json (if given) and JOB_META ``meta``."""
    app_dir = tmp_path / "app_server"
    (app_dir / "custom").mkdir(parents=True)
    if config is not None:
        (app_dir / "custom" / "config.json").write_text(json.dumps(config))
    ctx = _ctx(meta)
    ctx.get_engine.return_value.get_workspace.return_value.get_app_dir.return_value = str(app_dir)
    ctx.get_job_id.return_value = "job-1"
    return ctx


def test_resolves_from_the_apps_config_json(tmp_path):
    assert FLIP_MODEL_ID_CONFIG_KEY == "FLIP_MODEL_ID"
    assert get_flip_model_id(_ctx_app(tmp_path, config={"FLIP_MODEL_ID": _VALID})) == _VALID


def test_the_apps_config_json_beats_a_placeholder_left_in_custom_props(tmp_path):
    meta = {FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: _OTHER}}

    assert get_flip_model_id(_ctx_app(tmp_path, config={"FLIP_MODEL_ID": _VALID}, meta=meta)) == _VALID


def test_a_valid_component_arg_still_wins(tmp_path):
    assert get_flip_model_id(_ctx_app(tmp_path, config={"FLIP_MODEL_ID": _OTHER}), fallback=_VALID) == _VALID


@pytest.mark.parametrize("config", [None, {}, {"FLIP_MODEL_ID": "not-a-uuid"}, ["FLIP_MODEL_ID"]])
def test_without_a_usable_config_json_custom_props_still_resolve_it(tmp_path, config):
    """A SimEnv/PocEnv recipe run, or a job from an FL API that predates FLIP#70."""
    meta = {FLIP_CUSTOM_PROPS_KEY: {FLIP_MODEL_ID_KEY: _VALID}}

    assert get_flip_model_id(_ctx_app(tmp_path, config=config, meta=meta)) == _VALID


def test_the_error_names_every_place_it_looked(tmp_path):
    with pytest.raises(ValueError, match="FLIP_MODEL_ID.*config.json.*custom_props"):
        get_flip_model_id(_ctx_app(tmp_path, config={}, meta={}))


def test_app_config_is_empty_without_an_engine_or_a_workspace():
    assert get_app_config(_ctx_workspace(engine=False)) == {}
    assert get_app_config(_ctx_workspace(workspace=False)) == {}


@pytest.mark.parametrize("app_dir", [1, None], ids=["fd-like int", "bare MagicMock"])
def test_app_config_never_opens_a_non_path_as_a_file_descriptor(app_dir):
    ctx = _ctx_workspace()
    if app_dir is not None:
        ctx.get_engine.return_value.get_workspace.return_value.get_app_dir.return_value = app_dir

    assert get_app_config(ctx) == {}
    os.fstat(1)  # raises EBADF if the read closed stdout
