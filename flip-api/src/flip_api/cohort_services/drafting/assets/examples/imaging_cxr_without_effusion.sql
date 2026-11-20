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

-- Drafting example (FLIP#1556): "Chest X-rays, excluding those showing a pleural effusion."
-- An image_feature exclusion. A feature links to its observation through image_feature_event_id AND
-- image_feature_event_field_concept_id = 1147304; the event id alone is not a safe join. A study is left out
-- when any of its image occurrences records effusion = Yes; a study with no effusion feature is kept.
-- One row per (person_id, accession_id), hence DISTINCT.
SELECT DISTINCT
    io.person_id,
    io.accession_id
FROM
    omop.image_occurrence io
WHERE
    io.modality_concept_id = 4056681 -- plain radiography
    AND io.anatomic_site_concept_id = 4213162 -- lung structure
    AND NOT EXISTS (
        SELECT 1
        FROM
            omop.image_occurrence io_same_study
            JOIN omop.image_feature ife ON ife.image_occurrence_id = io_same_study.image_occurrence_id
            JOIN omop.observation o ON o.observation_id = ife.image_feature_event_id
        WHERE
            io_same_study.accession_id = io.accession_id
            AND ife.image_feature_event_field_concept_id = 1147304 -- feature links to observation
            AND ife.image_feature_concept_id = 4215818 -- pleural effusion
            AND o.value_as_concept_id = 4188539 -- Yes
    )
