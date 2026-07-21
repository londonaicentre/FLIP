# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

FROM orthancteam/orthanc:25.12.2

ENV ORTHANC__DICOM_WEB='{"Enable":true,"Root":"/dicom-web/","EnableWado":true,"WadoRoot":"/wado","Ssl":false,"QidoCaseSensitive":true,"Host":"localhost","StudiesMetadata":"Full","SeriesMetadata":"Full"}'

# Run as the non-root orthanc user (UID 999) shipped by the base image.
# Every pre-start write the entrypoint performs is 999-safe: /etc/hostid is
# owned by orthanc (so the random hostid is written in place), the generated
# config is written to /tmp/orthanc.json, and /var/lib/orthanc is orthanc-owned.
# The only path that must be made 999-writable is the storage bind mount, which
# is handled at the provisioning layer (update_orthanc_data.sh in dev, the
# Ansible playbooks in prod). Running non-root from PID 1 means no capabilities
# survive into the process (no-new-privileges), so cap_add is minimal.
USER orthanc
