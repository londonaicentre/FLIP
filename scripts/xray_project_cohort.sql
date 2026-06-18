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

-- Project cohort query for the synthetic chest-X-ray dataset loaded by
-- scripts/load_xray_omop_dataset.py (flip-fl-base "balanced_synthetic_split").
--
-- Returns one row per loaded image occurrence with all seven pathology labels as
-- Yes/No, in the same column shape as the FL app's sample_get_dataframe_response.csv
-- (the xray classification tutorial only consumes Effusion / Edema / Lungs in
-- normal arrangement; the other four are returned for completeness).
--
-- Scope: the query is restricted to THIS dataset via
-- ``procedure_occurrence.procedure_source_value = 'Chest X-ray'`` — the value the
-- loader stamps on every row. Pre-existing FLIP mock data uses 'Chest CT' /
-- 'Spleen CT', so it is excluded. NOTE: this discriminator is unique to the data
-- currently in the database; if a second chest-X-ray dataset is later loaded with
-- the same loader it would also carry 'Chest X-ray'. For multi-project isolation,
-- stamp a per-project tag at load time and filter on that instead.
--
-- Label concept ids: Effusion 4215818, Edema 4196943, Consolidation 4319884,
-- Infiltration 4264333, Lung Nodule or Mass 4214641 (SNOMED "Mass"),
-- Pneumothorax 253796, Lungs in normal arrangement 40481136. The Yes/No value is
-- read from observation.value_as_concept_id via the image_feature -> observation
-- link (image_feature_event_field_concept_id = 1147304, the "observation" table).
--
-- Verified against the loaded data: returns exactly the project's image
-- occurrences (2388 for Trust_1 site1+holdoff, 2340 for Trust_2 site2+holdoff),
-- every label matches the source CSV, and the statement passes the data-access-api
-- validate_query() checks (single SELECT, omop-schema-only, literal LIMIT/OFFSET).

WITH project_images AS (
    SELECT io.image_occurrence_id, io.accession_id, io.image_occurrence_date, io.modality_concept_id
    FROM omop.image_occurrence io
    JOIN omop.procedure_occurrence pr ON pr.procedure_occurrence_id = io.procedure_occurrence_id
    WHERE pr.procedure_source_value = 'Chest X-ray'
),
feature_observation AS (
    -- image_feature rows that link to the observation table (carry the Yes/No labels)
    SELECT ife.image_occurrence_id, ife.image_feature_concept_id, ife.image_feature_event_id
    FROM omop.image_feature ife
    WHERE ife.image_feature_event_field_concept_id = 1147304
      AND ife.image_occurrence_id IN (SELECT image_occurrence_id FROM project_images)
),
observation_value AS (
    -- pivot the per-label observations to one row per image occurrence
    SELECT fo.image_occurrence_id,
        MAX(CASE WHEN image_feature_concept_id = 4215818  THEN vc.concept_name END) AS effusion,
        MAX(CASE WHEN image_feature_concept_id = 4196943  THEN vc.concept_name END) AS edema,
        MAX(CASE WHEN image_feature_concept_id = 4319884  THEN vc.concept_name END) AS consolidation,
        MAX(CASE WHEN image_feature_concept_id = 4264333  THEN vc.concept_name END) AS infiltration,
        MAX(CASE WHEN image_feature_concept_id = 4214641  THEN vc.concept_name END) AS lung_nodule_or_mass,
        MAX(CASE WHEN image_feature_concept_id = 253796   THEN vc.concept_name END) AS pneumothorax,
        MAX(CASE WHEN image_feature_concept_id = 40481136 THEN vc.concept_name END) AS normal_lungs
    FROM feature_observation fo
    JOIN omop.observation o ON o.observation_id = fo.image_feature_event_id
    JOIN omop.concept vc ON vc.concept_id = o.value_as_concept_id
    GROUP BY fo.image_occurrence_id
)
SELECT
    pi.accession_id,
    pi.image_occurrence_date AS "Image date",
    modality.concept_name    AS "Modality",
    ov.effusion              AS "Effusion",
    ov.edema                 AS "Edema",
    ov.consolidation         AS "Consolidation",
    ov.infiltration          AS "Infiltration",
    ov.lung_nodule_or_mass   AS "Lung Nodule or Mass",
    ov.pneumothorax          AS "Pneumothorax",
    ov.normal_lungs          AS "Lungs in normal arrangement"
FROM project_images pi
JOIN observation_value ov ON ov.image_occurrence_id = pi.image_occurrence_id
JOIN omop.concept modality ON modality.concept_id = pi.modality_concept_id
ORDER BY pi.accession_id
