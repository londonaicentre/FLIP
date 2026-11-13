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

-- Drafting example (FLIP#1556): "The first CT scan for each patient."
-- First occurrence with a deterministic tie-break: exactly one row per person. ROW_NUMBER ranks each
-- person's CT rows by date and breaks a same-day tie on image_occurrence_id, so every re-run of the
-- query (approval, imaging pull, training) picks the same study.
WITH ranked_ct AS (
    SELECT
        io.person_id,
        io.accession_id,
        io.image_occurrence_date,
        ROW_NUMBER() OVER (
            PARTITION BY io.person_id
            ORDER BY io.image_occurrence_date, io.image_occurrence_id
        ) AS ct_rank
    FROM
        omop.image_occurrence io
    WHERE
        io.modality_concept_id = 4300757 -- CT
)
SELECT
    rc.person_id,
    rc.accession_id,
    rc.image_occurrence_date AS first_ct_date
FROM
    ranked_ct rc
WHERE
    rc.ct_rank = 1
