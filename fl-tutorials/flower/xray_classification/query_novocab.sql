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

-- FOR VOCABULARY-FREE DEV STACKS ONLY. Use query.sql anywhere omop.concept holds the core vocabulary.
--
-- The same cohort and columns as query.sql, with the concept names written in for the ids the
-- mock cxr_project data uses, so it runs where omop.concept holds only the DICOM vocabulary (the
-- local dev stack, where the core vocabulary is an optional, licensed load). Every id is matched by
-- an explicit IN list: a value, modality or anatomy outside it drops the row rather than reaching
-- the app as a NULL label, which the app would otherwise read as "uncertain" and train on.
WITH feature_observation AS (
    -- the subset of image_feature rows that link to the observation table
    SELECT
        ife.image_occurrence_id,
        ife.image_feature_concept_id,
        ife.anatomic_site_concept_id,
        ife.image_feature_event_id
    FROM
        omop.image_feature ife
    WHERE
        ife.image_feature_event_field_concept_id = 1147304 -- "observation" table
),
observation_value AS (
    -- Pivot image features and observation values to give one row per image occurrence.
    -- 4188539 = "Yes", 4188540 = "No"; any other value has already been filtered out below.
    SELECT
        fo.image_occurrence_id,
        -- anatomy is the same for every feature, so just pick one
        MAX(fo.anatomic_site_concept_id) AS anatomic_site_concept_id,
        MAX(
            CASE
                -- "effusion" (4215818)
                WHEN fo.image_feature_concept_id = 4215818 THEN CASE o.value_as_concept_id WHEN 4188539 THEN 'Yes' ELSE 'No' END
            END
        ) AS effusion,
        MAX(
            CASE
                -- "edema" (4196943)
                WHEN fo.image_feature_concept_id = 4196943 THEN CASE o.value_as_concept_id WHEN 4188539 THEN 'Yes' ELSE 'No' END
            END
        ) AS edema,
        MAX(
            CASE
                -- "lungs in normal arrangement" (40481136)
                WHEN fo.image_feature_concept_id = 40481136 THEN CASE o.value_as_concept_id WHEN 4188539 THEN 'Yes' ELSE 'No' END
            END
        ) AS normal_lungs
    FROM
        feature_observation fo
        JOIN omop.observation o ON o.observation_id = fo.image_feature_event_id
    WHERE
        o.value_as_concept_id IN (4188539, 4188540)
    GROUP BY
        fo.image_occurrence_id
)
-- Row grain is one row per imaging STUDY (image_occurrence); person_id is projected for the
-- trust's per-subject age/sex distributions. See query.sql for the full rationale.
SELECT
    -- subject (drives the per-subject age/sex distributions; the app ignores the column)
    io.person_id,
    -- image occurrence
    io.accession_id,
    io.image_occurrence_date AS "Image date",
    'Plain radiography' AS "Modality", -- 4056681
    'Lung structure' AS "Image occurrence anatomy", -- 4213162
    -- chest x-ray features
    CASE ov.anatomic_site_concept_id
        WHEN 4250192 THEN 'Both lungs'
        WHEN 4141610 THEN 'Right lung structure'
        WHEN 4195613 THEN 'Left lung structure'
    END AS "Image feature anatomy",
    ov.effusion AS "Effusion",
    ov.edema AS "Edema",
    ov.normal_lungs AS "Lungs in normal arrangement"
FROM
    omop.image_occurrence io
    JOIN observation_value ov ON ov.image_occurrence_id = io.image_occurrence_id
WHERE
    io.modality_concept_id = 4056681
    AND io.anatomic_site_concept_id = 4213162
    AND ov.anatomic_site_concept_id IN (4250192, 4141610, 4195613)
-- Load-bearing, as in query.sql: a stable order keeps the LIMIT picking the same 300 studies at
-- approval, imaging pull and training.
ORDER BY
    io.image_occurrence_id
LIMIT 300
