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

"""A synthetic patient identity for each PI-CAI study, the same on every run.

PI-CAI ships anonymised research DICOM: an opaque ``PatientID``, sex and age, and nothing a
hospital PACS study would carry — no patient name, no birth date, no referring physician, no study
description. The trusts' imaging-api rejects a study without those (they are what a real study has),
and the spleen and cxr mock sets synthesise a whole patient population for the same reason. This
module does that for prostate, with one difference from spleen's generators: every value is a pure
function of the PI-CAI identifiers, so re-converting a study on any machine, on any day, writes the
same name, the same birth date and the same referrer, and the published DICOM set stays
reproducible byte for byte (the UIDs already are — see ``convert_mha_to_dicom.py``).

Names come from the ``names`` package (US census lists), which draws from Python's global ``random``;
each generator seeds that RNG from the identifiers and restores its state afterwards, so nothing
else in the process is disturbed. None of this belongs to a real person.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import names

# The LOINC procedure the OMOP export records for every study (3047951, "MR Prostate WO contrast");
# the same words on the DICOM so the two never say different things about the same scan.
STUDY_DESCRIPTION = "MR Prostate WO contrast"


def _with_seeded_global_rng(seed: str, draw):
    """Run ``draw()`` with the global RNG seeded from ``seed``, then put the RNG back as it was."""
    state = random.getstate()
    try:
        random.seed(seed)
        return draw()
    finally:
        random.setstate(state)


def patient_name(patient_id: str) -> str:
    """``LAST^FIRST`` (a DICOM PN) for a male patient — PI-CAI is a prostate cohort — fixed by the id.

    Args:
        patient_id: The PI-CAI ``patient_id``.

    Returns:
        str: The same name for the same id on every call and every run.
    """
    return _with_seeded_global_rng(
        f"picai-patient-{patient_id}",
        lambda: f"{names.get_last_name()}^{names.get_first_name(gender='male')}",
    )


def referring_physician_name(patient_id: str, study_id: str) -> str:
    """``LAST^FIRST`` for the referring physician of one study, fixed by the study's ids."""
    return _with_seeded_global_rng(
        f"picai-referrer-{patient_id}-{study_id}",
        lambda: f"{names.get_last_name()}^{names.get_first_name()}",
    )


def birth_date(study_date: date, age_years: int, patient_id: str) -> date:
    """A birth date on which the patient was exactly ``age_years`` old on ``study_date``.

    The birthday (month and day) is fixed by the id, so a patient scanned twice keeps one birthday;
    the year is whichever makes the recorded age exact on this study's date — ``study_date.year -
    age_years`` when the birthday has already passed that year, one earlier otherwise. That is also
    the ``year_of_birth`` the OMOP export derives from the marksheet age, up to the same one-year
    ambiguity any age in whole years carries.

    Args:
        study_date: The acquisition date from the ``.mha`` header.
        age_years: PatientAge from the same header, in whole years.
        patient_id: The PI-CAI ``patient_id``.

    Returns:
        date: The synthetic birth date.
    """
    day_of_year = random.Random(f"picai-dob-{patient_id}").randrange(365)  # never 29 February
    birthday = date(2001, 1, 1) + timedelta(days=day_of_year)  # a non-leap year to pick month/day from
    year = study_date.year - age_years
    if (birthday.month, birthday.day) > (study_date.month, study_date.day):
        year -= 1  # this year's birthday is still to come, so the patient was born a year earlier
    return date(year, birthday.month, birthday.day)
