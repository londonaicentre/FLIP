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

"""The synthetic identities: pure functions of a case id, valid where the platform checks them.

A DICOM set that is regenerated rather than re-hosted (FLIP#1261) only matches its published OMOP
tables if every identity comes out the same on every run, on every machine. These tests pin that,
plus the two formats the platform validates: NHS-number check digits (spleen's ``dicom_utils``
algorithm) and RFC 3986 unreserved accession numbers (imaging-api's session-label guard).
"""

from __future__ import annotations

import random
import re
import string
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
sys.path.insert(0, str(DATASETS_DIR))

from utils import synthetic_identity as si  # noqa: E402

# imaging-api rejects a session label outside this set (routers/schemas.py, FLIP#1137).
URL_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
BRAIN_CASES = [f"BRATS_{i:03d}" for i in range(1, 485)]
SPLEEN_CASES = [f"spleen_{i}" for i in range(2, 64)]


def _nhs_check_digit(digits: str) -> int:
    """Modulus 11 over the first nine digits, weights 10..2; 11 → 0; 10 is invalid."""
    checksum = sum(int(d) * (10 - i) for i, d in enumerate(digits[:9]))
    check = 11 - checksum % 11
    return 0 if check == 11 else check


class TestNhsNumber:
    def test_is_formatted_and_carries_a_valid_check_digit(self):
        number = si.nhs_number("BRATS_001")
        assert re.fullmatch(r"\d{3} \d{3} \d{4}", number)
        digits = number.replace(" ", "")
        assert int(digits[9]) == _nhs_check_digit(digits)
        assert digits[:9] != "000000000"

    def test_is_a_pure_function_of_the_case_id(self):
        assert si.nhs_number("BRATS_001") == si.nhs_number("BRATS_001")
        assert si.nhs_number("BRATS_001") != si.nhs_number("BRATS_002")

    def test_person_ids_are_unique_across_both_datasets(self):
        ids = [si.person_id(si.nhs_number(case)) for case in BRAIN_CASES + SPLEEN_CASES]
        assert len(set(ids)) == len(ids)
        assert all(100_000_000 <= i <= 999_999_999 for i in ids), "nine digits, never a leading zero"

    def test_person_id_is_the_first_nine_digits(self):
        assert si.person_id("123 456 7890") == 123456789


class TestAccessionNumber:
    def test_shape_and_charset(self):
        accession = si.accession_number("BRATS_001")
        assert re.fullmatch(r"FAK\d{8}", accession)
        assert set(accession) <= URL_UNRESERVED

    def test_unique_across_both_datasets(self):
        accessions = [si.accession_number(case) for case in BRAIN_CASES + SPLEEN_CASES]
        assert len(set(accessions)) == len(accessions)


class TestNamesAndSex:
    def test_sex_is_m_or_f_and_stable(self):
        assert si.sex("BRATS_001") in {"M", "F"}
        assert si.sex("BRATS_001") == si.sex("BRATS_001")
        assert {si.sex(case) for case in BRAIN_CASES} == {"M", "F"}, "a cohort has both sexes"

    def test_patient_name_is_a_dicom_person_name(self):
        name = si.patient_name("BRATS_001", si.sex("BRATS_001"))
        last, first = name.split("^")
        assert last
        assert first
        assert name == si.patient_name("BRATS_001", si.sex("BRATS_001"))

    def test_referring_physician_is_a_dicom_person_name(self):
        last, first = si.referring_physician("BRATS_001").split("^")
        assert last
        assert first

    def test_the_global_rng_is_left_untouched(self):
        """`names` draws from Python's global RNG; seeding it must not disturb the caller's stream."""
        random.seed(1234)
        before = random.getstate()
        si.patient_name("BRATS_001", "F")
        si.referring_physician("BRATS_001")
        assert random.getstate() == before


class TestDates:
    def test_study_datetime_is_inside_the_window_and_stable(self):
        when = si.study_datetime("BRATS_001")
        assert isinstance(when, datetime)
        assert 2010 <= when.year <= 2019
        assert when == si.study_datetime("BRATS_001")

    def test_study_datetime_varies_between_cases(self):
        assert len({si.study_datetime(case) for case in BRAIN_CASES[:20]}) > 1

    def test_birth_date_makes_an_adult_on_the_study_date(self):
        study = date(2015, 6, 1)
        for case in BRAIN_CASES[:50]:
            dob = si.birth_date(case, study)
            age = study.year - dob.year - ((study.month, study.day) < (dob.month, dob.day))
            assert 18 <= age <= 80, f"{case}: age {age} on {study}"

    def test_birthday_is_fixed_by_the_case_id(self):
        first = si.birth_date("BRATS_001", date(2012, 3, 4))
        second = si.birth_date("BRATS_001", date(2018, 11, 30))
        assert (first.month, first.day) == (second.month, second.day)


class TestSiteAndScanner:
    def test_institution_and_department_come_from_the_shared_lists(self):
        assert si.institution("BRATS_001") in si.INSTITUTIONS
        assert si.department("BRATS_001") in si.DEPARTMENTS

    @pytest.mark.parametrize("modality", ["MR", "CT"])
    def test_scanner_is_a_real_pairing_for_the_modality(self, modality):
        scanner = si.scanner("BRATS_001", modality)
        assert scanner.manufacturer
        assert scanner.model
        table = si.MR_SCANNERS if modality == "MR" else si.CT_SCANNERS
        assert (scanner.manufacturer, scanner.model) in {(m, model) for m, model, *_ in table}
        if modality == "MR":
            assert scanner.field_strength in {1.5, 3.0}
        else:
            assert scanner.field_strength is None

    def test_unknown_modality_is_refused(self):
        with pytest.raises(KeyError):
            si.scanner("BRATS_001", "US")


class TestIdentityBundle:
    def test_identity_bundles_every_field_consistently(self):
        identity = si.identity("BRATS_001")
        assert identity.case_id == "BRATS_001"
        assert identity.patient_id == si.nhs_number("BRATS_001")
        assert identity.sex == si.sex("BRATS_001")
        assert identity.patient_name == si.patient_name("BRATS_001", identity.sex)
        assert identity.accession_number == si.accession_number("BRATS_001")
        assert identity.study_datetime == si.study_datetime("BRATS_001")
        assert identity.birth_date == si.birth_date("BRATS_001", identity.study_datetime.date())
        assert identity.referring_physician == si.referring_physician("BRATS_001")
        assert identity.institution == si.institution("BRATS_001")
        assert identity.department == si.department("BRATS_001")

    def test_identity_is_frozen_and_equal_across_calls(self):
        assert si.identity("BRATS_001") == si.identity("BRATS_001")
        with pytest.raises(AttributeError):
            si.identity("BRATS_001").sex = "M"  # type: ignore[misc]
