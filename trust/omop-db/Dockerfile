# Builds the base omop postgres image
FROM postgres:17

# Declare build-time args (can be overridden in docker-compose.yml)
ARG POSTGRES_USER
ARG POSTGRES_PASSWORD
ARG POSTGRES_DB

# Make them available as runtime env vars during build
ENV POSTGRES_USER=${POSTGRES_USER} \
    POSTGRES_PASSWORD=${POSTGRES_PASSWORD} \
    POSTGRES_DB=${POSTGRES_DB}

# Copy initialization scripts (Schema DDL, Keys, Indices, Users)
COPY ./files/init.sql /docker-entrypoint-initdb.d/00_init.sql
COPY ./files/OMOPCDM_postgresql_5.4_ddl.sql /docker-entrypoint-initdb.d/10_ddl.sql
COPY ./files/OMOPCDM_postgresql_5.4_primary_keys.sql /docker-entrypoint-initdb.d/20_primary_keys.sql
COPY ./files/OMOPCDM_postgresql_5.4_indices.sql /docker-entrypoint-initdb.d/30_indices.sql
# COPY ./files/OMOPCDM_postgresql_5.4_constraints.sql /docker-entrypoint-initdb.d/40_constraints.sql
# The SQL is parameterized; a shell wrapper reads DATA_ACCESS_POSTGRES_PASSWORD
# from the runtime environment and invokes psql with -v.
COPY ./files/create_readonly_users.sql /flip/omop/create_readonly_users.sql
COPY ./files/create_readonly_users.sh /docker-entrypoint-initdb.d/50_create_readonly_users.sh
RUN chmod +x /docker-entrypoint-initdb.d/50_create_readonly_users.sh
COPY ./files/populate_vocabulary_tables.sql /docker-entrypoint-initdb.d/60_populate_vocabulary_tables.sql

# Copy vocabulary data
# This was downloaded from s3://aicentre/vocab/vocab_aicentre_core_20240916.zip
COPY ./data/vocab_aicentre_core_20240916 /flip/omop/vocab

# Copy constraints
# NOTE This needs to be applied after running the image and importing data -- if we import data after applying constraints, it will fail.
# See Makefile 'apply-constraints' command
COPY ./files/OMOPCDM_postgresql_5.4_constraints.sql /flip/omop/constraints.sql

EXPOSE 5432
