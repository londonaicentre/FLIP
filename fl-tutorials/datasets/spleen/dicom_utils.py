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

import random
from datetime import datetime, timedelta

import names

MANUFACTURER = [
    "GE Healthcare",
    "Philips Healthcare",
    "Siemens Healthineers",
    "Canon Medical Systems",
    "Hitachi Healthcare",
    "Toshiba Medical Systems",
]

MODELS = [
    "BrightSpeed",
    "Discovery",
    "LightSpeed",
    "Optima CT660",
    "MX 16-slice",
    "Access-32",
    "Aquilion",
    "Celesteion",
    "Galaxy",
    "Vantage",
]

DEPARTMENTS = [
    "Radiology",
    "Neuroradiology",
    "CT",
    "Other Department",
]

INSTITUTIONS = [
    "KCH_Neuroradiology",
    "Kings_College_Hospital",
    "KING_S_COLLEGE_HOSPITAL",
    "KINGS_COLLEGE_HOSPITAL",
    "PRINCESS_ROYAL",
    "KINGS_COLLEGE",
    "Blackheath_Hospital",
    "King_s_College_Hospital",
]


def random_patient_id():
    return random_nhs_number()


def random_nhs_number() -> str:
    """Generate a random 10 digit nhs_number with the tenth digit a checksum of the first nine.

    NHS numbers must start from 1, not 0, and cannot have a check digit of 10.
    Returns NHS nhs_number in string format 'xxx xxx xxxx'."""
    nhs_num = "000000000"
    check_digit = 10
    while (nhs_num == "000000000") or (check_digit == 10):
        nhs_num = ""
        checksum = 0
        for digit in range(9):
            value = random.randrange(10)
            nhs_num += str(value)
            checksum += value * (10 - digit)
        check_digit = checksum % 11
        check_digit = 11 - check_digit
        if check_digit == 11:
            check_digit = 0
    nhs_num += str(check_digit)
    nhs_num = " ".join([nhs_num[0:3], nhs_num[3:6], nhs_num[6:]])
    return nhs_num


def random_datetime(start, end, prop):
    """Get a datetime at a proportion of a range of two datetimes.

    start and end should be datetime objects giving an interval [start, end].
    prop specifies how a proportion of the interval to be taken after
    start.  The returned time will be a datetime object.
    """
    ptime = start + prop * (end - start)
    return ptime


def random_gender():
    return random.sample(["male", "female"], 1)[0]


def get_sex(gender: str) -> str:
    names_to_dicom = {"male": "M", "female": "F"}
    trans = {"M": "F", "F": "M"}

    # From https://practicalandrogyny.com/2014/12/16/how-many-people-in-the-uk-are-nonbinary/#ehrc-gender-identity
    # Of 10,039 surveyed, 100 stated gender reassignment, of which 12 also identified as non-binary
    # An additional 26 identified as non-binary but did not state gender reassignment
    if random.random() < 100 / 10039:
        if random.random() < 12 / 100:
            sex = "O"
        else:
            sex = trans[names_to_dicom[gender]]
    else:
        if random.random() < 26 / 9939:
            sex = "O"
        else:
            sex = names_to_dicom[gender]
    return sex


def random_date_of_birth(datetime_format: str = "%d/%m/%Y %H:%M:%S") -> str:
    dob = random_datetime(
        datetime.strptime("1/1/1950 00:00:00", datetime_format),
        datetime.strptime("31/12/1999 23:59:59", datetime_format),
        random.random(),
    )
    return dob.strftime("%Y%m%d")


def random_study_date(datetime_format: str = "%d/%m/%Y %H:%M:%S") -> datetime:
    study_date = random_datetime(
        datetime.strptime("1/1/2000 00:00:00", datetime_format),
        datetime.strptime("31/12/2019 23:59:59", datetime_format),
        random.random(),
    )
    return study_date


def random_series_date(study_date: datetime) -> datetime:
    series_date = study_date + timedelta(minutes=random.randint(1, 100) * 5)
    return series_date


def random_manufacturer():
    return random.sample(MANUFACTURER, 1)[0]


def random_manufacturer_model_name():
    return random.sample(MODELS, 1)[0]


def random_institution():
    return random.sample(INSTITUTIONS, 1)[0]


def random_department():
    return random.sample(DEPARTMENTS, 1)[0]


def random_patient_name(gender: str = None) -> str:
    return names.get_full_name(gender=gender)


def random_referring_physician_name() -> str:
    return names.get_full_name()


def random_accession_number():
    accession_number = f"{datetime.now().microsecond:06d}"
    accession_number = f"FAK{accession_number}{random.randint(0, 99):02d}"
    return accession_number
