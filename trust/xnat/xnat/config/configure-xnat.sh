#!/bin/bash
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


# This script configures the XNAT instance
#
# Note the rest of the environment variables are available from the file
# - trust/xnat/.env
#

set -euo pipefail

# The below are fixed values for now
XNAT_URL="http://xnat-web:8080" # internal to Docker network
ORTHANC_HOST="orthanc" # name of the service (container) in docker-compose
ORTHANC_AETITLE="ORTHANC"

# Wait for XNAT to be available
echo "Waiting for XNAT to be available..."
until $(curl --output /dev/null --silent --head --fail $XNAT_URL/app/template/Login.vm); do
  printf '.'
  sleep 1
done
echo "XNAT is up!"

# Idempotency short-circuit: if XNAT is already initialized AND the initial
# admin password no longer authenticates, this instance was fully configured
# by a prior run. Re-running the rest of the script would silently 401 on the
# initial-password curls and then produce duplicate-key errors for the service
# account, PACS registration, and PACS availability intervals — so skip it.
init_pw_status=$(curl -s -o /dev/null -w '%{http_code}' \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
  "$XNAT_URL/xapi/siteConfig/initialized")
if [[ "${init_pw_status}" != "200" ]]; then
  initialized=$(curl -s -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    "$XNAT_URL/xapi/siteConfig/initialized")
  if [[ "${initialized}" == "true" ]]; then
    echo "XNAT already configured (initialized=true, initial password no longer works) — skipping."
    exit 0
  fi
fi

echo "Configuring XNAT instance..."
sleep 10 # Additional wait to ensure XNAT is fully up before proceeding

# Activate XNAT instance
echo "Activating XNAT instance..."
curl -s -X POST "$XNAT_URL/xapi/siteConfig" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{"initialized": true}'

# Change admin password
echo "Changing admin password..."
curl -s -X PUT "$XNAT_URL/xapi/users/admin" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"${XNAT_ADMIN_USER}\", \"password\": \"${XNAT_ADMIN_PASSWORD}\"}"

# Create service account
echo "Creating service account..."
curl -s -X POST "$XNAT_URL/xapi/users" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"${XNAT_SERVICE_USER}\",
    \"password\": \"${XNAT_SERVICE_PASSWORD}\",
    \"firstName\": \"flip Service Account\",
    \"lastName\": \"flip Service Account\",
    \"email\": \"xnat@example.com\"
  }"

# Add ContainerManager role to admin user
# NOTE This was added when the ContainerManager role was introduced in Container Service 3.7.0 (see
# release notes in https://bitbucket.org/xnatdev/container-service/src/master/CHANGELOG.md) and
# https://wiki.xnat.org/container-service/container-service-administration#ContainerServiceAdministration-EnablingaContainerManager
echo " "
echo "Assigning role 'ContainerManager' to admin account..."
curl -s -X PUT "$XNAT_URL/xapi/users/${XNAT_ADMIN_USER}/roles/ContainerManager" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "accept: application/json"

# Assign roles to service account (including 'ContainerManager' role)
echo " "
echo "Assigning roles to service account..."
curl -s -X PUT "$XNAT_URL/xapi/users/${XNAT_SERVICE_USER}/groups/" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '[
    "ALL_DATA_ADMIN"
  ]'

curl -s -X PUT "$XNAT_URL/xapi/users/${XNAT_SERVICE_USER}/roles/" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '[
    "ContainerManager",
    "DataManager",
    "SiteUser",
    "Administrator",
    "Dqr",
    "non_expiring"
  ]'

# Disable guest account
echo " "
echo "Disabling guest account..."
curl -s -X PUT "$XNAT_URL/xapi/users/guest/enabled/false" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}"

# Configure DQR plugin
echo "Configuring DQR plugin..."
curl -s -X POST "$XNAT_URL/xapi/dqr/settings" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{
    "pacsAvailabilityCheckFrequency": "1 minute",
    "dqrWaitToRetryRequestInSeconds": "300",
    "assumeSameSessionIfArrivedWithin": "30 minutes",
    "allowAllUsersToUseDqr": true,
    "dqrCallingAe": "XNAT",
    "notifyAdminOnImport": false,
    "allowAllProjectsToUseDqr": true,
    "leavePacsAuditTrail": false,
    "dqrMaxPacsRequestAttempts": "100"
  }'

