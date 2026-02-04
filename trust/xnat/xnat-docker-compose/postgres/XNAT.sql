-- Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--     http://www.apache.org/licenses/LICENSE-2.0
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

\echo # Loading roles

\set xnat_user `echo $XNAT_DATASOURCE_USERNAME`
\set xnat_user_pw `echo $XNAT_DATASOURCE_PASSWORD`
\set xnat_db `echo $POSTGRES_DB`

drop role if exists :xnat_user;
create role :"xnat_user" with login password :'xnat_user_pw';

-- add missing grants for database
ALTER DATABASE :"xnat_user" OWNER TO :"xnat_db";

