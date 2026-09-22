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

"""Synthetic patient identities that are pure functions of a case id (FLIP#1261).

A public research volume (MSD spleen, say) carries no patient: no name, no NHS number, no
dates, no referrer, no scanner. A trust's PACS study does, and the trusts' imaging-api parses those
tags out of every C-FIND answer, so the mock DICOM sets synthesise a whole patient population. None of
it belongs to a real person.

Every value here is derived from the dataset's own case id (``spleen_2``, ``BRATS_001``) through a
``random.Random`` seeded with that id and the value's own namespace, never from the global RNG, the
clock, or the order in which cases are processed. Two consequences that the DICOM regeneration path
depends on:

* re-converting a case on any machine, on any day, writes the same identity, so a DICOM set can be
  regenerated from its public source instead of being re-hosted (the published OMOP tables keep
  describing it); and
* adding a draw to one generator, or converting a different subset of cases, never shifts another
  generator's output — each is seeded independently.

``names`` (US census name lists) draws from Python's global RNG; the two name generators seed it and
put its state back afterwards, so nothing else in the process is disturbed.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TypeVar

import names

T = TypeVar("T")

# Institution and department labels as spleen's original `dicom_utils.py` used them — deliberately
# in the mixed, inconsistent spellings a real PACS accumulates.
INSTITUTIONS: tuple[str, ...] = (
    "KCH_Neuroradiology",
    "Kings_College_Hospital",
    "KING_S_COLLEGE_HOSPITAL",
    "KINGS_COLLEGE_HOSPITAL",
    "PRINCESS_ROYAL",
    "KINGS_COLLEGE",
    "Blackheath_Hospital",
    "King_s_College_Hospital",
)
DEPARTMENTS: tuple[str, ...] = ("Radiology", "Neuroradiology", "CT", "Other Department")

# (Manufacturer, ManufacturerModelName) pairs that exist — spleen's original lists were sampled
# independently, which produced Siemens-branded GE scanners in the published table.
CT_SCANNERS: tuple[tuple[str, str], ...] = (
    ("GE Healthcare", "BrightSpeed"),
    ("GE Healthcare", "Discovery CT750 HD"),
    ("GE Healthcare", "LightSpeed VCT"),
    ("GE Healthcare", "Optima CT660"),
    ("Philips Healthcare", "MX 16-slice"),
    ("Philips Healthcare", "Brilliance 64"),
    ("Siemens Healthineers", "SOMATOM Definition AS"),
    ("Siemens Healthineers", "SOMATOM Force"),
    ("Canon Medical Systems", "Aquilion ONE"),
    ("Canon Medical Systems", "Celesteion"),
)
# (Manufacturer, ManufacturerModelName, MagneticFieldStrength in tesla).
MR_SCANNERS: tuple[tuple[str, str, float], ...] = (
    ("Siemens Healthineers", "MAGNETOM Skyra", 3.0),
    ("Siemens Healthineers", "MAGNETOM Prisma", 3.0),
    ("Siemens Healthineers", "MAGNETOM Aera", 1.5),
    ("GE Healthcare", "SIGNA Premier", 3.0),
    ("GE Healthcare", "Discovery MR750", 3.0),
    ("GE Healthcare", "SIGNA Artist", 1.5),
    ("Philips Healthcare", "Ingenia", 3.0),
    ("Philips Healthcare", "Achieva", 1.5),
    ("Canon Medical Systems", "Vantage Galan 3T", 3.0),
)

_SEED_ROOT = "flip-synthetic-identity"


def _rng(namespace: str, case_id: str) -> random.Random:
    """A generator seeded by (namespace, case id) only — string seeds hash with SHA-512, so this is stable
    across processes and machines regardless of ``PYTHONHASHSEED``."""
    return random.Random(f"{_SEED_ROOT}/{namespace}/{case_id}")


def _with_seeded_global_rng(seed: str, draw: Callable[[], T]) -> T:
    """Run ``draw()`` with the global RNG seeded from ``seed``, then put the global RNG back as it was."""
    state = random.getstate()
    try:
        random.seed(seed)
        return draw()
    finally:
        random.setstate(state)


def nhs_number(case_id: str) -> str:
    """A valid 10-digit NHS number, ``"xxx xxx xxxx"``, fixed by the case id.

    The tenth digit is the modulus-11 check digit of the first nine (weights 10..2); a check digit of
    10 is invalid and an all-zero body is not a number, so both are redrawn. The first digit is never 0,
    so the nine-digit ``person_id`` the OMOP converters derive from it always has nine digits.
    """
    rng = _rng("nhs-number", case_id)
    while True:
        digits = [rng.randrange(1, 10)] + [rng.randrange(10) for _ in range(8)]
        check = 11 - sum(d * (10 - i) for i, d in enumerate(digits)) % 11
        if check == 11:
            check = 0
        if check == 10:
            continue
        body = "".join(str(d) for d in digits) + str(check)
        return f"{body[0:3]} {body[3:6]} {body[6:]}"


def person_id(nhs: str) -> int:
    """The OMOP ``person_id`` the converters derive from a PatientID: the first nine digits of the NHS number."""
    return int(nhs.replace(" ", "")[:9])


def sex(case_id: str) -> str:
    """DICOM PatientSex, ``M`` or ``F``."""
    return _rng("sex", case_id).choice("MF")


def patient_name(case_id: str, patient_sex: str) -> str:
    """``LAST^FIRST`` (a DICOM person name) for the patient, fixed by the case id and consistent with its sex."""
    gender = "male" if patient_sex == "M" else "female"
    return _with_seeded_global_rng(
        f"{_SEED_ROOT}/patient-name/{case_id}",
        lambda: f"{names.get_last_name()}^{names.get_first_name(gender=gender)}",
    )


def referring_physician(case_id: str) -> str:
    """``LAST^FIRST`` for the referring physician of the case's study."""
    return _with_seeded_global_rng(
        f"{_SEED_ROOT}/referring-physician/{case_id}",
        lambda: f"{names.get_last_name()}^{names.get_first_name()}",
    )


