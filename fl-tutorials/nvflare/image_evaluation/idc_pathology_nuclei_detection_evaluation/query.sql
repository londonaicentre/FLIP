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
-- Cohort query placeholder for the IDC digital-pathology nuclei-detection tutorial.
--
-- This tutorial runs under LOCAL_DEV against slides downloaded from the NCI Imaging Data Commons,
-- where `flip.get_dataframe` validates this query and then ignores it, reading the per-site
-- `dataframe.csv` instead. It is kept so the tutorial has the same shape as the others and so the
-- exported job carries a syntactically valid query.
--
-- Running this on a real trust would additionally require a pathology route through the platform
-- that does not exist yet: `ResourceType` has no pathology member, imaging-api converts DICOM to
-- NIfTI (meaningless for a tiled RGB pyramid), XNAT's model is series-oriented, and OMOP MI-CDM
-- would need an `SM` modality concept. See the README's "Running this on a real trust" section.
SELECT
    io.accession_id AS accession_id,
    p.person_source_value AS patient_id
FROM omop.image_occurrence io
JOIN omop.person p ON p.person_id = io.person_id
JOIN omop.concept modality ON modality.concept_id = io.modality_concept_id
WHERE modality.concept_code = 'SM'
ORDER BY io.accession_id
LIMIT 50
