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

"""The shared OMOP concept maps are the contract both converters resolve against."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

MAPPINGS_PATH = Path(__file__).resolve().parents[3] / "datasets" / "utils" / "omop_mappings.py"


@pytest.fixture(scope="module")
def mappings() -> ModuleType:
    """The mappings module, imported from its path under a unique module name."""
    module_name = "fl_tutorials_under_test.omop_mappings"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, MAPPINGS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {MAPPINGS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


EXPECTED_MAPS = (
    "MAPPING_SEX",
    "MAPPING_MODALITY",
    "MAPPING_PROCEDURE_TYPE",
    "MAPPING_ANATOMIC_SITE",
    "MAPPING_DICOM",
    "MAPPING_FINDING",
    "MAPPING_YES_NO",
    "MAPPING_CXR",
)


@pytest.mark.parametrize("name", EXPECTED_MAPS)
def test_every_map_is_present_and_maps_to_concept_ids(mappings: ModuleType, name: str) -> None:
    mapping = getattr(mappings, name)
    assert isinstance(mapping, dict), f"{name} must be a dict"
    assert mapping, f"{name} must be non-empty"
    for key, concept_id in mapping.items():
        assert isinstance(key, str), f"{name}[{key!r}] key must be a string"
        assert isinstance(concept_id, int), f"{name}[{key!r}] must map to an int concept id"
        assert concept_id >= 0, f"{name}[{key!r}] concept id must be non-negative"


def test_sex_map_covers_the_dicom_patient_sex_values(mappings: ModuleType) -> None:
    """PatientSex (0010,0040) is M, F or O — an unmapped value would become a silent NaN."""
    assert set(mappings.MAPPING_SEX) >= {"M", "F", "O"}


def test_modality_map_covers_the_modalities_the_converters_emit(mappings: ModuleType) -> None:
    """CT for spleen, CR/DX for cxr, MR for the prostate follow-up."""
    assert {"CT", "CR", "DX", "MR"} <= set(mappings.MAPPING_MODALITY)
    assert mappings.MAPPING_MODALITY["CT"] == 4300757
    assert mappings.MAPPING_MODALITY["MR"] == 4013636
