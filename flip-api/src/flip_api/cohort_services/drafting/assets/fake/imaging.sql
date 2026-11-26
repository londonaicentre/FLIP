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

-- Canned draft of the fake drafter (COHORT_DRAFT_PROVIDER=fake), returned for any imaging description.
SELECT
    io.person_id,
    io.accession_id,
    io.image_occurrence_date,
    io.modality_concept_id,
    p.gender_concept_id,
    EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth AS age_at_image
FROM
    omop.image_occurrence io
    JOIN omop.person p ON p.person_id = io.person_id
WHERE
    io.modality_concept_id IN (4056681, 4300757, 4013636)
