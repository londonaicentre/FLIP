-- Copyright 2024 Guy’s and St Thomas’ NHS Foundation Trust
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

DO $$
BEGIN

    -- CONCEPT_ANCESTOR
    IF NOT EXISTS (SELECT 1 FROM omop.CONCEPT_ANCESTOR) THEN
        RAISE NOTICE 'Populating OMOP concept_ancestor data...';
        COPY omop.CONCEPT_ANCESTOR FROM '/flip/omop/vocab/CONCEPT_ANCESTOR.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- CONCEPT_CLASS
    IF NOT EXISTS (SELECT 1 FROM omop.CONCEPT_CLASS) THEN
        RAISE NOTICE 'Populating OMOP concept_class data...';
        COPY omop.CONCEPT_CLASS FROM '/flip/omop/vocab/CONCEPT_CLASS.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- CONCEPT_RELATIONSHIP
    IF NOT EXISTS (SELECT 1 FROM omop.CONCEPT_RELATIONSHIP) THEN
        RAISE NOTICE 'Populating OMOP concept_relationship data...';
        COPY omop.CONCEPT_RELATIONSHIP FROM '/flip/omop/vocab/CONCEPT_RELATIONSHIP.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- CONCEPT_SYNONYM
    IF NOT EXISTS (SELECT 1 FROM omop.CONCEPT_SYNONYM) THEN
        RAISE NOTICE 'Populating OMOP concept_synonym data...';
        COPY omop.CONCEPT_SYNONYM FROM '/flip/omop/vocab/CONCEPT_SYNONYM.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- CONCEPT
    IF NOT EXISTS (SELECT 1 FROM omop.CONCEPT) THEN
        RAISE NOTICE 'Populating OMOP concept data...';
        COPY omop.CONCEPT FROM '/flip/omop/vocab/CONCEPT.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- DOMAIN
    IF NOT EXISTS (SELECT 1 FROM omop.DOMAIN) THEN
        RAISE NOTICE 'Populating OMOP domain data...';
        COPY omop.DOMAIN FROM '/flip/omop/vocab/DOMAIN.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- DRUG_STRENGTH
    IF NOT EXISTS (SELECT 1 FROM omop.DRUG_STRENGTH) THEN
        RAISE NOTICE 'Populating OMOP drug_strength data...';
        COPY omop.DRUG_STRENGTH FROM '/flip/omop/vocab/DRUG_STRENGTH.csv'
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- RELATIONSHIP
    IF NOT EXISTS (SELECT 1 FROM omop.RELATIONSHIP) THEN
        RAISE NOTICE 'Populating OMOP relationship data...';
        COPY omop.RELATIONSHIP FROM '/flip/omop/vocab/RELATIONSHIP.csv'
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

    -- VOCABULARY
    IF NOT EXISTS (SELECT 1 FROM omop.VOCABULARY) THEN
        RAISE NOTICE 'Populating OMOP vocabulary data...';
        COPY omop.VOCABULARY FROM '/flip/omop/vocab/VOCABULARY.csv' 
        WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b');
    END IF;

END $$