#!/usr/bin/env bash
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Configure a freshly deployed XNAT, as an SPCS job service (FLIP#1583).
#
# The Snowflake equivalent of the Compose `make -C trust/xnat xnat-configure` and of the
# Helm chart's xnat-init hook: site preferences, the service account, the DICOM SCP
# receiver, the PACS registration and the DQR schedule. Until it has run, XNAT has no
# registered receiver and rejects every C-STORE, which is the single most common reason a
# newly deployed trust answers HTTP but retrieves nothing.
#
# It runs configure-xnat.sh UNMODIFIED, from the xnat-web image that ships it, which the
# spike (FLIP#1577) verified works as a job service once xnat-web is READY. The script is
# convergent: on an already-configured site it skips what is in place and re-applies the
# rest, so re-running after `make up` is safe and is how a new image's configuration is
# picked up.
#
# What it deliberately does NOT run is configure-dcm2niix.sh. That registers the XNAT
# Container Service command and its event subscriptions, and there is no Container Service
# on SPCS: no Docker socket, no swarm, no Kubernetes backend. DICOM to NIfTI conversion is
# the dcm2niix job service instead, launched by imaging-api through APP.CONVERT_SCAN
# (FLIP#1578, FLIP#1581), so the xnat-web spec sets XNAT_CONTAINER_SERVICE_ENABLED=false.
#
# Usage (normally through `make -C trust/deploy/snowflake xnat-configure KIT=<CODE>`).

set -euo pipefail

# XNAT's factory password, used on a site that has never been initialised. An operator who
# has already rotated the admin password passes the current one in the kit.
export XNAT_ADMIN_INITIAL_PASSWORD="${XNAT_ADMIN_INITIAL_PASSWORD:-admin}"

# One admin-password probe round per run: a job service is retried by the operator, and
# in-script retries on top would multiply the failed logins a wrong password costs toward
# XNAT's ~20-login lockout (FLIP#1384).
export XNAT_AUTH_PROBE_ATTEMPTS="${XNAT_AUTH_PROBE_ATTEMPTS:-1}"

# A literal, exactly as in the Helm hook: XNAT_ROOT is a build ARG the image never exports,
# and /data/xnat is pinned by the xnat-web spec's volume mounts, its XNAT_HOME and its
# -Dxnat.home. An image rebuilt elsewhere breaks all of those with or without this line.
cd /data/xnat/config
bash configure-xnat.sh
echo "✅ XNAT configured: receiver, PACS registration and DQR schedule are in place."
