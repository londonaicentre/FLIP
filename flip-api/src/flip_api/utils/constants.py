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

from flip_api.domain.schemas.types import FLBackend

SERVICE_UNAVAILABLE_MESSAGE = "The server is unable to process any requests at the moment, please try again later."

# AWS SES email templates
ACCESS_REQUEST_TEMPLATE_NAME = "flip-access-request"
IMAGING_CREDENTIALS_TEMPLATE_NAME = "flip-xnat-credentials"
IMAGING_PROJECT_ACCESS_TEMPLATE_NAME = "flip-xnat-added-to-project"


# File containing job types and their required files. The manifest is per-backend:
# each FL backend (nvflare/flower) has its own copy pulled from S3 at runtime (never
# committed to source control), so the two frameworks never clobber each other's file.
def job_types_required_files_name(fl_backend: FLBackend) -> str:
    """Local filename for the per-backend job-types/required-files manifest.

    Args:
        fl_backend (FLBackend): The FL backend the manifest belongs to (``nvflare`` or ``flower``).

    Returns:
        str: The manifest filename, e.g. ``job_types_and_required_files.nvflare.json``.
    """
    return f"job_types_and_required_files.{fl_backend}.json"


# Testing constants
BASE_URL = "http://localhost:8080/api"

# Main user emails - these should match the users created in Cognito and seeded in DB
ADMIN_EMAIL_1 = "aicentreflip@gmail.com"
ADMIN_EMAIL_2 = "alexandre.triay_bagur@kcl.ac.uk"
ADMIN_EMAIL_3 = "rafael.dias@kcl.ac.uk"
RESEARCHER_EMAIL = "rafaelagd@gmail.com"
OBSERVER_EMAIL = "triayalex@gmail.com"
