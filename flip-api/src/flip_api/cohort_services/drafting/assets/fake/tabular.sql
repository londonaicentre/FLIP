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

-- Canned draft of the fake drafter (COHORT_DRAFT_PROVIDER=fake), returned for any tabular description.
SELECT
    p.person_id,
    p.gender_concept_id,
    p.year_of_birth
FROM
    omop.person p
WHERE
    EXISTS (SELECT 1 FROM omop.condition_occurrence co WHERE co.person_id = p.person_id)
