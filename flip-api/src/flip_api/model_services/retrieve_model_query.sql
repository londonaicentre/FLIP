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

SELECT
    -- Basic model info from the model table
    model.id AS model_id,
    model.name AS model_name,
    model.description AS model_description,
    model.project_id,
    model.status,

    -- Latest query and its results as JSON
    (
        SELECT
            json_build_object(
                'id', queries.id,
                'name', queries.name,
                'query', queries.query,
                'results', ARRAY(
                    SELECT
                        json_build_object(
                            'data', data::json,         -- Result data (cast to JSON)
                            'trust_name', trust.name
                        )
                    FROM query_result
                    LEFT JOIN trust ON query_result.trust_id = trust.id
                    WHERE query_result.query_id = queries.id
                        AND queries.project_id = model.project_id  -- Ensure the query is from the same project
                    GROUP BY trust.name, query_result.data
                )
            )
        FROM queries
        WHERE queries.project_id = model.project_id       -- Only queries from the same project
            AND model.deleted = false                   -- Ensure the model is not deleted
        GROUP BY queries.id
        ORDER BY queries.created DESC                   -- Get the latest query first
        LIMIT 1
    ) AS query,

    -- All uploaded files related to the model as JSON array
    (
        SELECT json_agg(json_build_object(
            'id', id,
            'name', name,
            'status', status,
            'size', size,
            'type', type,
            'tag', tag
        ))
        FROM uploaded_files
        WHERE model_id = model.id
    ) AS files

-- Main source: model table
FROM model
LEFT JOIN projects ON model.project_id = projects.id

-- Main filters:
-- - Return only the model requested by ID
-- - Ensure the project and model have not been deleted
WHERE model.id = :model_id
    AND projects.deleted = false
    AND model.deleted = false

LIMIT 1