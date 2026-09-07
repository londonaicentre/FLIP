-- Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--     http://www.apache.org/licenses/LICENSE-2.0
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
--
-- Cohort query for the IDC digital-pathology nuclei-detection tutorial.
--
-- Under LOCAL_DEV `flip.get_dataframe` validates this query and then ignores it, reading the
-- per-site `dataframe.csv` instead, so a simulator run never exercises it. On a trust it is the
-- real thing: it selects the slides whose imaging is pulled into XNAT and scored.
--
-- This comment previously said a real-trust run was impossible, listing four blockers. None of them
-- hold, and they are recorded here only so nobody reinstates the claim:
--
--   * `ResourceType` has a DICOM member, which is what whole-slide imaging is fetched as.
--   * The DICOM-to-NIfTI conversion is an asynchronous event subscription, not part of the pull.
--   * XNAT archives `xnat:smSessionData` and serves it over DICOMweb.
--   * OMOP already carries the slide-microscopy modality concept this query joins on.
--
-- What a trust does need is the data: `make -C fl-tutorials seed-idc-pathology` puts the slides in
-- Orthanc and these rows in OMOP. Note the cohort must also clear that trust's
-- COHORT_QUERY_THRESHOLD (default 10) before row-level data is released at all, which is why the
-- tutorial ships twelve slides per site rather than five.
SELECT
    io.accession_id AS accession_id,
    p.person_source_value AS patient_id
FROM omop.image_occurrence io
JOIN omop.person p ON p.person_id = io.person_id
JOIN omop.concept modality ON modality.concept_id = io.modality_concept_id
WHERE modality.concept_code = 'SM'
ORDER BY io.accession_id
LIMIT 50