def study_datetime(case_id: str, years: tuple[int, int] = (2010, 2019)) -> datetime:
    """When the study happened: a second drawn uniformly from the inclusive year window."""
    start = datetime(years[0], 1, 1)
    end = datetime(years[1], 12, 31, 23, 59, 59)
    return start + timedelta(seconds=_rng("study-datetime", case_id).randrange(int((end - start).total_seconds()) + 1))


def birth_date(case_id: str, study_date: date, age_range: tuple[int, int] = (18, 80)) -> date:
    """A birth date on which the patient was ``age`` years old on ``study_date``, ``age`` drawn from ``age_range``.

    The birthday (month and day) and the age are both fixed by the case id; the year is whatever makes
    that age exact on the study date, so a case's birthday never moves if its study date does.
    """
    rng = _rng("birth-date", case_id)
    birthday = date(2001, 1, 1) + timedelta(days=rng.randrange(365))  # a non-leap year: never 29 February
    age = rng.randint(*age_range)
    year = study_date.year - age
    if (birthday.month, birthday.day) > (study_date.month, study_date.day):
        year -= 1  # this year's birthday is still to come, so the patient was born a year earlier
    return date(year, birthday.month, birthday.day)


def accession_number(case_id: str) -> str:
    """``FAK`` + eight digits: the shape the shipped mock sets use, and only RFC 3986 unreserved characters, which
    the trusts' imaging-api requires of an accession before it becomes an XNAT session label."""
    return f"FAK{_rng('accession-number', case_id).randrange(10**8):08d}"


def institution(case_id: str) -> str:
    """The InstitutionName the study was acquired at."""
    return _rng("institution", case_id).choice(INSTITUTIONS)


def department(case_id: str) -> str:
    """The InstitutionalDepartmentName that acquired the study."""
    return _rng("department", case_id).choice(DEPARTMENTS)


@dataclass(frozen=True)
class Scanner:
    """The equipment a series was acquired on."""

    manufacturer: str
    model: str
    field_strength: float | None  # tesla for MR; None for CT


def scanner(case_id: str, modality: str) -> Scanner:
    """The scanner for a case's study, drawn from the modality's table.

    Raises:
        KeyError: For a modality with no scanner table — add one rather than reusing another's.
    """
    rng = _rng(f"scanner-{modality}", case_id)
    if modality == "MR":
        manufacturer, model, tesla = rng.choice(MR_SCANNERS)
        return Scanner(manufacturer, model, tesla)
    if modality == "CT":
        manufacturer, model = rng.choice(CT_SCANNERS)
        return Scanner(manufacturer, model, None)
    raise KeyError(f"no scanner table for modality {modality!r}; add one to synthetic_identity.py")


@dataclass(frozen=True)
class Identity:
    """Everything a mock study needs about its patient and its visit, all fixed by ``case_id``."""

    case_id: str
    patient_id: str  # the NHS number, space-formatted, as PatientID
    patient_name: str  # DICOM PN
    sex: str
    birth_date: date
    study_datetime: datetime
    accession_number: str
    referring_physician: str  # DICOM PN
    institution: str
    department: str


def identity(case_id: str) -> Identity:
    """The full synthetic identity of one case, every field from the generators above."""
    patient_sex = sex(case_id)
    when = study_datetime(case_id)
    return Identity(
        case_id=case_id,
        patient_id=nhs_number(case_id),
        patient_name=patient_name(case_id, patient_sex),
        sex=patient_sex,
        birth_date=birth_date(case_id, when.date()),
        study_datetime=when,
        accession_number=accession_number(case_id),
        referring_physician=referring_physician(case_id),
        institution=institution(case_id),
        department=department(case_id),
    )
