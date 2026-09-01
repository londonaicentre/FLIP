# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
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

"""Mappings from source values to OMOP concept IDs, shared by the dataset converters.

Imported from ``londonaicentre/flip-omop-mock-data`` (FLIP#1092) so the provenance of FLIP's
published mock OMOP data lives with the platform. Values are unchanged from that repo.
"""

MAPPING_SEX = {"M": 442985, "F": 442986, "O": 0}

MAPPING_MODALITY = {
    "CR": 4056681,
    "US": 4037672,
    "DX": 4056681,
    "CT": 4300757,
    "MR": 4013636,
    "MG": 4324693,
    "IO": 1072972,
    "XA": 4299523,
    "PX": 4233227,
    "NM": 4155794,
    "RF": 4195288,
    "PT": 4305790,
}

MAPPING_PROCEDURE_TYPE = {
    "ct spleen": 3006580,
    "spleen ct": 3006580,
}

MAPPING_ANATOMIC_SITE = {
    "spleen": 4302605,
    "abdomen": 37303869,
    "both lungs": 4250192,
    "right lung": 4141610,
    "left lung": 4195613,
    "heart": 4217142,
}

MAPPING_DICOM = {
    "00080070": 2128000056,  # Manufacturer
    "00081090": 2128000177,  # Manufacturer's Model Name
    "00180050": 2128000817,  # Slice Thickness
    "00280010": 2128002092,  # Rows
    "00280011": 2128002093,  # Columns
}

MAPPING_FINDING = {
    "pulmonary edema": 4196943,
    "congestive heart failure": 4196943,
    "normal lungs": 40481136,
    "pleural effusion": 4215818,
}

MAPPING_YES_NO = {
    "yes": 4188539,
    "no": 4188540,
}

MAPPING_CXR = {
    "ehr_radiology_report": 32841,  # EHR Radiology Note https://athena.ohdsi.org/search-terms/terms/32841
    "observation": 1147304,  # OMOP observation table
    "lung_structure": 4213162,
    "plain_xray": 4163872,
}

# Shared OMOP concept ids used by more than one dataset converter (FLIP#1092 task 9). Each is an OMOP
# convention rather than a per-dataset judgement call, so it lives here instead of being repeated —
# and re-diverging — across converters. Each call site keeps its own citation comment; see
# omop_convert_spleen.py for the source links these values were copied from.
UNKNOWN_CONCEPT_ID = 0
INPATIENT_VISIT_CONCEPT_ID = 9201
EHR_TYPE_CONCEPT_ID = 32817
IMAGE_FEATURE_EVENT_FIELD_CONCEPT_ID = 1147330
DICOM_ATTRIBUTE_CONCEPT_CLASS_ID = 2128000001
