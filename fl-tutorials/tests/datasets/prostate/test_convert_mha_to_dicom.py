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

"""The prostate DICOM carries a synthetic patient identity, fixed by the PI-CAI ids.

A study without PatientName, PatientBirthDate, ReferringPhysicianName and StudyDescription is not
what a hospital PACS hands over and the trusts' imaging-api does not import one, so the converter
synthesises them (``synthetic_identity.py``). These tests pin that they are written, that they are the
same on every run (the published DICOM set must stay reproducible), that a patient's two studies
share one name and birth date, and that the birth date agrees with the age PI-CAI recorded.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import numpy as np
import pydicom
import pytest
import SimpleITK as sitk

PROSTATE_DIR = Path(__file__).resolve().parents[3] / "datasets" / "prostate"


def load_script(name: str) -> ModuleType:
    """A loose script from ``datasets/prostate/`` loaded from its path, siblings importable."""
    if str(PROSTATE_DIR) not in sys.path:
        sys.path.insert(0, str(PROSTATE_DIR))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", PROSTATE_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def identity() -> ModuleType:
    return load_script("synthetic_identity")


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    return load_script("convert_mha_to_dicom")


def picai_image(study_date: str = "2019-07-02", age: str = "073Y") -> sitk.Image:
    """A tiny scan with the headers PI-CAI's anonymisation leaves in its ``.mha`` files."""
    image = sitk.GetImageFromArray(np.arange(4 * 6 * 3, dtype=np.uint16).reshape(3, 6, 4))
    image.SetSpacing((0.5, 0.5, 3.0))
    for tag, value in {
        "0008|0020": study_date,
        "0008|0060": "MR",
        "0008|0070": "SIEMENS",
        "0008|1090": "Skyra",
        "0010|0020": "10000",
        "0010|0040": "M",
        "0010|1010": age,
        "0012|0062": "YES",
    }.items():
        image.SetMetaData(tag, value)
    return image


def test_patient_name_is_a_fixed_male_person_name(identity):
    name = identity.patient_name("10000")
    last, first = name.split("^")
    assert last
    assert first
    assert identity.patient_name("10000") == name
    assert identity.patient_name("10001") != name


def test_referring_physician_is_fixed_per_study(identity):
    a = identity.referring_physician_name("10193", "1000196")
    assert a == identity.referring_physician_name("10193", "1000196")
    assert a != identity.referring_physician_name("10193", "1000197")
    assert "^" in a


def age_on(dob: date, day: date) -> int:
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def test_birth_date_makes_the_patient_exactly_that_age_on_the_study_date(identity):
    study = date(2019, 7, 2)
    dob = identity.birth_date(study, 73, "10000")
    assert age_on(dob, study) == 73
    assert dob.year in (2019 - 73, 2019 - 74)
    assert dob == identity.birth_date(study, 73, "10000")
    # A patient scanned again keeps the same birthday; the year follows the age PI-CAI recorded.
    later = identity.birth_date(date(2020, 3, 15), 74, "10000")
    assert (later.month, later.day) == (dob.month, dob.day)
    assert age_on(later, date(2020, 3, 15)) == 74
    # Different patients, different birthdays (almost surely) — at least not the same date.
    assert identity.birth_date(study, 73, "10001") != dob


def test_generators_leave_the_global_rng_untouched(identity):
    random.seed(1234)
    expected = random.random()
    random.seed(1234)
    identity.patient_name("10000")
    identity.referring_physician_name("10000", "1000000")
    assert random.random() == expected


def test_converter_writes_the_synthetic_identity(converter, identity, tmp_path):
    converter.write_dicom_series(picai_image(), tmp_path / "t2w", "10000", "1000000", "t2w", "PCNN")
    ds = pydicom.dcmread(sorted((tmp_path / "t2w").glob("*.dcm"))[0])

    assert str(ds.PatientName) == identity.patient_name("10000")
    assert str(ds.ReferringPhysicianName) == identity.referring_physician_name("10000", "1000000")
    assert ds.StudyDescription == identity.STUDY_DESCRIPTION
    assert ds.PatientSex == "M"
    assert ds.PatientBirthDate == identity.birth_date(date(2019, 7, 2), 73, "10000").strftime("%Y%m%d")
    # What PI-CAI did carry is untouched.
    assert ds.PatientID == "10000"
    assert ds.AccessionNumber == "10000_1000000"
    assert ds.PatientAge == "073Y"
    assert ds.ClinicalTrialSiteID == "PCNN"


def test_identity_is_identical_across_two_conversions_and_across_modalities(converter, tmp_path):
    for run in ("first", "second"):
        for modality in ("t2w", "adc"):
            converter.write_dicom_series(picai_image(), tmp_path / run / modality, "10000", "1000000", modality)
    read = {
        (run, modality): pydicom.dcmread(sorted((tmp_path / run / modality).glob("*.dcm"))[0])
        for run in ("first", "second")
        for modality in ("t2w", "adc")
    }
    identity_tags = ("PatientName", "PatientBirthDate", "ReferringPhysicianName", "StudyDescription", "PatientSex")
    values = {key: tuple(str(ds.get(tag)) for tag in identity_tags) for key, ds in read.items()}
    assert len(set(values.values())) == 1, values
    assert read[("first", "t2w")].SOPInstanceUID == read[("second", "t2w")].SOPInstanceUID


def test_birth_date_is_omitted_rather_than_invented_when_age_is_not_in_years(converter, tmp_path):
    converter.write_dicom_series(picai_image(age="876M"), tmp_path / "t2w", "10000", "1000000", "t2w")
    ds = pydicom.dcmread(sorted((tmp_path / "t2w").glob("*.dcm"))[0])
    # GDCM writes the Type 2 tag present-but-empty; what matters is that no date was invented.
    assert str(ds.get("PatientBirthDate", "")) == ""
    assert str(ds.PatientName)
