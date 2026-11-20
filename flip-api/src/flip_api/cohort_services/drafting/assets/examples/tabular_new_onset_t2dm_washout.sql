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

-- Drafting example (FLIP#1556): "Patients with new-onset type 2 diabetes: at least a year of observation
-- before their first diagnosis."
-- An observation_period washout: the first recorded diagnosis counts as new onset only when the person was
-- observed for the whole year before it, so an earlier diagnosis could have been recorded. Conditions are
-- matched on condition_source_value (SNOMED, as Synthea emits them), so no vocabulary is needed.
WITH first_dx AS (
    SELECT
        co.person_id,
        MIN(co.condition_start_date) AS dx_date
    FROM
        omop.condition_occurrence co
    WHERE
        co.condition_source_value = '44054006' -- SNOMED: type 2 diabetes mellitus
    GROUP BY
        co.person_id
)
SELECT
    fd.person_id,
    fd.dx_date
FROM
    first_dx fd
WHERE
    EXISTS (
        SELECT 1
        FROM
            omop.observation_period op
        WHERE
            op.person_id = fd.person_id
            AND op.observation_period_start_date <= fd.dx_date - INTERVAL '365 days'
            AND op.observation_period_end_date >= fd.dx_date
    )
