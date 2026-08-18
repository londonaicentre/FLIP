-- Portions derived from the OHDSI CommonDataModel project
-- (https://github.com/OHDSI/CommonDataModel, inst/ddl/5.4/postgresql),
-- extended with the MI-CDM medical-imaging tables.
-- Copyright (c) Observational Health Data Sciences and Informatics (OHDSI)
-- Licensed under the Apache License, Version 2.0.
-- SPDX-License-Identifier: Apache-2.0
--
-- Modifications Copyright (c) 2026
-- Guy's and St Thomas' NHS Foundation Trust & King's College London
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--     http://www.apache.org/licenses/LICENSE-2.0
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

set schema 'omop';

--postgresql CDM Primary Key Constraints for OMOP Common Data Model 5.4

DO $$
BEGIN
    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_episode')
	THEN
		ALTER TABLE EPISODE ADD CONSTRAINT xpk_EPISODE PRIMARY KEY (episode_id);
	END IF;

    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_concept')
	THEN
		ALTER TABLE CONCEPT ADD CONSTRAINT xpk_CONCEPT PRIMARY KEY (concept_id);
	END IF;

    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_vocabulary')
	THEN
		ALTER TABLE VOCABULARY ADD CONSTRAINT xpk_VOCABULARY PRIMARY KEY (vocabulary_id);
	END IF;

    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_domain')
	THEN
		ALTER TABLE DOMAIN ADD CONSTRAINT xpk_DOMAIN PRIMARY KEY (domain_id);
	END IF;

    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_concept_class')
	THEN
		ALTER TABLE CONCEPT_CLASS ADD CONSTRAINT xpk_CONCEPT_CLASS PRIMARY KEY (concept_class_id);
	END IF;
    
    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_relationship')
	THEN
		ALTER TABLE RELATIONSHIP ADD CONSTRAINT xpk_RELATIONSHIP PRIMARY KEY (relationship_id);
	END IF;

    IF NOT EXISTS ( SELECT CONSTRAINT_NAME
					FROM   INFORMATION_SCHEMA.KEY_COLUMN_USAGE
					WHERE  CONSTRAINT_SCHEMA = 'omop'
					AND    CONSTRAINT_NAME = 'xpk_cohort')
	THEN
		ALTER TABLE COHORT ADD CONSTRAINT xpk_COHORT PRIMARY KEY (cohort_definition_id, subject_id, cohort_start_date, cohort_end_date);
	END IF;

END$$;
