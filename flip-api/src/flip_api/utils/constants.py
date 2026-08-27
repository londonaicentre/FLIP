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

import os

SERVICE_UNAVAILABLE_MESSAGE = "The server is unable to process any requests at the moment, please try again later."

# Default label naming the x-axis of an FL training-metric plot. Used when a metric is logged without
# an explicit x-axis label — the historical behaviour, where the x-axis is the FL global round. Single
# source of truth for the FLMetrics column default, the ingest-schema default, and the plot's x-axis
# title (FLIP#148).
DEFAULT_X_AXIS_LABEL = "Global Rounds"

# AWS SES email templates
ACCESS_REQUEST_TEMPLATE_NAME = "flip-access-request"
IMAGING_CREDENTIALS_TEMPLATE_NAME = "flip-xnat-credentials"
IMAGING_PROJECT_ACCESS_TEMPLATE_NAME = "flip-xnat-added-to-project"


# Filename of the per-backend job-types/required-files manifest. It lives in each backend's
# folder on the local base-application tree (e.g. ``<FL_APP_BASE_DIR>/nvflare/required_files.json``),
# generated from the per-template arrays by ``fl-apps/check_required_files.sh``, committed to the
# repo, and baked into the flip-api image. flip-api reads it directly from disk (FLIP#724) — the
# two backends live in separate folders so they never clobber each other's manifest.
REQUIRED_FILES_MANIFEST_NAME = "required_files.json"


# Testing constants. BASE_URL defaults to the local dev stack; override with
# FLIP_E2E_BASE_URL to run the e2e smoke against a remote hub (stag/prod).
BASE_URL = os.environ.get("FLIP_E2E_BASE_URL", "http://localhost:8080/api")

# Main user emails - these should match the users created in Cognito and seeded in DB
ADMIN_EMAIL_1 = "aicentreflip@gmail.com"
ADMIN_EMAIL_2 = "alexandre.triay_bagur@kcl.ac.uk"
ADMIN_EMAIL_3 = "rafael.dias@kcl.ac.uk"
RESEARCHER_EMAIL = "rafaelagd@gmail.com"
VIEWER_EMAIL = "triayalex@gmail.com"

# Demo users for the scripted demo-video recorder (tests/demo_video.py). Created
# in Cognito by flip_api/scripts/create_demo_users.py (passwords come from
# DEMO_*_PASSWORD env vars, never committed); role grants are seeded at boot by
# db/seed/main_users.py iff the users exist in the pool. Emails are env-
# overridable so operators can point the demo at their own accounts.
DEMO_RESEARCHER_EMAIL = os.environ.get("DEMO_RESEARCHER_EMAIL", "flip-demo-researcher@example.com")
DEMO_ADMIN_EMAIL = os.environ.get("DEMO_ADMIN_EMAIL", "flip-demo-admin@example.com")
