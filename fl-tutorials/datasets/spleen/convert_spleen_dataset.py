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

"""Convert the MSD spleen NIfTI volumes to DICOM, mocking the tags a PACS expects.

Imported from ``londonaicentre/flip_project_spleen_segmentation`` (FLIP#1092). Behaviour is
unchanged from that repo.

Workstation-only: ``pyplastimatch.install_precompiled_binaries()`` installs to ``/usr/local/bin``,
so this often needs root and is deliberately excluded from CI. See the datasets README.
"""

import os
import random
import shutil

import dicom_utils as dicom
import pydicom
import pyplastimatch as pypla
from pyplastimatch.utils.install import install_precompiled_binaries

if __name__ == "__main__":
    random.seed(42)

    # Install precompiled binaries
    install_precompiled_binaries()

    # Define input and output directories
    input_dir = "data/Task09_Spleen/imagesTr"
    output_dir = "dicom_output"

    # Define metadata
    study_description = "Spleen CT"
    series_description = "CT Spleen"
    modality = "CT"

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Loop through all files in input directory
    for filename in sorted(os.listdir(input_dir)):
        if filename.endswith(".nii.gz") and not filename.startswith("._"):  # Process only NIFTI files
            # e.g. spleen_10
            subject = os.path.splitext(os.path.splitext(filename)[0])[0]  # Extract patient ID

            # Skip hidden files
            if subject.startswith("._"):
                continue
            print(f"Processing: {subject}")

            input_path = os.path.join(input_dir, filename)

            # Metadata
            patient_id = dicom.random_patient_id()
            gender = dicom.random_gender()
            patient_name = dicom.random_patient_name(gender=gender)
            patient_birth_date = dicom.random_date_of_birth()
            patient_sex = dicom.get_sex(gender=gender)

            accession_number = dicom.random_accession_number()
            study_date = dicom.random_study_date()
            series_date = dicom.random_series_date(study_date=study_date)
            manufacturer = dicom.random_manufacturer()
            manufacturer_model = dicom.random_manufacturer_model_name()
            institution_name = dicom.random_institution()
            referring_physician_name = dicom.random_referring_physician_name()
            department_name = dicom.random_department()

            convert_args_ct = {
                "input": input_path,
                "modality": modality,
                "patient-id": patient_id,
                "patient-name": patient_name,
                "output-dicom": os.path.join(output_dir, subject),
                # "series-description": series_description,
                # "study-description": study_description,
                # "metadata": metadata
            }

            shutil.rmtree(convert_args_ct["output-dicom"], ignore_errors=True)

            # Ensure patient-specific output directory exists
            print(f"Creating folder for {convert_args_ct['output-dicom']}")
            os.makedirs(convert_args_ct["output-dicom"])

            print(f"Converting {filename} to DICOM for patient: {subject}")
            pypla.convert(verbose=True, **convert_args_ct)

            # Now load the converted DICOM files set remaining metadata
            dicom_files = os.listdir(convert_args_ct["output-dicom"])
            for dicom_file in dicom_files:
                dicom_path = os.path.join(convert_args_ct["output-dicom"], dicom_file)
                # use pydicom to load the file
                ds = pydicom.dcmread(dicom_path)
                # set the remaining metadata
                ds.StudyDate = study_date.strftime("%Y%m%d")
                ds.SeriesDate = series_date.strftime("%Y%m%d")
                ds.StudyTime = study_date.strftime("%H%M%S")
                ds.SeriesTime = series_date.strftime("%H%M%S")
                ds.AccessionNumber = accession_number
                ds.Manufacturer = manufacturer
                ds.ManufacturerModelName = manufacturer_model
                ds.InstitutionName = institution_name
                ds.StudyDescription = study_description
                ds.SeriesDescription = series_description
                ds.ProtocolName = series_description
                ds.ReferringPhysicianName = referring_physician_name
                ds.DepartmentName = department_name
                ds.PatientBirthDate = patient_birth_date
                ds.PatientSex = patient_sex
                # save the file
                ds.save_as(dicom_path)

            # Zip the output directory for each patient
            # shutil.make_archive(os.path.join(output_dir, patient_id), 'zip', convert_args_ct["output-dicom"])
            # break

    # Read first file under the output directory to check metadata is set
    # dicom_files = os.listdir(convert_args_ct["output-dicom"])
    # file_path = os.path.join(convert_args_ct["output-dicom"], dicom_files[0])
    # ds = pydicom.dcmread(file_path)
    # print(ds)

    print("Conversion complete.")
