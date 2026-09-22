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

-- The brain_mri_project cohort: every MR study of the brain. A multi-series study has one
-- image_occurrence row per series (four here: FLAIR, T1w, T1Gd, T2w), so the query is DISTINCT
-- on the study's accession — the platform pulls a study, and all its series, by accession_id.
-- 4013636 = Magnetic resonance imaging; 4133034 = Brain structure (SNOMED 12738006).
SELECT DISTINCT io.accession_id
FROM omop.image_occurrence io
WHERE io.modality_concept_id = 4013636
  AND io.anatomic_site_concept_id = 4133034
LIMIT 100
