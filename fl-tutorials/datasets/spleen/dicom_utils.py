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

import decimal
import random
from datetime import datetime, timedelta
from pathlib import Path

import names
import pandas as pd
import pydicom
from PIL import Image
from pydicom import Dataset
from pydicom.dataset import FileMetaDataset
from pydicom.uid import UID, ImplicitVRLittleEndian, generate_uid

IMPLEMENTATION_CLASS_UID = UID("1.2.40.0.13.1.1.1")
DICOM_PDF_CLASS_UID = UID("1.2.840.10008.5.1.4.1.1.1")

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


def random_height() -> float:
    # sample a random number between 150 and 210 cm using random.random() and scale it to the range
    return round(150 + random.random() * 60, 2)


def random_weight() -> float:
    # sample a random number between 40 and 150 kg using random.random() and scale it to the range
    return round(40 + random.random() * 110, 1)


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


def calculate_age(dob: str, study_date: datetime) -> str:
    # difference between study_date and dob in years
    # Convert string back to datetime
    dob = datetime.strptime(dob, "%Y%m%d")
    age = study_date.year - dob.year - ((study_date.month, study_date.day) < (dob.month, dob.day))
    age = f"{age:03}Y"
    return age


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


def add_random_patient_info(
    dataset: Dataset,
    datetime_format: str = "%d/%m/%Y %H:%M:%S",
    study_description: str = "Chest CT",
    protocol_name: str = "Chest HCT",
) -> Dataset:
    """Add random patient information to DICOM dataset."""
    subject_num = random.randint(1, 1000000)
    hospital_num = f"C{subject_num:06d}"

    gender = random_gender()
    sex = get_sex(gender=gender)
    full_name = random_patient_name(gender=gender)

    study_date = random_study_date(datetime_format=datetime_format)
    dob = random_date_of_birth(datetime_format=datetime_format)
    age = calculate_age(dob, study_date)

    # Estimate series time based on series number
    series_number = None
    if "SeriesNumber" in dataset and str(dataset.SeriesNumber).isdecimal():
        series_number = int(dataset.SeriesNumber)
    else:
        series_number = decimal.Decimal(random.random() * 1e6).quantize(decimal.Decimal("1"))
        # raise ValueError(f"Series number not found in {filename}.")
    series_date = study_date + timedelta(minutes=int(series_number) * 5)

    dataset.SeriesNumber = series_number
    dataset.PatientName = full_name
    dataset.PatientSex = sex
    dataset.PatientSize = random_height()
    dataset.PatientWeight = random_weight()
    dataset.PatientBirthDate = dob
    dataset.PatientAge = age
    dataset.PatientID = random_nhs_number()
    dataset.OtherPatientIDs = hospital_num
    dataset.InstitutionName = random_institution()
    dataset.ReferringPhysicianName = random_referring_physician_name()
    dataset.InstitutionalDepartmentName = random_department()
    dataset.StudyDate = study_date.strftime("%Y%m%d")
    dataset.StudyTime = study_date.strftime("%H%M%S")
    dataset.StudyDescription = study_description
    dataset.SeriesDate = series_date.strftime("%Y%m%d")
    dataset.SeriesTime = series_date.strftime("%H%M%S")
    dataset.Manufacturer = random_manufacturer()
    dataset.ManufacturerModelName = random_manufacturer_model_name()
    dataset.ProtocolName = protocol_name
    dataset.AccessionNumber = random_accession_number()
    return dataset


def create_min_dicom_specifications(dataset: Dataset) -> Dataset:
    sop_uid = generate_uid()

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = DICOM_PDF_CLASS_UID
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.ImplementationClassUID = IMPLEMENTATION_CLASS_UID
    file_meta.TransferSyntaxUID = ImplicitVRLittleEndian

    series_uid = generate_uid()
    dataset.file_meta = file_meta
    dataset.SOPInstanceUID = sop_uid
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = series_uid
    dataset.InstanceNumber = 1

    dt = datetime.now()
    dataset.ContentDate = dt.strftime("%Y%m%d")
    dataset.ContentTime = dt.strftime("%H%M%S.%f")

    dataset.SOPClassUID = DICOM_PDF_CLASS_UID

    return dataset


