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

from unittest.mock import MagicMock

import pytest
from nvflare.apis.fl_constant import FLContextKey

from flip.nvflare.runtime import (
    FLIP_CUSTOM_PROPS_KEY,
    FLIP_MODEL_ID_KEY,
    get_flip_model_id,
    get_job_custom_props,
)

_VALID = "abcdef01-2345-6789-abcd-ef0123456789"


def _ctx(meta):
    ctx = MagicMock(name="fl_ctx")
    ctx.get_prop.side_effect = lambda key, default=None: meta if key == FLContextKey.JOB_META else default
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
