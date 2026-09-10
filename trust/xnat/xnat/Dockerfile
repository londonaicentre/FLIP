# Portions derived from the XNAT docker-compose project
# Copyright (c) 2020, Washington University School of Medicine
# Licensed under the BSD 2-Clause License.
# SPDX-License-Identifier: BSD-2-Clause
#
# Modifications Copyright (c) 2026,
# Guy's and St Thomas' NHS Foundation Trust & King's College London

# XNAT 1.10.0 is the first release compiled against JDK 21 (it will not run on JDK 8).
# Tomcat stays on the 9.x line — 1.10 still targets Tomcat 9, matching upstream's
# reference stack (NrgXnat/xnat-docker-compose, branch 1.10.0-update).
FROM tomcat:9.0.108-jdk21-temurin-noble AS development

# Non-secret build args only. Datasource credentials, SMTP credentials, and
# the admin password are loaded at container start from the runtime
# environment so they are NOT baked into image layers (verifiable with
# `docker history --no-trunc`).
ARG XNAT_VERSION=${XNAT_VERSION}
ARG XNAT_ROOT=${XNAT_ROOT}
ARG XNAT_HOME=${XNAT_HOME}
ARG XNAT_PROCESSING_URL=http://xnat-web:8080

ARG TOMCAT_XNAT_FOLDER=${TOMCAT_XNAT_FOLDER}
ARG TOMCAT_XNAT_FOLDER_PATH=${CATALINA_HOME}/webapps/${TOMCAT_XNAT_FOLDER}

ADD make-xnat-config.sh /usr/local/bin/make-xnat-config.sh
ADD wait-for-postgres.sh /usr/local/bin/wait-for-postgres.sh

RUN apt-get update && apt-get install -y postgresql-client wget jq unzip
RUN rm -rf ${CATALINA_HOME}/webapps/*
RUN mkdir -p \
        ${TOMCAT_XNAT_FOLDER_PATH} \
        ${XNAT_HOME}/config \
        ${XNAT_HOME}/logs \
        ${XNAT_HOME}/plugins \
        ${XNAT_HOME}/work \
        ${XNAT_ROOT}/archive \
        ${XNAT_ROOT}/build \
        ${XNAT_ROOT}/cache \
        ${XNAT_ROOT}/ftp \
        ${XNAT_ROOT}/pipeline \
        ${XNAT_ROOT}/prearchive

COPY build-artifacts/xnat-web-${XNAT_VERSION}.war /tmp/xnat-web-${XNAT_VERSION}.war
RUN unzip -o -d ${TOMCAT_XNAT_FOLDER_PATH} /tmp/xnat-web-${XNAT_VERSION}.war
RUN rm -f /tmp/xnat-web-${XNAT_VERSION}.war

# FLIP branding for the enclave XNAT login/header. configure-xnat.sh points the site's siteLogoPath
# at this served path so the login page shows the FLIP logo instead of the stock XNAT one. Baked into
# the webapp (not a bind mount) so it is present in dev and prod alike.
COPY branding/flip-logo.png ${TOMCAT_XNAT_FOLDER_PATH}/images/flip-logo.png

# Create a non-root xnat user and group, and set ownership on all XNAT paths
RUN groupadd --system --gid 1001 xnat \
    && useradd --system --uid 1001 --gid xnat --home "${XNAT_HOME}" --shell /sbin/nologin xnat \
    && chown -R xnat:xnat \
        "${TOMCAT_XNAT_FOLDER_PATH}" \
        "${XNAT_HOME}" \
        "${XNAT_ROOT}"

# Switch to the non-root user before running the application
USER xnat

EXPOSE 8080

ENV XNAT_HOME=${XNAT_HOME}

CMD ["wait-for-postgres.sh", "/usr/local/tomcat/bin/catalina.sh", "run"]

FROM development AS production

# Copy plugins folder
ADD plugins ${XNAT_HOME}/plugins

# Copy config folder
ADD config ${XNAT_ROOT}/config

# Ensure the files added in the production stage are owned by xnat
USER root
RUN chown -R xnat:xnat "${XNAT_HOME}/plugins" "${XNAT_ROOT}/config"
USER xnat
