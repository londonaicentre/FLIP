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

-- TEST TRUSTS ONLY: query.sql for a trust deployed without the OMOP vocabulary (the Helm chart's
-- NO_VOCAB_LOAD=1, FLIP#1390). Such a trust holds the x-ray data but only a sliver of omop.concept,
-- so query.sql's concept joins drop every row and staging refuses the project. This copy names the
-- two answer concepts inline instead -- 4188539 "Yes" and 4188540 "No", the only values the cxr
-- converter writes (fl-tutorials/datasets/utils/omop_mappings.py) -- and leaves out the modality and
-- anatomy columns, which the app never reads. A real trust has the vocabulary: use query.sql there.
-- fl-tutorials/tests/test_xray_no_vocab_query.py runs both against one synthetic OMOP database and
-- fails if they stop handing the app the same studies with the same findings.
WITH feature_observation AS (
    -- the subset of image_feature rows that link to the observation table
    SELECT
        ife.image_occurrence_id,
        ife.image_feature_concept_id,
        ife.image_feature_event_id
    FROM
        omop.image_feature ife
    WHERE
        ife.image_feature_event_field_concept_id = 1147304 -- "observation" table
),
observation_value AS (
    -- one row per image occurrence, holding each finding's answer concept
    SELECT
        fo.image_occurrence_id,
        MAX(CASE WHEN fo.image_feature_concept_id = 4215818 THEN o.value_as_concept_id END) AS effusion,
        MAX(CASE WHEN fo.image_feature_concept_id = 4196943 THEN o.value_as_concept_id END) AS edema,
        MAX(CASE WHEN fo.image_feature_concept_id = 40481136 THEN o.value_as_concept_id END) AS normal_lungs
    FROM
        feature_observation fo
        JOIN omop.observation o ON o.observation_id = fo.image_feature_event_id
    GROUP BY
        fo.image_occurrence_id
)
-- Same row grain, order and cut as query.sql: one row per imaging study, ordered on the primary key.
SELECT
    io.person_id,
    io.accession_id,
    io.image_occurrence_date AS "Image date",
    CASE ov.effusion WHEN 4188539 THEN 'Yes' WHEN 4188540 THEN 'No' END AS "Effusion",
    CASE ov.edema WHEN 4188539 THEN 'Yes' WHEN 4188540 THEN 'No' END AS "Edema",
    CASE ov.normal_lungs WHEN 4188539 THEN 'Yes' WHEN 4188540 THEN 'No' END AS "Lungs in normal arrangement"
FROM
    omop.image_occurrence io
    JOIN observation_value ov ON ov.image_occurrence_id = io.image_occurrence_id
ORDER BY
    io.image_occurrence_id
LIMIT 300
