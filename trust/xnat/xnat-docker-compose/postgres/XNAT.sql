-- Portions derived from the XNAT docker-compose project
-- Copyright (c) 2020, Washington University School of Medicine
-- Licensed under the BSD 2-Clause License.
-- SPDX-License-Identifier: BSD-2-Clause
--
-- Modifications Copyright (c) 2026,
-- Guy's and St Thomas' NHS Foundation Trust & King's College London

\echo # Loading roles

\set xnat_user `echo $XNAT_DATASOURCE_USERNAME`
\set xnat_user_pw `echo $XNAT_DATASOURCE_PASSWORD`
\set xnat_db `echo $POSTGRES_DB`

drop role if exists :xnat_user;
create role :"xnat_user" with login password :'xnat_user_pw';

-- add missing grants for database
ALTER DATABASE :"xnat_user" OWNER TO :"xnat_db";
