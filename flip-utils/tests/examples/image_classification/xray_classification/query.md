<!--
Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

To use on this application:

```sql
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
    -- Pivot image features and observation values to give one row per image occurrence
    SELECT
        fo.image_occurrence_id,
        -- anatomy is the same for every feature, so just pick one
        MAX(anatomic_site_concept_id) AS anatomic_site_concept_id,
        MAX(
            CASE
                -- when the feature is "effusion" (4215818) get the "yes/no" value from observation table
                WHEN image_feature_concept_id = 4215818 THEN value_concept.concept_name
            END
        ) AS effusion,
        MAX(
            CASE
                WHEN image_feature_concept_id = 4196943 THEN value_concept.concept_name
            END
        ) AS edema,
        MAX(
            CASE
                WHEN image_feature_concept_id = 40481136 THEN value_concept.concept_name
            END
        ) AS normal_lungs
    FROM
        feature_observation fo
        JOIN omop.observation o ON o.observation_id = fo.image_feature_event_id
        JOIN omop.concept value_concept ON value_concept.concept_id = o.value_as_concept_id
    GROUP BY
        fo.image_occurrence_id
)
SELECT
    -- image occurrence
    io.accession_id,
    io.image_occurrence_date AS "Image date",
    modality_concept.concept_name AS "Modality",
    io_anatomic_site_concept.concept_name AS "Image occurrence anatomy",
    -- chest x-ray features
    ife_anatomic_site_concept.concept_name AS "Image feature anatomy",
    ov.effusion AS "Effusion",
    ov.edema AS "Edema",
    ov.normal_lungs AS "Lungs in normal arrangement"
FROM
    -- data tables
    omop.image_occurrence io
    JOIN observation_value ov ON ov.image_occurrence_id = io.image_occurrence_id -- concept tables
    JOIN omop.concept modality_concept ON modality_concept.concept_id = io.modality_concept_id
    JOIN omop.concept io_anatomic_site_concept ON io.anatomic_site_concept_id = io_anatomic_site_concept.concept_id
    JOIN omop.concept ife_anatomic_site_concept ON ov.anatomic_site_concept_id = ife_anatomic_site_concept.concept_id
LIMIT 100
```