def save_dicom(
    output_path: str | Path,
    dataset: Dataset,
) -> str:
    dataset.save_as(output_path, enforce_file_format=True)
    return str(output_path)


def encapsulate_jpeg_pixel_data(dataset, input_path: str | Path) -> Dataset:
    """Encapsulate JPEG pixel data into DICOM file."""
    # Set specifications for a X-Ray Monochromatic JPEG image
    dataset.Modality = "CR"

    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsStored = 8
    dataset.SamplesPerPixel = 1
    dataset.BitsAllocated = 8
    dataset.HighBit = dataset.BitsStored - 1
    dataset.PixelRepresentation = 0

    # # Add orientation and positioning metadata
    dataset.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]  # L-R (rows), S-I (columns)
    dataset.ImagePositionPatient = [0.0, 0.0, 0.0]  # Origin in patient coordinates
    dataset.PatientPosition = "HFS"  # Head-First Supine for chest X-rays

    # # Add other sizing metadata
    dataset.PixelSpacing = [0.143, 0.143]  # Pixel spacing in mm
    dataset.SliceThickness = 1.0  # Slice thickness in mm

    # # Add rescale and display tags
    dataset.RescaleIntercept = 0.0
    dataset.RescaleSlope = 1.0
    dataset.WindowCenter = 128  # Center of grayscale range for 8-bit images
    dataset.WindowWidth = 256  # Width of grayscale range for 8-bit images

    # Encapsulate JPEG pixel data
    pixel_data = Image.open(input_path)
    dataset.Rows = pixel_data.height
    dataset.Columns = pixel_data.width
    dataset.PixelData = pixel_data.tobytes()
    return dataset


def jpeg2dicom(input_path: str | Path, output_dir: str | Path, add_random_info: bool = True) -> str:
    """Convert JPEG image to DICOM file.

    This function encapsulates JPEG pixel data into a DICOM file.
    It is only intended to work with CT X-Ray i

    Parameters
    ----------
    input_path : str | Path
        Path to JPEG image.
    output_dir : str | Path
        Path to output directory.
    add_random_info : bool, optional
        Add random patient information to DICOM file, by default True.

    Returns
    -------
    str
        Path to DICOM file.
    """
    input_path = Path(input_path).resolve() if isinstance(input_path, str) else input_path
    dataset = Dataset()
    dataset = create_min_dicom_specifications(dataset)
    dataset = encapsulate_jpeg_pixel_data(dataset, input_path)
    if add_random_info:
        dataset = add_random_patient_info(dataset)
    output_path = save_dicom(output_dir, dataset)
    return output_path


def extract_dcm_data(dcm_path: str | Path) -> dict:
    """Extract DICOM data from DICOM dataset.

    Parameters
    ----------
    dcm_path : str | Path
        Path to DICOM file.

    Returns
    -------
    dict
        Extracted DICOM data.
    """
    ds = pydicom.dcmread(dcm_path)
    return {var.keyword: str(var.value) for var in ds.iterall() if var.keyword != "PixelData"}


def convert_dataset(img_dir: str | Path, output_dir: str | Path | None = None) -> pd.DataFrame:
    """Convert all JPEG images in a directory to DICOM files.

    Parameters
    ----------
    img_dir : str | Path
        Path to directory containing JPEG images. It will recursively search for all JPEG images in the directory.

    Returns
    -------
    pd.DataFrame
        DataFrame containing extracted DICOM data for which the index is the image filename.
    """
    img_dir = Path(img_dir)
    if output_dir is None:
        output_dir = img_dir / "dicom"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)
    df = pd.DataFrame()
    for i, img_path in enumerate(img_dir.rglob("*.jpg")):
        output_path = output_dir / img_path.relative_to(img_dir).with_suffix(".dcm")
        output_path = jpeg2dicom(img_path, output_path)
        dcm_data = extract_dcm_data(output_path)
        row = pd.Series(dcm_data)
        row.name = img_path.stem
        if i == 0:
            df = pd.DataFrame(columns=row.index)
            df.columns = row.index
        df.loc[img_path.stem] = row
    df.index.name = "FileName"
    return df
