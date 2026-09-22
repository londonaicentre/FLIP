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

"""The OMOP table schemas must load offline, from any working directory."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

SCHEMAS_PATH = Path(__file__).resolve().parents[3] / "datasets" / "utils" / "omop_schemas.py"

CANONICAL_TABLES = (
    "person",
    "procedure_occurrence",
    "visit_occurrence",
    "image_occurrence",
    "image_feature",
    "measurement",
    "observation",
)


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the schemas module with all network access poisoned."""

    def _no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("omop_schemas must not reach the network at import time")

    import requests

    monkeypatch.setattr(requests, "get", _no_network)
    module_name = "fl_tutorials_under_test.omop_schemas"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SCHEMAS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCHEMAS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def test_schemas_load_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(monkeypatch)
    assert set(module.schemas) >= set(CANONICAL_TABLES)


def test_schemas_load_from_an_unrelated_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The cache is resolved relative to the module, not the caller's cwd."""
    monkeypatch.chdir(tmp_path)
    module = _load(monkeypatch)
    assert set(module.schemas) >= set(CANONICAL_TABLES)
    assert not (tmp_path / "omop").exists(), "must not write a cache into the caller's cwd"


def test_image_occurrence_schema_rejects_a_missing_required_column(monkeypatch: pytest.MonkeyPatch) -> None:
    """image_occurrence.accession_id is the key the whole imaging pipeline routes on."""
    import pandera.errors

    module = _load(monkeypatch)
    frame = pd.DataFrame(
        {
            "image_occurrence_id": [1],
            "person_id": [2],
            "procedure_occurrence_id": [3],
            "image_occurrence_date": pd.to_datetime(["2020-01-01"]),
            "image_study_uid": ["1.2.3"],
            "image_series_uid": ["1.2.3.4"],
            "modality_concept_id": [4300757],
        }
    )
    with pytest.raises(pandera.errors.SchemaError):
        module.schemas["image_occurrence"].validate(frame)
