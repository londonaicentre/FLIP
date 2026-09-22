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

SELECT *
FROM omop.image_occurrence
WHERE modality_concept_id = 4300757
-- Load-bearing, not cosmetic: without it the LIMIT below picks an unspecified 50 studies in an
-- unspecified order, so the same cohort can disagree with itself between approval, the imaging
-- pull and training -- the app can then be handed a study whose DICOM was never fetched. Ordered
-- on the primary key rather than accession_id, which carries no unique constraint and so is not a
-- total order.
ORDER BY
    image_occurrence_id
LIMIT 50
