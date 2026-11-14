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

-- Drafting example (FLIP#1556): "Men with a PSA of 10 ng/mL or more."
-- A lab threshold with units: the threshold only means something in the unit it was written in, so the
-- query filters on that unit too. A result recorded in another unit is left out, not converted; the
-- draft says so in its assumptions.
SELECT DISTINCT
    p.person_id
FROM
    omop.person p
    JOIN omop.measurement m ON m.person_id = p.person_id
WHERE
    p.gender_concept_id = 8507 -- MALE
    AND m.measurement_concept_id = 3013603 -- prostate specific antigen
    AND m.value_as_number >= 10
    AND (m.unit_concept_id = 8842 OR m.unit_source_value = 'ng/mL') -- ng/mL
