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

-- Every prostate bpMRI series (t2w, adc, hbv — one image_occurrence row each, sharing the study's
-- accession) with the per-study clinical facts prostate_project carries as observations, so a
-- cohort can be narrowed on them (e.g. add `AND c.cspca = 'Yes'` or `AND c.isup_grade_group >= 2`).
-- The segmentation labels themselves are not in OMOP: they arrive through XNAT data enrichment
-- (fl-tutorials/datasets/prostate/upload_prostate_labels_to_xnat.py).
WITH clinical AS (
    SELECT
        o.visit_occurrence_id,
        MAX(CASE WHEN o.observation_concept_id = 602257 THEN o.value_as_number END) AS isup_grade_group,
        MAX(CASE WHEN o.observation_concept_id = 4163261 THEN yes_no.concept_name END) AS cspca,
        MAX(CASE WHEN o.observation_concept_id = 2128008964 THEN o.value_as_number END) AS max_pirads
    FROM omop.observation o
    LEFT JOIN omop.concept yes_no ON yes_no.concept_id = o.value_as_concept_id
    GROUP BY o.visit_occurrence_id
)
SELECT
    io.accession_id,
    io.image_series_uid,
    io.image_occurrence_date AS "Image date",
    modality.concept_name AS "Modality",
    site.concept_name AS "Anatomy",
    c.isup_grade_group AS "ISUP grade group",
    c.cspca AS "Clinically significant cancer",
    c.max_pirads AS "PI-RADS"
FROM omop.image_occurrence io
JOIN omop.concept modality ON modality.concept_id = io.modality_concept_id
JOIN omop.concept site ON site.concept_id = io.anatomic_site_concept_id
LEFT JOIN clinical c ON c.visit_occurrence_id = io.visit_occurrence_id
WHERE io.anatomic_site_concept_id = 4165732 -- Prostatic structure
  AND io.modality_concept_id = 4013636      -- Magnetic resonance imaging
LIMIT 1000
