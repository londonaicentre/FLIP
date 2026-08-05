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

# FLIP-PT-091: HTTP basic auth is always enforced. Users come from
# ORTHANC__REGISTERED_USERS, injected at runtime — compose builds it from the
# kit file's ORTHANC_USERNAME/ORTHANC_PASSWORD; k8s reads the
# orthanc-registered-users secret key. flip-entrypoint.sh refuses to start
# without a usable user map, so the image cannot boot unauthenticated (Orthanc
# would otherwise auto-register the well-known orthanc/orthanc user). The
# entrypoint also re-asserts the flag below at runtime, so an override that
# turned auth off would refuse to start rather than silently take effect.
# DICOM-Web stays disabled: nothing in FLIP issues QIDO/WADO/STOW — XNAT DQR
# pulls over plain DICOM (port 4242). Do NOT set
# ORTHANC__REMOTE_ACCESS_ALLOWED=false: it limits the HTTP server to loopback
# clients, which would break the published UI port and the XNAT nginx
# /orthanc/ proxy.
ENV ORTHANC__AUTHENTICATION_ENABLED=true

COPY --chmod=0755 flip-entrypoint.sh /flip-entrypoint.sh

# Run as the non-root orthanc user (UID 999) shipped by the base image.
# Every pre-start write the entrypoint performs is 999-safe: /etc/hostid is
# owned by orthanc (so the random hostid is written in place), the generated
# config is written to /tmp/orthanc.json, and /var/lib/orthanc is orthanc-owned.
# The only path that must be made 999-writable is the storage bind mount, which
# is handled at the provisioning layer (update_orthanc_data.sh in dev, the
# Ansible playbooks in prod). Running non-root from PID 1 means no capabilities
# survive into the process (no-new-privileges), so cap_add is minimal.
USER orthanc

# Setting ENTRYPOINT resets the CMD inherited from the base image — restate it
# (base 25.12.2: ENTRYPOINT ["/docker-entrypoint.sh"], CMD ["/tmp/orthanc.json"]).
ENTRYPOINT ["/flip-entrypoint.sh"]
CMD ["/tmp/orthanc.json"]