# Configure site-wide anonymization script
echo "Configuring site-wide anonymization script..."
curl -s -X PUT "$XNAT_URL/xapi/anonymize/site" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: text/plain" \
  --data-binary @anon_script.das

# Enable site-wide anonymization
echo "Enabling site-wide anonymization..."
curl -s -X PUT "$XNAT_URL/xapi/anonymize/site/enabled" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d 'true'

# Get SCP receivers
response=$(curl -s -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" "$XNAT_URL/xapi/dicomscp")

# Debug: Print Raw Response
echo "Raw API Response: $response"

# Check if response is empty or invalid
if [[ -z "$response" || "$response" == "[]" ]]; then
    echo "No SCP receivers found."
else
    # Extract the SCP receiver ID with "aeTitle": "XNAT" using grep and sed
    scp_receiver_data=$(echo "$response" | grep -o '{[^}]*"aeTitle"[^}]*}' | grep '"aeTitle":"XNAT"')
    echo "SCP Receiver Data: $scp_receiver_data"

    if [[ -n "$scp_receiver_data" ]]; then
        # Extract the "id" field of the SCP receiver
        scp_receiver_id=$(echo "$scp_receiver_data" | sed -n 's/.*"id":\([0-9]\+\).*/\1/p')

        if [[ -n "$scp_receiver_id" ]]; then
            echo "Removing SCP Receiver with ID: $scp_receiver_id..."

            # Send DELETE request to remove the SCP receiver
            delete_response=$(curl -s -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" -X DELETE "$XNAT_URL/xapi/dicomscp/$scp_receiver_id")

            echo "Delete Response: $delete_response"
            echo "SCP Receiver removed successfully."
        else
            echo "Failed to extract SCP receiver ID."
        fi
    else
        echo "No SCP receiver with aeTitle='XNAT' found."
    fi
fi

# Configure SCP receiver to have dqrObjectIdentifier as the identifier (the default is not)
echo "Configuring SCP receiver..."
curl -s -X POST "$XNAT_URL/xapi/dicomscp" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"aeTitle\": \"XNAT\",
    \"port\": ${XNAT_PORT},
    \"enabled\": true,
    \"customProcessing\": true,
    \"directArchive\": true,
    \"identifier\": \"dqrObjectIdentifier\",
    \"anonymizationEnabled\": true,
    \"whitelistEnabled\": false,
    \"whitelistText\": \"\",
    \"routingExpressionsEnabled\": false,
    \"projectRoutingExpression\": \"\",
    \"subjectRoutingExpression\": \"\",
    \"sessionRoutingExpression\": \"\"
  }"

# Configure OHIF viewer
echo "Configuring OHIF viewer..."
curl -s -X POST "$XNAT_URL/xapi/siteConfig" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{"addOhifViewLinkToProjectListingDefaults": true }'

# Register the DX (digital radiography) image-session data type as a secured element.
#
# WHY: XNAT only registers a fixed set of image-session types in element security
# by default (CR, CT, MR, PET, PET-MR) — `xnat:dxSessionData` is NOT among them,
# even though the schema defines it. Chest X-rays arrive as DICOM modality "DX",
# so the DQR import archives them as `xnat:dxSessionData`. Without an element-
# security row, new projects never grant read access to that type, so the sessions
# are invisible to every project-scoped query (`/data/projects/{id}/experiments`)
# — which is exactly what imaging-api polls for import status and what the
# fl-client uses to retrieve images. The data imports fine but appears "stuck at
# 0 imported". (The global `/data/experiments?project=` query bypasses project
# permissions, which is why the data is in XNAT yet hidden per-project.)
#
# FIX: register `xnat:dxSessionData` in element security, cloning the flags of the
# already-present `xnat:crSessionData`. Once registered, every newly-created
# project auto-grants DX read access to its owner/member/collaborator groups.
#
# NOTE: XNAT loads element security into memory at startup, so this row only
# takes effect after an XNAT restart. There is no REST endpoint to register a
# data type; the UI "Set Up Additional Data Type" wizard is the only other path,
# and is a stateful multi-step form unsuitable for scripting — hence the direct,
# idempotent SQL below, run with the same DB credentials XNAT itself uses. When a
# row is newly inserted we print the sentinel `DX_ELEMENT_SECURITY_REGISTERED`;
# the Makefile `xnat-configure` target greps for it and forces a one-time XNAT
# service reload so the type is live before any project is created.
echo " "
echo "Registering xnat:dxSessionData element security..."
XNAT_CONF="/data/xnat/home/config/xnat-conf.properties"
DB_URL=$(grep -E '^datasource\.url=' "$XNAT_CONF" | cut -d= -f2-)
DB_HOST=$(echo "$DB_URL" | sed -E 's#jdbc:postgresql://([^/:]+).*#\1#')
DB_NAME=$(echo "$DB_URL" | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')
DB_USER=$(grep -E '^datasource\.username=' "$XNAT_CONF" | cut -d= -f2-)
DB_PASS=$(grep -E '^datasource\.password=' "$XNAT_CONF" | cut -d= -f2-)
dx_sql_out=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'DXSQL'
DO $$
DECLARE
  new_meta INT;
BEGIN
  IF EXISTS (SELECT 1 FROM xdat_element_security WHERE element_name = 'xnat:dxSessionData') THEN
    RAISE NOTICE 'xnat:dxSessionData already registered in element security; skipping.';
    RETURN;
  END IF;
  -- Audit/meta row referenced by element_security_info.
  INSERT INTO xdat_element_security_meta_data
    (xft_version, status, activation_date, row_last_modified, insert_date, modified, shareable)
  VALUES (1, 'active', now(), now(), now(), 0, 1)
  RETURNING meta_data_id INTO new_meta;
  -- Clone crSessionData's security flags; only identity columns differ.
  INSERT INTO xdat_element_security
    (element_name, secondary_password, secure_ip, secure, browse, sequence, quarantine,
     pre_load, searchable, secure_read, secure_edit, secure_create, secure_delete, accessible,
     usage, singular, plural, category, code, element_security_info,
     element_security_set_element_se_xdat_security_id)
  SELECT 'xnat:dxSessionData', secondary_password, secure_ip, secure, browse, sequence, quarantine,
     pre_load, searchable, secure_read, secure_edit, secure_create, secure_delete, accessible,
     usage, 'DX Session', 'DX Sessions', category, 'DX', new_meta,
     element_security_set_element_se_xdat_security_id
  FROM xdat_element_security
  WHERE element_name = 'xnat:crSessionData';
  RAISE NOTICE 'Registered xnat:dxSessionData (effective after the next XNAT restart).';
END $$;
DXSQL
)
echo "$dx_sql_out"
if echo "$dx_sql_out" | grep -q 'Registered xnat:dxSessionData'; then
  # Sentinel consumed by the Makefile xnat-configure target to force an XNAT reload.
  echo "DX_ELEMENT_SECURITY_REGISTERED"
fi

# Register PACS
echo "Registering PACS..."
curl -s -X POST "$XNAT_URL/xapi/pacs" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"aeTitle\": \"${ORTHANC_AETITLE}\",
    \"defaultQueryRetrievePacs\": true,
    \"defaultStoragePacs\": true,
    \"host\": \"${ORTHANC_HOST}\",
    \"label\": \"Test PACS instance\",
    \"ormStrategySpringBeanId\": \"dicomOrmStrategy\",
    \"queryRetrievePort\": ${PACS_DICOM_PORT},
    \"queryable\": true,
    \"storable\": true,
    \"supportsExtendedNegotiations\": true
  }"

# Configure PACS availability schedule (all days)
# Not sure if the below is working or is just returning the default configuration
for DAY in MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY SATURDAY SUNDAY; do
  echo "Setting PACS availability for $DAY..."
  curl -s -X POST "$XNAT_URL/xapi/pacs/1/availability" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "{
      \"availabilityEnd\": \"23:59\",
      \"availabilityStart\": \"00:00\",
      \"availableNow\": true,
      \"dayOfWeek\": \"$DAY\",
      \"enabled\": true,
      \"pacsId\": 1,
      \"threads\": 1,
      \"utilizationPercent\": 100
    }"
done

echo "XNAT configuration complete!"
